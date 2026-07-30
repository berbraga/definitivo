from renov_market_scan.models import (
    Anomaly,
    Listing,
    ModelStats,
    RejectedListing,
    ReportKey,
    SearchPlanItem,
)
from renov_market_scan.report.assemble import (
    ANOMALY_COLUMNS,
    DISCARDED_COLUMNS,
    FOOTNOTE,
    SAMPLE_COLUMNS,
    SUMMARY_COLUMNS,
    build_anomaly_rows,
    build_discarded_rows,
    build_sample_rows,
    build_summary_rows,
)

DAY = "2026-07-28"


def make_item(search_key="k1", report_keys=None, **overrides) -> SearchPlanItem:
    base = {
        "search_key": search_key,
        "manufacturer": "APPLE",
        "model": "IPHONE 13",
        "storage_label": "128GB",
        "storage_gb": 128,
        "report_keys": report_keys
        or [
            ReportKey(
                erp_code="20022A0", model="IPHONE 13", storage_label="128GB",
                device_name="IPHONE 13 128GB A0", price_instore=2000.0, row_number=3,
            )
        ],
    }
    base.update(overrides)
    return SearchPlanItem(**base)


def make_stats(**overrides) -> ModelStats:
    base = {
        "search_key": "k1", "n": 5, "minimum": 2500.0, "p25": 2600.0, "median": 2800.0,
        "p75": 3000.0, "maximum": 3200.0, "spread_pct": 0.25,
        "min_url": "https://olx.com.br/min", "max_url": "https://olx.com.br/max",
        "min_raw": 90.0, "max_raw": 3200.0, "sources": ["olx", "enjoei"],
        "predominant_condition": "seminovo", "status": "ok",
    }
    base.update(overrides)
    return ModelStats(**base)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1", "source": "olx", "title": "iPhone 13 128GB seminovo",
        "price_brl": 2800.0, "condition": "seminovo", "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00", "cited_text": "R$ 2.800,00",
        "flag_5g_divergent": False,
    }
    base.update(overrides)
    return Listing(**base)


def test_summary_row_has_every_declared_column():
    rows = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)
    assert len(rows) == 1
    assert set(rows[0]) == set(SUMMARY_COLUMNS)


def test_summary_carries_the_statistics_and_both_links():
    row = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)[0]
    assert row["erp_code"] == "20022A0"
    assert row["mediana"] == 2800.0
    assert row["preco_minimo"] == 2500.0
    assert row["link_minimo"] == "https://olx.com.br/min"
    assert row["preco_maximo"] == 3200.0
    assert row["link_maximo"] == "https://olx.com.br/max"
    assert row["min_bruto"] == 90.0
    assert row["status"] == "ok"
    assert row["coletado_em"] == DAY


def test_ratio_against_the_sheet_price_is_computed():
    row = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)[0]
    assert row["razao_mediana_vs_atual"] == 2800.0 / 2000.0


def test_ratio_is_none_without_a_sheet_price():
    keys = [
        ReportKey(erp_code="X", model="IPHONE 13", storage_label="128GB",
                  device_name="d", price_instore=None, row_number=3)
    ]
    row = build_summary_rows([make_item(report_keys=keys)], {"k1": make_stats()}, DAY)[0]
    assert row["razao_mediana_vs_atual"] is None


def test_ratio_is_none_for_a_non_positive_sheet_price():
    keys = [
        ReportKey(erp_code="X", model="IPHONE 13", storage_label="128GB",
                  device_name="d", price_instore=-100.0, row_number=3)
    ]
    row = build_summary_rows([make_item(report_keys=keys)], {"k1": make_stats()}, DAY)[0]
    assert row["razao_mediana_vs_atual"] is None

    keys_zero = [
        ReportKey(erp_code="X", model="IPHONE 13", storage_label="128GB",
                  device_name="d", price_instore=0.0, row_number=3)
    ]
    row_zero = build_summary_rows([make_item(report_keys=keys_zero)], {"k1": make_stats()}, DAY)[0]
    assert row_zero["razao_mediana_vs_atual"] is None


