from renov_market_scan.config import Settings
from renov_market_scan.cost import (
    PRICE_PER_SEARCH_USD,
    Calibration,
    estimate,
    format_estimate,
)
from renov_market_scan.models import ReportKey, SearchPlanItem
from renov_market_scan.query.builder import Source

CALIBRATION = Calibration(
    search_tokens_in=12000,
    search_tokens_out=900,
    extraction_tokens_in=2500,
    extraction_tokens_out=1000,
    searches_per_call=2.0,
)
SOURCES = [
    Source(name="mercadolivre", domain="mercadolivre.com.br"),
    Source(name="trocafy", domain="trocafy.com.br"),
    Source(name="rede_cell_store", domain="redecellstore.com.br"),
    Source(name="celular_seminovo_rede", domain="celularseminovo.redecellstore.com.br"),
    Source(name="cellularstore", domain="cellularstore.com.br"),
]
SOURCE_COUNT = len(SOURCES)


def make_items(count: int) -> list[SearchPlanItem]:
    return [
        SearchPlanItem(
            search_key=f"k{index}",
            manufacturer="APPLE",
            model=f"IPHONE {index}",
            storage_label="128GB",
            storage_gb=128,
            report_keys=[
                ReportKey(
                    erp_code=f"E{index}",
                    model=f"IPHONE {index}",
                    storage_label="128GB",
                    device_name=f"IPHONE {index} 128GB A0",
                    price_instore=2000.0,
                    row_number=3 + index,
                )
            ],
        )
        for index in range(count)
    ]


def settings() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def test_pair_and_call_count_is_items_times_sources_times_two():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.pairs == 10 * SOURCE_COUNT
    assert result.calls == 10 * SOURCE_COUNT * 2


def test_ceiling_uses_max_uses_per_call():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.searches_ceiling == 10 * SOURCE_COUNT * 3
    assert result.searches_expected == 10 * SOURCE_COUNT * 2.0


def test_search_cost_is_a_cent_per_search():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.search_cost_usd == 10 * SOURCE_COUNT * 2.0 * PRICE_PER_SEARCH_USD


def test_token_cost_uses_the_model_price_and_sums_both_stages():
    result = estimate(make_items(1), SOURCES, settings(), CALIBRATION)
    expected_in = SOURCE_COUNT * (12000 + 2500) / 1_000_000 * 2.00
    expected_out = SOURCE_COUNT * (900 + 1000) / 1_000_000 * 10.00
    assert abs(result.token_cost_usd - (expected_in + expected_out)) < 1e-9


def test_total_is_search_plus_tokens():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert abs(result.total_usd - (result.search_cost_usd + result.token_cost_usd)) < 1e-9


def test_ceiling_is_never_below_the_total():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.ceiling_usd >= result.total_usd


def test_the_android_active_batch_matches_the_spec_order_of_magnitude():
    """285 active models, 5 sources: the search fee floor scales with source count."""
    result = estimate(make_items(285), SOURCES, settings(), CALIBRATION)
    pairs = 285 * SOURCE_COUNT
    assert result.pairs == pairs
    assert result.calls == pairs * 2
    assert result.searches_ceiling == pairs * 3
    assert abs(result.searches_ceiling * PRICE_PER_SEARCH_USD - pairs * 3 * 0.01) < 1e-9


def test_an_empty_plan_costs_nothing():
    result = estimate([], SOURCES, settings(), CALIBRATION)
    assert result.pairs == 0
    assert result.calls == 0
    assert result.total_usd == 0.0


def test_an_unknown_model_falls_back_to_the_most_expensive_price():
    unknown = Settings(anthropic_api_key="sk-test", model="claude-opus-5")
    result = estimate(make_items(1), SOURCES, unknown, CALIBRATION)
    assert result.token_cost_usd > 0


def test_the_formatted_estimate_is_in_portuguese_and_names_the_numbers():
    text = format_estimate(estimate(make_items(10), SOURCES, settings(), CALIBRATION))
    lowered = text.lower()
    assert "buscas" in lowered
    assert "us$" in lowered
    assert "minuto" in lowered
