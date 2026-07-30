"""Survey new v2 sources via web search. Costs ~US$ 0.15-0.30 (3 domains).

Run: uv run python spikes/source_survey_v2.py

Measures per domain: stop_reason, web_searches, citations, whether text
mentions R$, and a preview of the response. Results feed docs/fontes.md.
"""

from typing import Any

import anthropic

from renov_market_scan.collect.anthropic_search import build_web_search_tool
from renov_market_scan.config import Settings

QUERY = "apple iphone 13 128gb seminovo recondicionado r$"
DOMAINS = [
    "trocafy.com.br",
    "redecellstore.com.br",
    "celularseminovo.redecellstore.com.br",
    "cellularstore.com.br",
]

SEARCH_PROMPT = (
    "Busque anuncios de celular usado ou seminovo usando esta frase de busca: "
    f'"{QUERY}". Considere apenas resultados do dominio {{domain}}. '
    "Descreva cada anuncio individual encontrado, incluindo titulo, preco pedido, "
    "condicao e link."
)


def count_citations(content: list[Any]) -> int:
    total = 0
    for block in content:
        if getattr(block, "type", None) == "text" and getattr(block, "citations", None):
            total += len(block.citations)
    return total


def response_text(content: list[Any]) -> str:
    return "\n".join(
        block.text for block in content if getattr(block, "type", None) == "text"
    )


def survey_domain(settings: Settings, client: anthropic.Anthropic, domain: str) -> dict[str, Any]:
    tool = build_web_search_tool(settings, domain)
    prompt = SEARCH_PROMPT.format(domain=domain)
    response = client.messages.create(
        model=settings.model,
        max_tokens=4000,
        tools=[tool],
        messages=[{"role": "user", "content": prompt}],
    )
    content = list(response.content)
    text = response_text(content)
    usage = response.usage
    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = getattr(server_tool_use, "web_search_requests", 0) or 0
    return {
        "domain": domain,
        "stop_reason": response.stop_reason,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "web_searches": searches,
        "citations": count_citations(content),
        "has_brl_price": "R$" in text or "r$" in text.lower(),
        "text_preview": text[:500],
    }


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    for domain in DOMAINS:
        result = survey_domain(settings, client, domain)
        print(f"--- {result['domain']}")
        print(f"    stop_reason   = {result['stop_reason']}")
        print(f"    web_searches  = {result['web_searches']}")
        print(f"    citations     = {result['citations']}")
        print(f"    has_brl_price = {result['has_brl_price']}")
        print(f"    tokens        = {result['input_tokens']} in / {result['output_tokens']} out")
        print(f"    preview       = {result['text_preview']!r}")


if __name__ == "__main__":
    main()
