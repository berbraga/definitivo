"""Per-search-key statistics with IQR outlier removal.

The reported minimum and maximum are the extremes of the cleaned sample and
each carries the URL of its own advert. The pre-cleaning extremes are kept in
min_raw and max_raw so the removal is auditable rather than invisible — a
reader can see that a R$ 90 entry was dropped from an R$ 1.800 sample.
"""

import math
from collections import Counter

from renov_market_scan.models import Condition, Listing, ModelStats, SampleStatus

# Below four points the quartiles carry no information and the IQR filter can
# empty the sample, so it is not applied.
MIN_SAMPLE_FOR_IQR = 4

STATUS_OK_THRESHOLD = 5
STATUS_LOW_THRESHOLD = 3

IQR_MULTIPLIER = 1.5


def quantile(sorted_values: list[float], q: float) -> float | None:
    """Linear-interpolation quantile over an already-sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def iqr_bounds(values: list[float]) -> tuple[float, float] | None:
    """Inclusive [lower, upper] acceptance bounds, or None for a small sample."""
    if len(values) < MIN_SAMPLE_FOR_IQR:
        return None
    ordered = sorted(values)
    q1 = quantile(ordered, 0.25)
    q3 = quantile(ordered, 0.75)
    if q1 is None or q3 is None:
        return None
    spread = q3 - q1
    return q1 - IQR_MULTIPLIER * spread, q3 + IQR_MULTIPLIER * spread


def _status(count: int) -> SampleStatus:
    """Sample-size status. Annotated with the Literal, not str, so that passing it
    to ModelStats.status type-checks under mypy strict."""
    if count >= STATUS_OK_THRESHOLD:
        return "ok"
    if count >= STATUS_LOW_THRESHOLD:
        return "amostra_baixa"
    return "insuficiente"


def aggregate(search_key: str, listings: list[Listing]) -> ModelStats:
    """Compute statistics for one search key from its accepted listings."""
    priced = [item for item in listings if item.price_brl is not None]
    raw_prices = [item.price_brl for item in priced if item.price_brl is not None]

    if not priced:
        return ModelStats(
            search_key=search_key,
            n=0,
            minimum=None,
            p25=None,
            median=None,
            p75=None,
            maximum=None,
            spread_pct=None,
            min_url=None,
            max_url=None,
            min_raw=None,
            max_raw=None,
            sources=[],
            predominant_condition="desconhecido",
            status="insuficiente",
        )

    bounds = iqr_bounds(raw_prices)
    if bounds is None:
        clean = priced
    else:
        low, high = bounds
        clean = [
            item
            for item in priced
            if item.price_brl is not None and low <= item.price_brl <= high
        ]
        if not clean:
            clean = priced

    ordered = sorted(clean, key=lambda item: item.price_brl or 0.0)
    prices = [item.price_brl for item in ordered if item.price_brl is not None]
    count = len(prices)
    status = _status(count)

    has_stats = status != "insuficiente"
    median = quantile(prices, 0.5) if has_stats else None
    p25 = quantile(prices, 0.25) if has_stats else None
    p75 = quantile(prices, 0.75) if has_stats else None
    minimum = prices[0]
    maximum = prices[-1]
    spread = (maximum - minimum) / median if median else None

    condition_counts = Counter(item.condition for item in ordered)
    predominant: Condition = (
        condition_counts.most_common(1)[0][0] if condition_counts else "desconhecido"
    )

    return ModelStats(
        search_key=search_key,
        n=count,
        minimum=minimum,
        p25=p25,
        median=median,
        p75=p75,
        maximum=maximum,
        spread_pct=spread,
        min_url=ordered[0].url,
        max_url=ordered[-1].url,
        min_raw=min(raw_prices),
        max_raw=max(raw_prices),
        sources=sorted({item.source for item in ordered}),
        predominant_condition=predominant,
        status=status,
    )
