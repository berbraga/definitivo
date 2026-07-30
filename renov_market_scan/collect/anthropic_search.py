"""Production adapter: two messages.create calls per (search key, source).

Three facts about the API shape this module. First, search errors arrive
inside a successful HTTP 200 response as a web_search_tool_result_error
block, so retry policy is driven by that block and not by an exception.
Second, the client never receives search-result text: only URLs, titles,
encrypted content, and up to 150 verbatim characters per citation. Third,
forcing JSON-only output suppresses citations entirely, since citations only
attach to free-text blocks (measured in the Task 15 spike) — so extraction
cannot happen in the same call as the search. Stage one searches freely and
keeps its citations; stage two, with no search tool available, turns that
free text into structured JSON with a verbatim cited_text per item, which the
evidence rule verifies downstream.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from renov_market_scan.collect.base import SearchOutcome
from renov_market_scan.collect.errors import ErrorAction, classify_tool_error, status_for_error
from renov_market_scan.config import Settings
from renov_market_scan.models import Condition, Listing, Query

MAX_PAUSE_RESUMES = 3
MAX_OUTPUT_TOKENS = 8000

STATUS_OK = "ok"
STATUS_PARSE_ERROR = "parse_error"
STATUS_REFUSED = "recusado"
STATUS_PAUSE_EXCEEDED = "pausa_excedida"

VALID_CONDITIONS: frozenset[str] = frozenset({"novo", "seminovo", "usado", "desconhecido"})

SEARCH_PROMPT_TEMPLATE = (
    "Busque anuncios de celular usado ou seminovo usando estas frases de "
    "busca, nesta ordem: {phrases}. "
    "Considere apenas resultados do dominio {domain}. "
    "Descreva cada anuncio individual encontrado, sem repetir URLs, "
    "incluindo titulo, preco pedido, condicao e link."
)

EXTRACTION_SYSTEM_PROMPT = (
    "A partir do texto de busca abaixo, extraia um array JSON de objetos "
    'no formato {"anuncios": [{"titulo","preco_brl","condicao","url","fonte","cited_text"}]}. '
    "Preencha cited_text com o trecho exato (verbatim) do texto que evidencia o preco. "
    "Use o titulo do anuncio exatamente como aparece no texto. "
    "Nunca invente preco: se o preco nao aparecer no texto, use null. "
    "Nunca use valor de parcela como preco. "
    "Sem preambulo, sem markdown, sem texto fora do JSON."
)

CORRECTION_PROMPT = (
    "Sua resposta anterior nao era JSON valido. Responda novamente apenas com o "
    'objeto JSON {"anuncios": [...]}, sem nenhum texto fora dele.'
)


class ExtractedListing(BaseModel):
    """One advert as reported by the model, before any filtering."""

    titulo: str
    preco_brl: float | None = None
    condicao: str = "desconhecido"
    url: str
    fonte: str = ""
    cited_text: str = ""


class ExtractionPayload(BaseModel):
    """The model's whole answer."""

    anuncios: list[ExtractedListing] = []


