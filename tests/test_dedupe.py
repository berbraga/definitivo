from renov_market_scan.filtering.dedupe import canonical_url, dedupe
from renov_market_scan.models import Listing


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://sp.olx.com.br/celulares/iphone-13-128gb-seminovo-1446106085",
        "captured_at": "2026-07-28T10:00:00",
    }
    base.update(overrides)
    return Listing(**base)


def test_tracking_parameters_are_stripped():
    dirty = "https://olx.com.br/x-123?utm_source=google&utm_medium=cpc&gclid=abc"
    assert canonical_url(dirty) == "https://olx.com.br/x-123"


def test_meaningful_query_parameters_survive():
    url = "https://lista.mercadolivre.com.br/celulares?q=iphone+13"
    assert canonical_url(url) == "https://lista.mercadolivre.com.br/celulares?q=iphone+13"


def test_trailing_slash_and_fragment_are_normalized():
    assert canonical_url("https://olx.com.br/x-123/#descricao") == "https://olx.com.br/x-123"


def test_scheme_and_host_case_are_normalized():
    assert canonical_url("HTTPS://OLX.COM.BR/X-123") == "https://olx.com.br/X-123"


def test_same_canonical_url_is_deduped():
    first = make_listing()
    second = make_listing(url=make_listing().url + "?utm_source=x")
    kept, duplicates = dedupe([first, second])
    assert len(kept) == 1
    assert len(duplicates) == 1


def test_same_title_and_price_from_a_different_url_is_deduped():
    first = make_listing(url="https://olx.com.br/a-1")
    second = make_listing(url="https://olx.com.br/b-2")
    kept, duplicates = dedupe([first, second])
    assert len(kept) == 1
    assert len(duplicates) == 1


def test_same_title_with_a_different_price_is_kept():
    first = make_listing(url="https://olx.com.br/a-1", price_brl=3050.0)
    second = make_listing(url="https://olx.com.br/b-2", price_brl=2800.0)
    kept, _ = dedupe([first, second])
    assert len(kept) == 2


def test_first_occurrence_is_the_one_kept():
    first = make_listing(url="https://olx.com.br/a-1", source="olx")
    second = make_listing(url="https://olx.com.br/a-1", source="enjoei")
    kept, _ = dedupe([first, second])
    assert kept[0].source == "olx"


def test_empty_input_is_handled():
    kept, duplicates = dedupe([])
    assert kept == []
    assert duplicates == []