def test_statistics_fan_out_to_every_report_key():
    keys = [
        ReportKey(erp_code="A", model="IPHONE 13", storage_label="128GB",
                  device_name="d1", price_instore=2000.0, row_number=3),
        ReportKey(erp_code="B", model="IPHONE 13", storage_label="128GB",
                  device_name="d2", price_instore=2000.0, row_number=4),
    ]
    rows = build_summary_rows([make_item(report_keys=keys)], {"k1": make_stats()}, DAY)
    assert [row["erp_code"] for row in rows] == ["A", "B"]
    assert {row["mediana"] for row in rows} == {2800.0}


def test_a_key_without_statistics_still_produces_a_row():
    rows = build_summary_rows([make_item()], {}, DAY)
    assert len(rows) == 1
    assert rows[0]["status"] == "insuficiente"
    assert rows[0]["mediana"] is None
    assert rows[0]["n_amostras"] == 0


def test_sample_rows_repeat_per_report_key_and_declare_evidence():
    rows = build_sample_rows([make_item()], {"k1": [make_listing()]})
    assert set(rows[0]) == set(SAMPLE_COLUMNS)
    assert rows[0]["erp_code"] == "20022A0"
    assert rows[0]["cited_text"] == "R$ 2.800,00"
    assert rows[0]["flag_5g_divergente"] == "nao"


def test_five_g_flag_renders_in_portuguese():
    rows = build_sample_rows([make_item()], {"k1": [make_listing(flag_5g_divergent=True)]})
    assert rows[0]["flag_5g_divergente"] == "sim"


def test_discarded_rows_always_carry_a_reason():
    rejected = [RejectedListing(listing=make_listing(), reason="acessorio_ou_peca")]
    rows = build_discarded_rows([make_item()], {"k1": rejected})
    assert set(rows[0]) == set(DISCARDED_COLUMNS)
    assert rows[0]["motivo_descarte"] == "acessorio_ou_peca"


def test_discarded_rows_fan_out_to_every_report_key():
    """A discard on a search key shared by two report keys must credit both
    ERP codes; otherwise the Descartados sheet would show the second code as
    discard-free even though the same rejected listing applies to it too."""
    keys = [
        ReportKey(erp_code="A", model="IPHONE 13", storage_label="128GB",
                  device_name="d1", price_instore=2000.0, row_number=3),
        ReportKey(erp_code="B", model="IPHONE 13", storage_label="128GB",
                  device_name="d2", price_instore=2000.0, row_number=4),
    ]
    rejected = [RejectedListing(listing=make_listing(), reason="acessorio_ou_peca")]
    rows = build_discarded_rows([make_item(report_keys=keys)], {"k1": rejected})
    assert {row["erp_code"] for row in rows} == {"A", "B"}
    assert {row["motivo_descarte"] for row in rows} == {"acessorio_ou_peca"}


def test_anomaly_rows_include_storage_and_empty_samples():
    anomaly = Anomaly(
        row_number=5, erp_code="10000A0", device_name="MOTO XT882 1GB A0",
        field="Storage, GB*", raw_value="1", reason="storage_suspeito", status="revisao_humana",
    )
    stats = {"k1": make_stats(n=0, status="insuficiente")}
    rows = build_anomaly_rows([anomaly], [make_item()], stats)
    assert set(rows[0]) == set(ANOMALY_COLUMNS)
    reasons = {row["motivo"] for row in rows}
    assert "storage_suspeito" in reasons
    assert "sem_amostra" in reasons


def test_a_model_with_samples_is_not_reported_as_an_anomaly():
    rows = build_anomaly_rows([], [make_item()], {"k1": make_stats(n=5)})
    assert rows == []


def test_the_footnote_states_the_asking_price_caveat():
    assert "anuncio" in FOOTNOTE.lower()
    assert "transacao" in FOOTNOTE.lower() or "transação" in FOOTNOTE.lower()
