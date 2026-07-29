from renov_market_scan.cache.store import (
    count_raw,
    has_raw,
    load_listings,
    open_store,
    save_listings,
    save_raw,
)
from renov_market_scan.models import Listing, RawResponse

DAY = "2026-07-28"


def make_raw(**overrides) -> RawResponse:
    base = {
        "search_key": "k1",
        "source": "olx",
        "phrase_index": 0,
        "collected_on": DAY,
        "payload": {"content": [{"type": "text", "text": "[]"}], "stop_reason": "end_turn"},
        "status": "ok",
    }
    base.update(overrides)
    return RawResponse(**base)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "R$ 3.050,00",
        "flag_5g_divergent": False,
    }
    base.update(overrides)
    return Listing(**base)


def test_schema_is_created_on_open(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_search", "listing"} <= tables
    conn.close()


def test_raw_round_trips_and_is_detected(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    assert has_raw(conn, "k1", "olx", 0, DAY) is False
    save_raw(conn, make_raw())
    assert has_raw(conn, "k1", "olx", 0, DAY) is True
    conn.close()


def test_raw_is_keyed_by_phrase_source_and_day(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    assert has_raw(conn, "k1", "olx", 1, DAY) is False
    assert has_raw(conn, "k1", "enjoei", 0, DAY) is False
    assert has_raw(conn, "k1", "olx", 0, "2026-07-29") is False
    conn.close()


def test_saving_the_same_key_twice_replaces_instead_of_duplicating(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    save_raw(conn, make_raw(status="bloqueado"))
    assert count_raw(conn, DAY) == 1
    row = conn.execute("SELECT status FROM raw_search").fetchone()
    assert row[0] == "bloqueado"
    conn.close()


def test_payload_survives_as_structured_json(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw(payload={"a": [1, 2, {"b": "c"}]}))
    row = conn.execute("SELECT payload FROM raw_search").fetchone()
    import json

    assert json.loads(row[0]) == {"a": [1, 2, {"b": "c"}]}
    conn.close()


def test_count_raw_counts_only_the_requested_day(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    save_raw(conn, make_raw(phrase_index=1))
    save_raw(conn, make_raw(collected_on="2026-07-29"))
    assert count_raw(conn, DAY) == 2
    conn.close()


def test_listings_round_trip_grouped_by_search_key(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(
        conn, "k1", "olx", DAY, [make_listing(), make_listing(url="https://olx.com.br/a-2")]
    )
    save_listings(conn, "k2", "olx", DAY, [make_listing(search_key="k2")])
    loaded = load_listings(conn, DAY)
    assert set(loaded) == {"k1", "k2"}
    assert len(loaded["k1"]) == 2
    assert loaded["k1"][0].price_brl == 3050.0
    assert loaded["k1"][0].cited_text == "R$ 3.050,00"
    conn.close()


def test_resaving_listings_for_a_key_replaces_the_previous_set(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(
        conn, "k1", "olx", DAY, [make_listing(), make_listing(url="https://olx.com.br/a-2")]
    )
    save_listings(conn, "k1", "olx", DAY, [make_listing()])
    loaded = load_listings(conn, DAY)
    assert len(loaded["k1"]) == 1
    conn.close()


def test_a_none_price_survives_the_round_trip(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(conn, "k1", "olx", DAY, [make_listing(price_brl=None)])
    loaded = load_listings(conn, DAY)
    assert loaded["k1"][0].price_brl is None
    conn.close()


def test_the_row_key_comes_from_the_arguments_not_the_listing(tmp_path):
    """If the insert used the listing's own key, clearing k1 would leave a row
    nothing can ever delete."""
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(conn, "k1", "olx", DAY, [make_listing(search_key="OTHER")])
    loaded = load_listings(conn, DAY)
    assert set(loaded) == {"k1"}
    save_listings(conn, "k1", "olx", DAY, [])
    assert load_listings(conn, DAY) == {}
    conn.close()
