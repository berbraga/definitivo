"""The single seam between the pipeline and any source of listings."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from renov_market_scan.models import Listing, Query


@dataclass(frozen=True)
class SearchOutcome:
    """What one search produced, plus the raw payload to cache verbatim."""

    listings: list[Listing]
    status: str
    payload: dict[str, Any] = field(default_factory=dict)


class SearchAdapter(Protocol):
    """Any source of listings. One call per (model, source) pair, carrying
    every phrase. AnthropicSearchAdapter is the only networked implementation;
    tests collect through FixtureAdapter."""

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Run one call covering every phrase for a (model, source) pair.

        Both phrases travel in a single call so that max_uses caps the whole
        pair, which is what the cost estimate assumes.
        """
        ...
