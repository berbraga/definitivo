from renov_market_scan.ingest.normalize import (
    build_search_plan,
    compute_search_key,
    normalize_storage,
    strip_grade_suffix,
)
from renov_market_scan.models import DeviceRow


def make_row(**overrides) -> DeviceRow:
    base = {
        "row_number": 3,
        "device_name": "SAMSUNG GALAXY A17 128GB A0",
        "manufacturer": "SAMSUNG",
        "model": "GALAXY A17",
        "device_type": "Phone",
        "storage_raw": "128",
        "ram_raw": None,
        "color": "Any Color",
        "price_instore": 400.0,
        "price_widget_mobile": 400.0,
        "erp_code": "10870A0",
    }
    base.update(overrides)
    return DeviceRow(**base)


def test_unambiguous_storages_are_normalized():
    assert normalize_storage("128").label == "128GB"
    assert normalize_storage("128").gb == 128
    assert normalize_storage("128").suspicious is False
    assert normalize_storage("1024").label == "1TB"
    assert normalize_storage("1024").gb == 1024
    assert normalize_storage("2048").label == "2TB"
    assert normalize_storage("2048").gb == 2048


def test_dirty_storages_are_flagged_and_never_guessed():
    for raw in ("1", "1288", "", None, "abc"):
        result = normalize_storage(raw)
        assert result.suspicious is True, raw


def test_dirty_storage_keeps_the_raw_value_in_the_label():
    assert normalize_storage("1288").label == "1288"
    assert normalize_storage("1").label == "1"


def test_grade_suffix_is_removed_only_at_the_end():
    assert strip_grade_suffix("SAMSUNG GALAXY A17 128GB A0") == "SAMSUNG GALAXY A17 128GB"
    assert strip_grade_suffix("MOTOROLA EDGE 70 512GB") == "MOTOROLA EDGE 70 512GB"
    assert strip_grade_suffix("A0 SPECIAL 64GB A0") == "A0 SPECIAL 64GB"


def test_search_key_is_stable_and_case_insensitive():
    first = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    second = compute_search_key("samsung", "galaxy a17", "128gb")
    assert first == second
    assert first != compute_search_key("SAMSUNG", "GALAXY A17", "256GB")


def test_active_only_drops_the_placeholder_price():
    rows = [make_row(), make_row(row_number=4, erp_code="X", price_instore=10.0)]
    plan, anomalies = build_search_plan(rows, active_only=True)
    assert len(plan) == 1
    assert plan[0].report_keys[0].erp_code == "10870A0"
    assert anomalies == []


def test_todos_keeps_inactive_rows():
    rows = [
        make_row(),
        make_row(row_number=4, model="GALAXY A55", erp_code="X", price_instore=10.0),
    ]
    plan, _ = build_search_plan(rows, active_only=False)
    assert len(plan) == 2


def test_suspicious_storage_becomes_an_anomaly_and_is_not_searched():
    rows = [make_row(storage_raw="1288", price_instore=400.0)]
    plan, anomalies = build_search_plan(rows, active_only=True)
    assert plan == []
    assert len(anomalies) == 1
    assert anomalies[0].reason == "storage_suspeito"
    assert anomalies[0].status == "revisao_humana"
    assert anomalies[0].raw_value == "1288"


def test_suspicious_storage_is_excluded_from_the_full_listing_too():
    """A suspicious row is never searched, in either mode: it would carry a
    nonsensical storage_gb (1, 1288 or 0), produce a query no real listing can
    match, and still cost money to search. active_only=False widens the batch
    to inactive-but-valid devices, not to rows whose capacity is known-corrupt.
    """
    rows = [make_row(storage_raw="1288", price_instore=400.0)]
    plan, anomalies = build_search_plan(rows, active_only=False)
    assert plan == []
    assert len(anomalies) == 1


def test_duplicate_erp_with_different_model_does_not_collapse():
    """The A55 / A55 5G pair shares one ERP Code but is two distinct devices."""
    rows = [
        make_row(row_number=3, model="GALAXY A55 5G", erp_code="10080A0", price_instore=640.0),
        make_row(row_number=4, model="GALAXY A55", erp_code="10080A0", price_instore=640.0),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 2
    assert {item.model for item in plan} == {"GALAXY A55 5G", "GALAXY A55"}


def test_duplicate_erp_across_manufacturers_does_not_collapse():
    """10884A0 is shared by a Motorola and a Realme in the real sheet."""
    rows = [
        make_row(row_number=3, manufacturer="MOTOROLA", model="MOTO G17", erp_code="10884A0"),
        make_row(row_number=4, manufacturer="REALME", model="C63", erp_code="10884A0"),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 2


def test_identical_combo_fans_out_to_several_report_keys():
    rows = [
        make_row(row_number=3, erp_code="A0001"),
        make_row(row_number=4, erp_code="A0002"),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 1
    assert {key.erp_code for key in plan[0].report_keys} == {"A0001", "A0002"}


def test_manufacturer_filter_is_case_insensitive():
    rows = [make_row(), make_row(row_number=4, manufacturer="MOTOROLA", erp_code="X")]
    plan, _ = build_search_plan(rows, manufacturer_filter="samsung")
    assert len(plan) == 1
    assert plan[0].manufacturer == "SAMSUNG"


def test_limit_takes_the_first_items_in_sheet_order():
    rows = [
        make_row(row_number=3, model="A", erp_code="1"),
        make_row(row_number=4, model="B", erp_code="2"),
        make_row(row_number=5, model="C", erp_code="3"),
    ]
    plan, _ = build_search_plan(rows, limit=2)
    assert [item.model for item in plan] == ["A", "B"]
