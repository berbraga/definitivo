import asyncio

from renov_market_scan.collect.fixture import FixtureAdapter
from renov_market_scan.models import Listing, Query

QUERY = Query(
    search_key="k1", source="olx", domain="olx.com.br", phrase_index=0, text="iphone 13 128gb"
)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo R$ 3.050,00",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "R$ 3.050,00",
    }
    base.update(overrides)
    return Listing(**base)


def test_returns_the_canned_listings_for_a_key_and_source():
    adapter = FixtureAdapter({("k1", "olx"): [make_listing()]})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert len(outcome.listings) == 1
    assert outcome.status == "ok"


def test_an_unknown_key_returns_an_empty_ok_result():
    adapter = FixtureAdapter({})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.listings == []
    assert outcome.status == "ok"


def test_a_configured_status_is_returned_verbatim():
    adapter = FixtureAdapter({}, statuses={("k1", "olx"): "bloqueado"})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "bloqueado"


def test_the_payload_is_serializable_for_the_cache():
    import json

    adapter = FixtureAdapter({("k1", "olx"): [make_listing()]})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert json.dumps(outcome.payload)


def test_calls_are_recorded_so_tests_can_assert_zero_network():
    adapter = FixtureAdapter({})
    asyncio.run(adapter.search([QUERY]))
    asyncio.run(adapter.search([QUERY]))
    assert adapter.call_count == 2
