"""Decide how to constrain the model's output. Costs about US$ 0.10-0.25.

Three mechanisms are tried against one real query:

A. output_config.format with a json_schema. Strongest guarantee, but the docs
   say structured outputs is incompatible with citations, and web search always
   enables citations. May return 400.
B. JSON by prompt, validated with pydantic. Certain to work, weaker guarantee.
C. A client tool with strict=True. Schema guaranteed by a different mechanism,
   so it should coexist with citations, at the cost of an extra turn.

All three, as run here, return citations = 0: forcing the SYSTEM prompt to
produce JSON-only output with no free text suppresses citations entirely,
since citations only attach to free-text blocks. See docs/fontes.md for the
measured proof and the two-stage mechanism chosen instead (free-text search
call, then a separate no-search call that extracts structured JSON with
verbatim cited_text from the first call's output).

Run: uv run python spikes/extraction_contract.py
"""

import json
from typing import Any

import anthropic

from renov_market_scan.config import Settings

QUERY = "iphone 13 128gb seminovo estado de conservacao r$"
DOMAIN = "olx.com.br"

SYSTEM = (
    "Voce extrai anuncios de celulares usados. Busque anuncios do modelo pedido "
    "e responda apenas com um array JSON de objetos "
    '{"titulo","preco_brl","condicao","url","fonte"}. '
    "Sem preambulo, sem markdown, sem texto fora do JSON."
)

LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "anuncios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "preco_brl": {"type": "number"},
                    "condicao": {"type": "string"},
                    "url": {"type": "string"},
                    "fonte": {"type": "string"},
                },
                "required": ["titulo", "preco_brl", "condicao", "url", "fonte"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["anuncios"],
    "additionalProperties": False,
}


def web_search_tool(settings: Settings) -> dict[str, Any]:
    """The tool definition, with direct calling forced on modern versions."""
    tool: dict[str, Any] = {
        "type": settings.web_search_tool_version,
        "name": "web_search",
        "max_uses": settings.max_uses_per_call,
        "allowed_domains": [DOMAIN],
        "user_location": {"type": "approximate", "country": settings.user_location_country},
    }
    if settings.web_search_tool_version != "web_search_20250305":
        tool["allowed_callers"] = ["direct"]
    return tool


def report(label: str, response: Any) -> None:
    """Print what we need to decide: stop reason, searches, tokens, text."""
    usage = response.usage
    searches = 0
    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is not None:
        searches = getattr(server_tool_use, "web_search_requests", 0) or 0
    texts = [block.text for block in response.content if block.type == "text"]
    citations = 0
    for block in response.content:
        if block.type == "text" and getattr(block, "citations", None):
            citations += len(block.citations)
    print(f"--- {label}")
    print(f"    stop_reason      = {response.stop_reason}")
    print(f"    input_tokens     = {usage.input_tokens}")
    print(f"    output_tokens    = {usage.output_tokens}")
    print(f"    web_searches     = {searches}")
    print(f"    citations        = {citations}")
    print(f"    text[0][:400]    = {(texts[0][:400] if texts else '(vazio)')!r}")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    tool = web_search_tool(settings)
    prompt = f"Busque anuncios de: {QUERY}. Somente no dominio {DOMAIN}."

    # A. structured outputs
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[tool],
            output_config={"format": {"type": "json_schema", "schema": LISTING_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        report("A output_config.format: FUNCIONOU", response)
    except Exception as error:  # noqa: BLE001 - we want the message, whatever it is
        print(f"--- A output_config.format: FALHOU\n    {type(error).__name__}: {error}")

    # B. JSON by prompt
    response = client.messages.create(
        model=settings.model,
        max_tokens=8000,
        system=SYSTEM,
        tools=[tool],
        messages=[{"role": "user", "content": prompt}],
    )
    report("B JSON por prompt", response)
    texts = [block.text for block in response.content if block.type == "text"]
    if texts:
        try:
            json.loads(texts[0])
            print("    JSON parseavel direto = sim")
        except json.JSONDecodeError as error:
            print(f"    JSON parseavel direto = NAO ({error})")

    # C. strict client tool
    record_tool: dict[str, Any] = {
        "name": "registrar_anuncios",
        "description": "Registra os anuncios encontrados.",
        "strict": True,
        "input_schema": LISTING_SCHEMA,
    }
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[tool, record_tool],
            messages=[{"role": "user", "content": prompt}],
        )
        report("C tool estrita: FUNCIONOU", response)
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        print(f"    tool_use blocks = {len(tool_uses)}")
    except Exception as error:  # noqa: BLE001
        print(f"--- C tool estrita: FALHOU\n    {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
