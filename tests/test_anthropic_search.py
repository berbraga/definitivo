import asyncio
from types import SimpleNamespace
from typing import Any

from renov_market_scan.collect.anthropic_search import (
    AnthropicSearchAdapter,
    build_web_search_tool,
    collect_evidence,
    find_tool_error,
)
from renov_market_scan.config import Settings
from renov_market_scan.models import Query

QUERY = Query(
    search_key="k1",
    source="olx",
    domain="olx.com.br",
    phrase_index=0,
    text="apple iphone 13 128gb usado seminovo",
)

VALID_JSON = (
    '{"anuncios": [{"titulo": "iPhone 13 128GB seminovo R$ 3.050,00", '
    '"preco_brl": 3050.0, "condicao": "seminovo", '
    '"url": "https://olx.com.br/a-1", "fonte": "olx"}]}'
)


def text_block(text: str, citations: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=citations or [])


def citation(cited_text: str, title: str = "OLX", url: str = "https://olx.com.br/a-1"):
    return SimpleNamespace(cited_text=cited_text, title=title, url=url)


def search_result_block(error_code: str | None = None) -> SimpleNamespace:
    if error_code is None:
        return SimpleNamespace(
            type="web_search_tool_result",
            content=[
                SimpleNamespace(
                    type="web_search_result",
                    url="https://olx.com.br/a-1",
                    title="OLX",
                    page_age=None,
                )
            ],
        )
    return SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(type="web_search_tool_result_error", error_code=error_code),
    )


def response(content: list[Any], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-sonnet-5",
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            server_tool_use=SimpleNamespace(web_search_requests=2),
        ),
    )


