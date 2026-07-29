from renov_market_scan.models import (
    Anomaly,
    DeviceRow,
    Listing,
    ModelStats,
    Query,
    RejectedListing,
    ReportKey,
    SearchPlanItem,
    StorageNorm,
)


def test_device_row_holds_the_nineteen_column_subset_we_use():
    row = DeviceRow(
        row_number=3,
        device_name="SAMSUNG GALAXY A17 128GB A0",
        manufacturer="SAMSUNG",
        model="GALAXY A17",
        device_type="Phone",
        storage_raw="128",
        ram_raw=None,
        color="Any Color",
        price_instore=400.0,
        price_widget_mobile=400.0,
        erp_code="10870A0",
    )
    assert row.erp_code == "10870A0"
    assert row.ram_raw is None


def test_device_row_is_active_when_price_is_above_ten():
    active = DeviceRow(
        row_number=3, device_name="d", manufacturer="m", model="mo",
        device_type="Phone", storage_raw="128", ram_raw=None, color="Any Color",
        price_instore=400.0, price_widget_mobile=400.0, erp_code="1A0",
    )
    inactive = active.model_copy(update={"price_instore": 10.0})
    missing = active.model_copy(update={"price_instore": None})
    assert active.is_active is True
    assert inactive.is_active is False
    assert missing.is_active is False


def test_storage_norm_carries_label_gb_and_suspicion():
    ok = StorageNorm(label="1TB", gb=1024, suspicious=False)
    bad = StorageNorm(label="1", gb=1, suspicious=True)
    assert ok.gb == 1024
    assert bad.suspicious is True


def test_listing_defaults_are_safe():
    listing = Listing(
        search_key="k", source="olx", title="t", price_brl=100.0,
        condition="usado", url="https://x", captured_at="2026-07-28T00:00:00",
    )
    assert listing.cited_text == ""
    assert listing.flag_5g_divergent is False


def test_model_stats_allows_absent_statistics_for_small_samples():
    stats = ModelStats(
        search_key="k", n=2, minimum=100.0, p25=None, median=None, p75=None,
        maximum=200.0, spread_pct=None, min_url="https://a", max_url="https://b",
        min_raw=100.0, max_raw=200.0, sources=["olx"],
        predominant_condition="usado", status="insuficiente",
    )
    assert stats.median is None
    assert stats.status == "insuficiente"


def test_remaining_models_construct():
    key = ReportKey(
        erp_code="10870A0", model="GALAXY A17", storage_label="128GB",
        device_name="SAMSUNG GALAXY A17 128GB A0", price_instore=400.0,
        row_number=3,
    )
    item = SearchPlanItem(
        search_key="k", manufacturer="SAMSUNG", model="GALAXY A17",
        storage_label="128GB", storage_gb=128, report_keys=[key],
    )
    query = Query(
        search_key="k", source="olx", domain="olx.com.br", phrase_index=0,
        text="samsung galaxy a17 128gb usado seminovo",
    )
    anomaly = Anomaly(
        row_number=5, erp_code="10000A0", device_name="MOTO XT882 1GB A0",
        field="Storage, GB*", raw_value="1", reason="storage_suspeito",
        status="revisao_humana",
    )
    rejected = RejectedListing(
        listing=Listing(
            search_key="k", source="olx", title="capa", price_brl=20.0,
            condition="desconhecido", url="https://x",
            captured_at="2026-07-28T00:00:00",
        ),
        reason="acessorio_ou_peca",
    )
    assert item.report_keys[0].erp_code == "10870A0"
    assert query.phrase_index == 0
    assert anomaly.status == "revisao_humana"
    assert rejected.reason == "acessorio_ou_peca"
