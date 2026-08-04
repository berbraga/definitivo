"""Production adapter: drives the Claude Code CLI (`claude -p`) instead of the
Messages API. Authenticated by the CLI's own session (browser login or
CLAUDE_CODE_OAUTH_TOKEN), never by an API key.

cited_text produced by this adapter is the model's own self-report of what it
read — not an API-verified citation. The Messages API's citation mechanism
(cryptographically tied to search results) has no equivalent exposed through
`claude -p`'s WebSearch tool, so this is a known, accepted reduction in
evidence strength relative to AnthropicSearchAdapter.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from renov_market_scan.collect.base import SearchOutcome
from renov_market_scan.config import Settings
from renov_market_scan.models import Condition, Listing, Query

PLAN_LIMIT_PATTERNS: tuple[str, ...] = (
    "usage limit",
    "rate limit",
    "session limit",
    "try again",
)


def child_env() -> dict[str, str]:
    """Subprocess environment: CLI session only, API key vars stripped."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def preflight() -> None:
    """Fail loudly before any subprocess is spawned for real work."""
    if shutil.which("claude") is None:
        raise RuntimeError(
            "'claude' nao encontrado no PATH. Instale o Claude Code CLI."
        )
    probe = subprocess.run(
        ["claude", "auth", "status"],
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "Claude CLI nao autenticado.\n"
            "  Maquina com navegador: rode 'claude' e faca /login.\n"
            "  Servidor sem navegador: gere o token numa maquina com navegador\n"
            "  com 'claude setup-token' e exporte CLAUDE_CODE_OAUTH_TOKEN."
        )


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON answer, tolerating a markdown fence around it.

    The model is instructed to answer with bare JSON but sometimes wraps it
    in ```json ... ``` anyway.
    """
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"resposta sem JSON reconhecivel: {stripped[:300]}")
    return dict(json.loads(stripped[start : end + 1]))


STATUS_OK = "ok"
STATUS_PARSE_ERROR = "parse_error"
STATUS_SUBPROCESS_ERROR = "erro_subprocess"
STATUS_PLAN_LIMIT = "limite_de_plano"

SUBPROCESS_TIMEOUT_S = 900

VALID_CONDITIONS: frozenset[str] = frozenset({"novo", "seminovo", "usado", "desconhecido"})

SEARCH_PROMPT_TEMPLATE = (
    "Voce e um coletor de referencia de precos de celulares seminovos no "
    "Brasil.\n\n"
    "Use WebSearch para encontrar anuncios de aparelhos USADOS ou SEMINOVOS "
    "a venda no dominio {domain}, usando estas frases de busca: {phrases}.\n\n"
    "Extraia o preco APENAS do snippet/resumo retornado pela busca. Nao "
    "abra paginas. Se o snippet nao mostrar preco claro, descarte o "
    "anuncio.\n\n"
    "REGRAS DE ACEITE DE ANUNCIO (aplicar antes de incluir):\n"
    "1. Descarte acessorios e pecas: capa, capinha, case, pelicula, vidro, "
    "tela, display, touch, bateria, placa, flex, conector, carcaca, aro, "
    "tampa, camera, alto-falante, botao, carregador, fone, cabo, chip, "
    'suporte, "para retirada", "nao liga", replica, clone, similar.\n'
    "2. Preco A VISTA. Rejeite valor precedido de \"12x\", \"10 x\", \"sem "
    "juros\". Converta \"R$ 1.234,56\" para 1234.56 (numero, ponto decimal).\n"
    "3. Descarte \"novo\"/\"lacrado\". Aceite: seminovo, usado, vitrine, "
    "recondicionado.\n\n"
    "RESPONDA APENAS COM JSON, sem markdown, sem code fence, sem preambulo:\n"
    '{{"anuncios":[{{"titulo":"...","preco_brl":1234.56,"condicao":"usado",'
    '"url":"https://...","fonte":"...",'
    '"cited_text":"trecho verbatim do texto que evidencia o preco"}}]}}\n\n'
    "Se nao achar nada valido, devolva \"anuncios\": []. Nao calcule "
    "mediana, minimo ou maximo. Nao faca perguntas."
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
    anuncios: list[ExtractedListing] = []


def _normalize_condition(value: str) -> Condition:
    lowered = value.strip().lower()
    if lowered in VALID_CONDITIONS:
        return lowered  # type: ignore[return-value]
    return "desconhecido"


def collection_error_hint(payload: dict[str, Any]) -> str | None:
    """Human-readable failure reason from a CLI subprocess payload, if any."""
    stdout_tail = payload.get("stdout_tail")
    if isinstance(stdout_tail, str) and stdout_tail.strip():
        try:
            envelope = json.loads(stdout_tail)
            result = envelope.get("result")
            if result:
                return str(result)
        except json.JSONDecodeError:
            pass
    stderr_tail = payload.get("stderr_tail")
    if isinstance(stderr_tail, str) and stderr_tail.strip():
        return stderr_tail.strip()[:300]
    return None


def _classify_failure(stdout: str, stderr: str) -> str:
    combined = (stdout + stderr).lower()
    if any(pattern in combined for pattern in PLAN_LIMIT_PATTERNS):
        return STATUS_PLAN_LIMIT
    return STATUS_SUBPROCESS_ERROR


class ClaudeCliAdapter:
    """Search and extract by driving `claude -p` as a subprocess.

    Serialized by an internal asyncio.Lock, regardless of how many concurrent
    search() calls the caller fires. claude -p draws from the plan's shared
    5h/weekly usage window, not a per-token rate limit, so concurrent CLI
    processes risk exhausting that window faster and interleaving sessions
    unpredictably. run.py fires every (item, source) pair concurrently via
    asyncio.gather and makes no serialization guarantee of its own; this lock
    is what actually keeps at most one `claude -p` subprocess running at a
    time.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()  # serializes across concurrent search() calls
        preflight()

    async def search(self, queries: list[Query]) -> SearchOutcome:
        if not queries:
            return SearchOutcome(listings=[], status=STATUS_OK, payload={})
        async with self._lock:
            return await asyncio.to_thread(self._search_blocking, queries)

    def _search_blocking(self, queries: list[Query]) -> SearchOutcome:
        first = queries[0]
        phrases = "; ".join(f'"{query.text}"' for query in queries)
        prompt = SEARCH_PROMPT_TEMPLATE.format(domain=first.domain, phrases=phrases)

        # No --max-turns: the installed CLI (2.1.220) does not expose that
        # flag. See docs/superpowers/sdd/2026-07-30-claude-cli-adapter/
        # cli-flags-findings.md for the confirmed `claude --help` output.
        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--allowedTools", "WebSearch",
            "--model", self._settings.model,
        ]

        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                env=child_env(),
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            payload: dict[str, Any] = {
                "returncode": None,
                "stdout_tail": (exc.stdout or "")[-2000:],
                "stderr_tail": (exc.stderr or "")[-2000:],
                "timeout": True,
            }
            return SearchOutcome(
                listings=[], status=STATUS_SUBPROCESS_ERROR, payload=payload
            )

        payload = {
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

        if proc.returncode != 0:
            status = _classify_failure(proc.stdout, proc.stderr)
            return SearchOutcome(listings=[], status=status, payload=payload)

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

        if envelope.get("is_error"):
            status = _classify_failure(str(envelope), "")
            return SearchOutcome(listings=[], status=status, payload=payload)

        payload["session_id"] = envelope.get("session_id")
        payload["num_turns"] = envelope.get("num_turns")

        try:
            parsed = extract_json(envelope.get("result", ""))
            extraction = ExtractionPayload.model_validate(parsed)
        except (ValueError, ValidationError):
            return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

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
                cited_text=item.cited_text,
            )
            for item in extraction.anuncios
        ]
        return SearchOutcome(listings=listings, status=STATUS_OK, payload=payload)