class FakeMessages:
    """Stands in for client.messages. Returns queued responses in order."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of queued responses")
        queued = self._responses.pop(0)
        if isinstance(queued, Exception):
            raise queued
        return queued


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"anthropic_api_key": "sk-test"}
    base.update(overrides)
    return Settings(**base)


def test_tool_definition_carries_domain_location_and_direct_caller():
    tool = build_web_search_tool(make_settings(), "olx.com.br")
    assert tool["type"] == "web_search_20260209"
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == 3
    assert tool["allowed_domains"] == ["olx.com.br"]
    assert tool["user_location"]["country"] == "BR"
    assert tool["allowed_callers"] == ["direct"]
    assert "blocked_domains" not in tool


def test_basic_tool_version_omits_allowed_callers():
    settings = make_settings(
        model="claude-haiku-4-5",
        web_search_tool_version="web_search_20250305",
    )
    tool = build_web_search_tool(settings, "olx.com.br")
    assert "allowed_callers" not in tool


def test_evidence_is_collected_from_citations_and_titles():
    content = [text_block("resposta", [citation("R$ 3.050,00 iPhone 13", title="Anuncio OLX")])]
    evidence = collect_evidence(content)
    assert any("3.050,00" in item for item in evidence)
    assert any("Anuncio OLX" in item for item in evidence)


def test_evidence_is_empty_when_there_are_no_citations():
    assert collect_evidence([text_block("sem citacoes")]) == []


def test_tool_error_is_detected():
    assert find_tool_error([search_result_block("too_many_requests")]) == "too_many_requests"


def test_no_tool_error_on_a_successful_result():
    assert find_tool_error([search_result_block()]) is None


VALID_JSON_WITH_EVIDENCE = (
    '{"anuncios": [{"titulo": "iPhone 13 128GB seminovo R$ 3.050,00", '
    '"preco_brl": 3050.0, "condicao": "seminovo", '
    '"url": "https://olx.com.br/a-1", "fonte": "olx", '
    '"cited_text": "R$ 3.050,00 iPhone 13 seminovo"}]}'
)


def test_a_successful_search_yields_listings_with_evidence_attached():
    """Two calls: stage 1 searches in free text (with a real citation), stage 2
    extracts structured JSON with cited_text from that text — no new search."""
    client = FakeClient(
        [
            response(
                [
                    search_result_block(),
                    text_block("iPhone 13 seminovo R$ 3.050,00", [citation("R$ 3.050,00")]),
                ]
            ),
            response([text_block(VALID_JSON_WITH_EVIDENCE)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1
    listing = outcome.listings[0]
    assert listing.price_brl == 3050.0
    assert listing.search_key == "k1"
    assert listing.source == "olx"
    assert "3.050,00" in listing.cited_text
    assert len(client.messages.calls) == 2
    assert "tools" not in client.messages.calls[1]


def test_the_raw_payload_is_json_serializable():
    import json

    client = FakeClient(
        [
            response([text_block("busca livre")]),
            response([text_block(VALID_JSON_WITH_EVIDENCE)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert json.dumps(outcome.payload)
    assert outcome.payload["stop_reason"] == "end_turn"
    assert outcome.payload["web_search_requests"] == 2


def test_invalid_json_is_retried_once_then_marked_parse_error():
    """The retry happens within stage 2, on top of a completed stage 1."""
    client = FakeClient(
        [
            response([text_block("busca livre")]),
            response([text_block("isto nao e json")]),
            response([text_block("ainda nao e json")]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "parse_error"
    assert outcome.listings == []
    assert len(client.messages.calls) == 3


def test_invalid_json_recovered_by_the_retry_is_accepted():
    client = FakeClient(
        [
            response([text_block("busca livre")]),
            response([text_block("isto nao e json")]),
            response([text_block(VALID_JSON_WITH_EVIDENCE)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1


def test_a_markdown_fence_around_the_json_is_stripped():
    """The spike showed the model sometimes wraps the answer in ```json anyway."""
    fenced = "```json\n" + VALID_JSON_WITH_EVIDENCE + "\n```"
    client = FakeClient(
        [
            response([text_block("busca livre")]),
            response([text_block(fenced)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1


def test_never_uses_eval_or_regex_on_malformed_json():
    """A payload that eval would happily execute must not be executed."""
    client = FakeClient(
        [
            response([text_block("busca livre")]),
            response([text_block("__import__('os').system('echo boom')")]),
            response([text_block("__import__('os').system('echo boom')")]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "parse_error"


def test_max_uses_exceeded_accepts_the_partial_result():
    client = FakeClient(
        [
            response([search_result_block("max_uses_exceeded"), text_block("resultado parcial")]),
            response([text_block(VALID_JSON_WITH_EVIDENCE)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "busca_truncada"
    assert len(outcome.listings) == 1


def test_invalid_tool_input_fails_without_retry():
    """A stage-1 tool error stops the pipeline before stage 2 ever runs."""
    client = FakeClient([response([search_result_block("invalid_tool_input")])])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "erro_query"
    assert outcome.listings == []
    assert len(client.messages.calls) == 1


def test_pause_turn_is_resumed_by_resending_the_assistant_message():
    """pause_turn belongs to stage 1; the resume is stage 1's own retry, not stage 2."""
    client = FakeClient(
        [
            response([text_block("parcial")], stop_reason="pause_turn"),
            response([text_block("busca completa")]),
            response([text_block(VALID_JSON_WITH_EVIDENCE)]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(client.messages.calls) == 3
    resumed = client.messages.calls[1]["messages"]
    assert resumed[-1]["role"] == "assistant"


def test_pause_turn_gives_up_after_the_resume_cap():
    paused = [response([text_block("parcial")], stop_reason="pause_turn") for _ in range(5)]
    client = FakeClient(paused)
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "pausa_excedida"


def test_refusal_is_recorded_without_retry():
    client = FakeClient([response([], stop_reason="refusal")])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "recusado"
    assert len(client.messages.calls) == 1


def test_an_empty_result_list_is_ok_not_an_error():
    client = FakeClient(
        [
            response([text_block("busca sem resultados")]),
            response([text_block('{"anuncios": []}')]),
        ]
    )
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert outcome.listings == []


def test_client_is_constructed_with_retries_disabled(monkeypatch):
    """tenacity owns retry policy; stacking the SDK's own retries multiplies it."""
    captured: dict[str, Any] = {}

    class SpyAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.messages = FakeMessages([])

    monkeypatch.setattr(
        "renov_market_scan.collect.anthropic_search.anthropic.Anthropic", SpyAnthropic
    )
    AnthropicSearchAdapter(make_settings())
    assert captured["max_retries"] == 0
