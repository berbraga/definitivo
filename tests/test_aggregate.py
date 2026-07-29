import pytest

from renov_market_scan.models import Listing
from renov_market_scan.stats.aggregate import aggregate, iqr_bounds, quantile


def make_listing(price: float, url: str, source: str = "olx", condition: str = "usado") -> Listing:
    return Listing(
        search_key="k",
        source=source,
        title=f"iPhone 13 128GB R$ {price:.2f}",
        price_brl=price,
        condition=condition,
        url=url,
        captured_at="2026-07-28T10:00:00",
    )


def test_quantile_uses_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.5) == 2.5
    assert quantile(values, 0.25) == 1.75
    assert quantile(values, 0.75) == 3.25


def test_quantile_handles_degenerate_inputs():
    assert quantile([], 0.5) is None
    assert quantile([7.0], 0.5) == 7.0


def test_median_with_an_even_sample():
    prices = [100.0, 200.0, 300.0, 400.0]
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate(prices)]
    stats = aggregate("k", listings)
    assert stats.median == 250.0
    assert stats.n == 4


def test_median_with_an_odd_sample():
    prices = [100.0, 200.0, 300.0]
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate(prices)]
    stats = aggregate("k", listings)
    assert stats.median == 200.0


def test_iqr_removes_the_ninety_reais_outlier_from_an_eighteen_hundred_sample():
    prices = [90.0, 1750.0, 1780.0, 1800.0, 1820.0, 1850.0]
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate(prices)]
    stats = aggregate("k", listings)
    assert stats.n == 5
    assert stats.minimum == 1750.0
    assert stats.maximum == 1850.0
    assert stats.min_raw == 90.0
    assert stats.max_raw == 1850.0
    assert stats.status == "ok"


def test_extremes_carry_the_url_of_their_own_listing():
    prices = [1750.0, 1780.0, 1800.0, 1820.0, 1850.0]
    listings = [make_listing(p, f"https://x/{int(p)}") for p in prices]
    stats = aggregate("k", listings)
    assert stats.min_url == "https://x/1750"
    assert stats.max_url == "https://x/1850"


def test_status_ok_at_five():
    listings = [make_listing(1000.0 + i, f"https://x/{i}") for i in range(5)]
    assert aggregate("k", listings).status == "ok"


def test_status_low_sample_between_three_and_four():
    for count in (3, 4):
        listings = [make_listing(1000.0 + i, f"https://x/{i}") for i in range(count)]
        assert aggregate("k", listings).status == "amostra_baixa", count


def test_status_insufficient_below_three_and_no_median():
    listings = [make_listing(1000.0, "https://x/1"), make_listing(1200.0, "https://x/2")]
    stats = aggregate("k", listings)
    assert stats.n == 2
    assert stats.status == "insuficiente"
    assert stats.median is None
    assert stats.p25 is None
    assert stats.p75 is None
    assert stats.spread_pct is None
    assert stats.minimum == 1000.0
    assert stats.maximum == 1200.0
    assert stats.min_url == "https://x/1"


def test_empty_sample_is_insufficient():
    stats = aggregate("k", [])
    assert stats.n == 0
    assert stats.status == "insuficiente"
    assert stats.minimum is None
    assert stats.min_url is None


def test_spread_pct_is_computed_from_the_clean_sample():
    prices = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0]
    listings = [make_listing(p, f"https://x/{int(p)}") for p in prices]
    stats = aggregate("k", listings)
    assert stats.median == 1200.0
    assert stats.spread_pct == pytest.approx((1400.0 - 1000.0) / 1200.0)


def test_sources_and_predominant_condition_are_reported():
    listings = [
        make_listing(1000.0, "https://x/1", source="olx", condition="usado"),
        make_listing(1100.0, "https://x/2", source="olx", condition="usado"),
        make_listing(1200.0, "https://x/3", source="enjoei", condition="seminovo"),
    ]
    stats = aggregate("k", listings)
    assert sorted(stats.sources) == ["enjoei", "olx"]
    assert stats.predominant_condition == "usado"


def test_iqr_is_not_applied_below_four_points():
    """With three points the quartiles are meaningless; keep the sample intact."""
    listings = [
        make_listing(90.0, "https://x/1"),
        make_listing(1800.0, "https://x/2"),
        make_listing(1820.0, "https://x/3"),
    ]
    stats = aggregate("k", listings)
    assert stats.n == 3
    assert stats.minimum == 90.0


def test_iqr_bounds_returns_none_for_small_samples():
    assert iqr_bounds([1.0, 2.0]) is None
    assert iqr_bounds([1.0, 2.0, 3.0, 4.0]) is not None
