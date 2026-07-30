"""Estimate what a run will cost before spending anything.

The search fee is fixed at US$ 0.01 per search regardless of model, so it is the
floor of any run. Token cost is the part that model choice moves. The token
figures are not guessed: they come from the Task 15 spike measurement.
"""

from dataclasses import dataclass

from renov_market_scan.config import Settings
from renov_market_scan.models import SearchPlanItem
from renov_market_scan.query.builder import PHRASE_COUNT, Source

PRICE_PER_SEARCH_USD = 0.01

# (input, output) US$ per million tokens.
MODEL_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),  # introductory pricing through 2026-08-31
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

FALLBACK_PRICE_PER_MTOK = (10.00, 50.00)

# Wall-clock seconds per call at the default concurrency, measured empirically.
SECONDS_PER_CALL = 12.0


@dataclass(frozen=True)
class Calibration:
    """Per-pair figures measured by the Task 15 spike.

    A pair costs two calls: a search call (with the search tool, which is
    what spends web_search_requests) and a separate extraction call (no
    tool, different token profile). Kept apart because forcing JSON-only
    output in the same call as the search suppresses citations entirely
    (measured in the spike) — merging their token counts into one number
    would hide that a real run always pays for two distinct calls.
    """

    search_tokens_in: int
    search_tokens_out: int
    extraction_tokens_in: int
    extraction_tokens_out: int
    searches_per_call: float


CALIBRATION_FROM_SPIKE = Calibration(
    search_tokens_in=25808,
    search_tokens_out=1521,
    extraction_tokens_in=2492,
    extraction_tokens_out=3809,
    searches_per_call=2.0,
)


@dataclass(frozen=True)
class CostEstimate:
    """What a run is expected to cost, and the worst case."""

    pairs: int
    calls: int
    searches_expected: float
    searches_ceiling: int
    search_cost_usd: float
    token_cost_usd: float
    total_usd: float
    ceiling_usd: float
    minutes: float


def estimate(
    plan_items: list[SearchPlanItem],
    sources: list[Source],
    settings: Settings,
    calibration: Calibration = CALIBRATION_FROM_SPIKE,
) -> CostEstimate:
    """Estimate the cost of collecting this plan from these sources.

    Both phrases of a (model, source) pair travel in the search call, so the
    pair count is items times sources. Each pair costs two calls — search,
    then extraction — so the search ceiling (which only the search call can
    spend) is pairs times max_uses, while token cost sums both calls' own
    token counts.
    """
    pairs = len(plan_items) * len(sources)
    calls = pairs * 2
    searches_expected = pairs * calibration.searches_per_call
    searches_ceiling = pairs * settings.max_uses_per_call

    price_in, price_out = MODEL_PRICES_PER_MTOK.get(settings.model, FALLBACK_PRICE_PER_MTOK)
    tokens_in = pairs * (calibration.search_tokens_in + calibration.extraction_tokens_in)
    tokens_out = pairs * (calibration.search_tokens_out + calibration.extraction_tokens_out)
    token_cost = tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out
    search_cost = searches_expected * PRICE_PER_SEARCH_USD
    ceiling_cost = searches_ceiling * PRICE_PER_SEARCH_USD + token_cost

    concurrency = max(settings.concurrency, 1)
    minutes = calls * SECONDS_PER_CALL / concurrency / 60.0

    return CostEstimate(
        pairs=pairs,
        calls=calls,
        searches_expected=searches_expected,
        searches_ceiling=searches_ceiling,
        search_cost_usd=search_cost,
        token_cost_usd=token_cost,
        total_usd=search_cost + token_cost,
        ceiling_usd=ceiling_cost,
        minutes=minutes,
    )


def format_estimate(estimate_result: CostEstimate) -> str:
    """Human-readable pt-BR summary for the dry-run output."""
    return (
        f"Pares (modelo x fonte): {estimate_result.pairs}\n"
        f"Chamadas a API:      {estimate_result.calls}\n"
        f"Frases por chamada:  {PHRASE_COUNT}\n"
        f"Buscas esperadas:    {estimate_result.searches_expected:.0f}\n"
        f"Buscas no teto:      {estimate_result.searches_ceiling}\n"
        f"Custo de busca:      US$ {estimate_result.search_cost_usd:.2f}\n"
        f"Custo de tokens:     US$ {estimate_result.token_cost_usd:.2f}\n"
        f"Total esperado:      US$ {estimate_result.total_usd:.2f}\n"
        f"Total no teto:       US$ {estimate_result.ceiling_usd:.2f}\n"
        f"Tempo previsto:      {estimate_result.minutes:.0f} minuto(s)"
    )
