"""Build search phrases from a plan item.

Two phrases per source, measured in the Phase 0 exploration: a generic phrase
returned 9 of 10 results as category pages with no unit price, while a phrase
written the way a seller writes an advert returned 5 of 10 as individual
adverts with the price in the title.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel

from renov_market_scan.ingest.normalize import strip_grade_suffix
from renov_market_scan.models import Query, SearchPlanItem

PHRASE_COUNT = 2

GENERIC_TERMS = "usado seminovo"
ADVERTISER_TERMS = "seminovo estado de conservacao r$"


class Source(BaseModel):
    """One marketplace, as configured in fontes.yaml."""

    name: str
    domain: str
    weight: float = 1.0
    enabled: bool = True


def load_sources(path: Path) -> list[Source]:
    """Load every configured source, enabled or not."""
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or []
    return [Source(**entry) for entry in loaded]


def enabled_sources(sources: list[Source], wanted: list[str] | None) -> list[Source]:
    """Select sources to use.

    Without an explicit selection, the enabled flag decides. An explicit
    selection is an override: naming a disabled source turns it on for this run.
    """
    if wanted is None:
        return [source for source in sources if source.enabled]
    requested = [name.strip().lower() for name in wanted if name.strip()]
    by_name = {source.name.lower(): source for source in sources}
    return [by_name[name] for name in requested if name in by_name]


def _brand_term(manufacturer: str, brand_aliases: dict[str, list[str]]) -> str:
    """The market-facing brand name, which is not always the sheet value."""
    aliases = brand_aliases.get(manufacturer.upper())
    if aliases:
        return aliases[0]
    return manufacturer.lower()


def build_queries(
    item: SearchPlanItem, sources: list[Source], brand_aliases: dict[str, list[str]]
) -> list[Query]:
    """Two phrases for every source, all lowercase."""
    brand = _brand_term(item.manufacturer, brand_aliases)
    model = strip_grade_suffix(item.model).lower()
    storage = item.storage_label.lower()
    base = f"{brand} {model} {storage}".strip()

    phrases = (
        f"{base} {GENERIC_TERMS}",
        f"{base} {ADVERTISER_TERMS}",
    )

    queries: list[Query] = []
    for source in sources:
        for index, phrase in enumerate(phrases):
            queries.append(
                Query(
                    search_key=item.search_key,
                    source=source.name,
                    domain=source.domain,
                    phrase_index=index,
                    text=" ".join(phrase.split()),
                )
            )
    return queries
