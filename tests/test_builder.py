from pathlib import Path

import pytest

from renov_market_scan.models import ReportKey, SearchPlanItem
from renov_market_scan.query.builder import (
    PHRASE_COUNT,
    build_queries,
    enabled_sources,
    load_sources,
)


def make_item(**overrides) -> SearchPlanItem:
    base = {
        "search_key": "k1",
        "manufacturer": "APPLE",
        "model": "IPHONE 13",
        "storage_label": "128GB",
        "storage_gb": 128,
        "report_keys": [
            ReportKey(
                erp_code="20022A0",
                model="IPHONE 13",
                storage_label="128GB",
                device_name="IPHONE 13 128GB A0",
                price_instore=2000.0,
                row_number=3,
            )
        ],
    }
    base.update(overrides)
    return SearchPlanItem(**base)


ENABLED_V2 = {
    "mercadolivre",
    "trocafy",
    "cellularstore",
}


def test_sources_load_from_yaml():
    sources = load_sources(Path("fontes.yaml"))
    names = [source.name for source in sources]
    assert "trocafy" in names and "trocafone" in names
    by_name = {source.name: source for source in sources}
    assert by_name["trocafy"].enabled is True
    assert by_name["trocafy"].domain == "trocafy.com.br"
    assert by_name["rede_cell_store"].enabled is False
    assert by_name["celular_seminovo_rede"].enabled is False
    assert by_name["olx"].enabled is False
    assert by_name["trocafone"].enabled is False


def test_only_enabled_sources_are_used_by_default():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, None)
    assert {source.name for source in selected} == ENABLED_V2


def test_explicit_selection_replaces_the_default_list():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["trocafy"])
    assert [source.name for source in selected] == ["trocafy"]


def test_selecting_a_disabled_source_enables_it_explicitly():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["olx"])
    assert [source.name for source in selected] == ["olx"]


def test_two_phrases_per_source():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["trocafy", "mercadolivre"])
    queries = build_queries(make_item(), selected, {})
    assert len(queries) == 2 * PHRASE_COUNT
    assert {query.phrase_index for query in queries} == {0, 1}
    assert {query.source for query in queries} == {"trocafy", "mercadolivre"}


def test_generic_phrase_carries_brand_model_storage_and_used_terms():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    generic = next(q for q in build_queries(make_item(), sources, {}) if q.phrase_index == 0)
    assert "apple" in generic.text
    assert "iphone 13" in generic.text
    assert "128gb" in generic.text
    assert "usado" in generic.text and "seminovo" in generic.text


def test_advertiser_phrase_differs_from_the_generic_one():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    queries = build_queries(make_item(), sources, {})
    generic = next(q for q in queries if q.phrase_index == 0)
    advertiser = next(q for q in queries if q.phrase_index == 1)
    assert generic.text != advertiser.text
    assert "r$" in advertiser.text


def test_grade_suffix_is_removed_from_the_query():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    item = make_item(model="GALAXY A17 A0", manufacturer="SAMSUNG")
    for query in build_queries(item, sources, {}):
        assert " a0" not in query.text


def test_brand_alias_replaces_the_sheet_manufacturer():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    item = make_item(manufacturer="REDMI", model="NOTE 12")
    aliases = {"REDMI": ["xiaomi redmi", "redmi"]}
    generic = next(q for q in build_queries(item, sources, aliases) if q.phrase_index == 0)
    assert "xiaomi redmi" in generic.text


def test_terabyte_label_reaches_the_query_as_written():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    item = make_item(storage_label="1TB", storage_gb=1024)
    generic = next(q for q in build_queries(item, sources, {}) if q.phrase_index == 0)
    assert "1tb" in generic.text


def test_domain_travels_with_the_query():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    for query in build_queries(make_item(), sources, {}):
        assert query.domain == "trocafy.com.br"
        assert query.search_key == "k1"


def test_duplicate_sources_are_deduplicated_to_avoid_double_billing():
    """A duplicated source name (same or different case) should not generate
    duplicate queries, which would cost money with no sample benefit."""
    sources = load_sources(Path("fontes.yaml"))
    selected_dup = enabled_sources(sources, ["trocafy", "trocafy"])
    selected_case = enabled_sources(sources, ["trocafy", "TROCAFY"])
    item = make_item()
    queries_dup = build_queries(item, selected_dup, {})
    queries_case = build_queries(item, selected_case, {})
    assert len(queries_dup) == PHRASE_COUNT
    assert len(queries_case) == PHRASE_COUNT
    assert {query.source for query in queries_dup} == {"trocafy"}
    assert {query.source for query in queries_case} == {"trocafy"}


def test_unmatched_source_name_raises_with_valid_names():
    sources = load_sources(Path("fontes.yaml"))
    with pytest.raises(ValueError) as exc_info:
        enabled_sources(sources, ["trocafyy"])
    assert "trocafyy" in str(exc_info.value)
    assert "Valid sources:" in str(exc_info.value)


def test_mixed_case_alias_is_lowercased_in_phrase():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["trocafy"])
    item = make_item(manufacturer="REDMI", model="NOTE 12")
    aliases = {"REDMI": ["Xiaomi Redmi"]}
    generic = next(q for q in build_queries(item, sources, aliases) if q.phrase_index == 0)
    assert "xiaomi redmi" in generic.text
    assert "Xiaomi" not in generic.text
