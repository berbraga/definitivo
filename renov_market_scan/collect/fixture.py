"""Offline adapter. Every test in the suite collects through this class."""

from typing import Any

from renov_market_scan.collect.base import SearchAdapter, SearchOutcome
from renov_market_scan.models import Listing, Query


class FixtureAdapter(SearchAdapter):
    """Return canned listings keyed by (search_key, source)."""

    def __init__(
        self,
        listings_by_key: dict[tuple[str, str], list[Listing]],
        statuses: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._listings = listings_by_key
        self._statuses = statuses or {}
        self.call_count = 0

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Look up the canned result. Never touches the network."""
        self.call_count += 1
        if not queries:
            return SearchOutcome(listings=[], status="ok", payload={"fixture": True})
        key = (queries[0].search_key, queries[0].source)
        listings = self._listings.get(key, [])
        status = self._statuses.get(key, "ok")
        payload: dict[str, Any] = {
            "fixture": True,
            "queries": [query.text for query in queries],
            "listings": [item.model_dump() for item in listings],
        }
        return SearchOutcome(
            listings=[item.model_copy() for item in listings],
            status=status,
            payload=payload,
        )