def _strip_markdown_fence(text: str) -> str:
    """Drop a ```json ... ``` wrapper the model sometimes adds despite instructions."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


def _search_text_and_evidence(content: list[Any]) -> tuple[str, list[str]]:
    """The search stage's free text plus every citation attached to it.

    Citations only attach to free-text blocks, so this must run against the
    search-stage response, before any JSON-only extraction step.
    """
    parts: list[str] = []
    evidence: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        parts.append(block.text)
        for cite in getattr(block, "citations", None) or []:
            cited_text = getattr(cite, "cited_text", None)
            if cited_text:
                evidence.append(str(cited_text))
    return "\n".join(parts), evidence


class TransientAPIError(Exception):
    """Raised so tenacity retries a rate limit or an overload."""


def build_web_search_tool(settings: Settings, domain: str) -> dict[str, Any]:
    """Tool definition for one domain.

    allowed_domains and blocked_domains are mutually exclusive, so only the
    allow list is ever sent. Versions from 20260209 onward default to running
    inside code execution, so direct calling is requested explicitly.
    """
    tool: dict[str, Any] = {
        "type": settings.web_search_tool_version,
        "name": "web_search",
        "max_uses": settings.max_uses_per_call,
        "allowed_domains": [domain],
        "user_location": {
            "type": "approximate",
            "country": settings.user_location_country,
        },
    }
    if settings.web_search_tool_version != "web_search_20250305":
        tool["allowed_callers"] = ["direct"]
    return tool


def collect_evidence(content: list[Any]) -> list[str]:
    """Every verbatim cited_text and cited title in the response."""
    evidence: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        for cite in getattr(block, "citations", None) or []:
            cited_text = getattr(cite, "cited_text", None)
            if cited_text:
                evidence.append(str(cited_text))
            title = getattr(cite, "title", None)
            if title:
                evidence.append(str(title))
    return evidence


def find_tool_error(content: list[Any]) -> str | None:
    """The first web_search_tool_result_error code, if any.

    On success the block's content is a list; on error it is a single object.
    """
    for block in content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        inner = getattr(block, "content", None)
        if isinstance(inner, list):
            continue
        code = getattr(inner, "error_code", None)
        if code:
            return str(code)
    return None


def _response_text(content: list[Any]) -> str:
    """Concatenate the text blocks, which is where the JSON lives."""
    return "".join(
        block.text for block in content if getattr(block, "type", None) == "text"
    )


def _to_jsonable(response: Any) -> dict[str, Any]:
    """Best-effort structured copy of the response for the raw cache."""
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:  # noqa: BLE001 - fall back to the summary below
            pass
    usage = getattr(response, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None)
    return {
        "stop_reason": getattr(response, "stop_reason", None),
        "model": getattr(response, "model", None),
        "text": _response_text(list(getattr(response, "content", []))),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "web_search_requests": getattr(server_tool_use, "web_search_requests", None),
    }


def _normalize_condition(value: str) -> Condition:
    lowered = value.strip().lower()
    if lowered in VALID_CONDITIONS:
        return lowered  # type: ignore[return-value]
    return "desconhecido"


class AnthropicSearchAdapter:
    """Search and extract through the Anthropic API's web search tool."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            # tenacity owns retry policy. Leaving the SDK's default of 2 would
            # multiply attempts (3 x 3) and make the backoff meaningless.
            max_retries=0,
        )
        self._semaphore = asyncio.Semaphore(settings.concurrency)

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Run one call for a (model, source) pair. Returns a status, never
        raises for a data problem."""
        if not queries:
            return SearchOutcome(listings=[], status=STATUS_OK, payload={})
        async with self._semaphore:
            return await asyncio.to_thread(self._search_blocking, queries)

    @retry(
        retry=retry_if_exception_type(TransientAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=False,
    )
    def _call(
        self,
        messages: list[dict[str, Any]],
        tool: dict[str, Any] | None,
        system: str | None = None,
    ) -> Any:
        """One API call, retried by tenacity on transient failures.

        tool=None omits `tools` entirely, so the extraction stage cannot spend
        a web_search_request even if the model wanted to.
        """
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tool is not None:
            kwargs["tools"] = [tool]
        try:
            return self._client.messages.create(**kwargs)
        except anthropic.RateLimitError as error:
            raise TransientAPIError(str(error)) from error
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise TransientAPIError(str(error)) from error
            raise
        except anthropic.APIConnectionError as error:
            raise TransientAPIError(str(error)) from error

    def _search_blocking(self, queries: list[Query]) -> SearchOutcome:
        first = queries[0]
        tool = build_web_search_tool(self._settings, first.domain)
        phrases = "; ".join(f'"{query.text}"' for query in queries)
        search_prompt = SEARCH_PROMPT_TEMPLATE.format(phrases=phrases, domain=first.domain)
        messages: list[dict[str, Any]] = [{"role": "user", "content": search_prompt}]

        response = self._call(messages, tool=tool)
        payload = _to_jsonable(response)

        resumes = 0
        while getattr(response, "stop_reason", None) == "pause_turn":
            if resumes >= MAX_PAUSE_RESUMES:
                return SearchOutcome(listings=[], status=STATUS_PAUSE_EXCEEDED, payload=payload)
            resumes += 1
            messages = [
                {"role": "user", "content": search_prompt},
                {"role": "assistant", "content": response.content},
            ]
            response = self._call(messages, tool=tool)
            payload = _to_jsonable(response)

        if getattr(response, "stop_reason", None) == "refusal":
            return SearchOutcome(listings=[], status=STATUS_REFUSED, payload=payload)

        content = list(getattr(response, "content", []))
        status = STATUS_OK

        error_code = find_tool_error(content)
        if error_code is not None:
            action = classify_tool_error(error_code)
            status = status_for_error(error_code)
            if action is not ErrorAction.ACCEPT_PARTIAL:
                return SearchOutcome(listings=[], status=status, payload=payload)

        search_text, evidence = _search_text_and_evidence(content)

        extracted = self._extract(search_text)
        if extracted is None:
            return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

        joined_evidence = " | ".join(evidence)
        captured_at = datetime.now(UTC).isoformat()

        listings = [
            Listing(
                search_key=first.search_key,
                source=first.source,
                title=item.titulo,
                price_brl=item.preco_brl,
                condition=_normalize_condition(item.condicao),
                url=item.url,
                captured_at=captured_at,
                cited_text=item.cited_text or joined_evidence,
            )
            for item in extracted.anuncios
        ]
        return SearchOutcome(listings=listings, status=status, payload=payload)

    def _extract(self, search_text: str) -> ExtractionPayload | None:
        """Stage 2: turn stage 1's free text into structured JSON.

        No tool is passed here — this call never searches, it only reads the
        text stage 1 already produced. One correction attempt on invalid
        JSON, then give up. Never eval, never regex on the result.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": search_text}]
        response = self._call(messages, tool=None, system=EXTRACTION_SYSTEM_PROMPT)
        parsed = self._parse(list(getattr(response, "content", [])))
        if parsed is not None:
            return parsed

        messages = [
            {"role": "user", "content": search_text},
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": CORRECTION_PROMPT},
        ]
        response = self._call(messages, tool=None, system=EXTRACTION_SYSTEM_PROMPT)
        return self._parse(list(getattr(response, "content", [])))

    def _parse(self, content: list[Any]) -> ExtractionPayload | None:
        """Validate the model's JSON. Returns None when it cannot be trusted."""
        text = _strip_markdown_fence(_response_text(content))
        if not text:
            return None
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        try:
            return ExtractionPayload.model_validate(loaded)
        except ValidationError:
            return None
