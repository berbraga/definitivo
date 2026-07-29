"""Deduplicate listings by canonical URL and by (normalized title, price)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from renov_market_scan.filtering.text import normalize_text
from renov_market_scan.models import Listing

DUPLICATE = "duplicado"

# Query parameters that identify a campaign, not the advert.
TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "gclid", "fbclid", "mkt_", "pk_", "_gl")


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Strip tracking parameters, fragments and trailing slashes.

    Scheme and host are lowercased; the path keeps its case because some
    marketplaces use case-sensitive advert slugs.
    """
    parts = urlsplit(url.strip())
    kept = [(name, value) for name, value in parse_qsl(parts.query) if not _is_tracking(name)]
    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), "")
    )


def dedupe(listings: list[Listing]) -> tuple[list[Listing], list[Listing]]:
    """Return (kept, duplicates). The first occurrence of a key is kept."""
    seen_urls: set[str] = set()
    seen_title_price: set[tuple[str, float | None]] = set()
    kept: list[Listing] = []
    duplicates: list[Listing] = []

    for listing in listings:
        url_key = canonical_url(listing.url)
        title_price_key = (normalize_text(listing.title), listing.price_brl)
        if url_key in seen_urls or title_price_key in seen_title_price:
            duplicates.append(listing)
            continue
        seen_urls.add(url_key)
        seen_title_price.add(title_price_key)
        kept.append(listing)

    return kept, duplicates
