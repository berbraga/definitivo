"""Order the filtering rules and record why every discard happened.

The first rule that rejects a listing is the one recorded, so the Descartados
sheet names the most decisive reason rather than an incidental one.
"""

from dataclasses import dataclass

from renov_market_scan.filtering.blacklist import (
    ACCESSORY_OR_PART,
    LIKELY_PART,
    is_contextual_part,
    is_hard_blacklisted,
)
from renov_market_scan.filtering.condition import classify_condition
from renov_market_scan.filtering.dedupe import DUPLICATE, dedupe
from renov_market_scan.filtering.evidence import PRICE_WITHOUT_EVIDENCE, price_has_evidence
from renov_market_scan.filtering.matcher import MODEL_MISMATCH, capacity_present, model_matches
from renov_market_scan.filtering.price import PRICE_ABSENT, extract_price
from renov_market_scan.models import Listing, RejectedListing

CONDITION_EXCLUDED = "condicao_excluida"
PRICE_OUT_OF_RANGE = "preco_fora_de_faixa"
CAPACITY_MISSING = "capacidade_ausente"

# 'desconhecido' is accepted: most real advert titles state no condition at all,
# and excluding them would discard the bulk of the sample and bias what survives
# toward sellers who happen to write 'usado'. Only 'novo' is excluded by default,
# because a sealed unit prices as a different market.
ACCEPTED_CONDITIONS: frozenset[str] = frozenset({"seminovo", "usado", "desconhecido"})


@dataclass(frozen=True)
class FilterContext:
    """Everything a rule needs that is not on the listing itself."""

    storage_gb: int
    manufacturer: str
    model: str
    include_new: bool
    price_floor_brl: float
    price_ceiling_brl: float


def run_pipeline(
    listings: list[Listing], context: FilterContext
) -> tuple[list[Listing], list[RejectedListing]]:
    """Apply every rule in order. Returns (accepted, rejected-with-reason)."""
    survivors: list[Listing] = []
    rejected: list[RejectedListing] = []

    for listing in listings:
        evidence = [listing.title, listing.cited_text]
        combined = f"{listing.title} {listing.cited_text}"

        if is_hard_blacklisted(listing.title):
            rejected.append(RejectedListing(listing=listing, reason=ACCESSORY_OR_PART))
            continue

        match = model_matches(context.manufacturer, context.model, listing.title)
        if not match.matches:
            rejected.append(
                RejectedListing(listing=listing, reason=match.reason or MODEL_MISMATCH)
            )
            continue

        if not capacity_present(context.storage_gb, listing.title, listing.url):
            rejected.append(RejectedListing(listing=listing, reason=CAPACITY_MISSING))
            continue

        if is_contextual_part(listing.title):
            rejected.append(RejectedListing(listing=listing, reason=LIKELY_PART))
            continue

        condition = classify_condition(listing.title)
        allowed = set(ACCEPTED_CONDITIONS)
        if context.include_new:
            allowed |= {"novo"}
        if condition not in allowed:
            rejected.append(RejectedListing(listing=listing, reason=CONDITION_EXCLUDED))
            continue

        price, price_reason = extract_price(combined)
        if price_reason is not None and price_reason != PRICE_ABSENT:
            rejected.append(RejectedListing(listing=listing, reason=price_reason))
            continue
        if price is None:
            price = listing.price_brl
        if price is None:
            rejected.append(RejectedListing(listing=listing, reason=PRICE_ABSENT))
            continue

        if not price_has_evidence(price, evidence):
            rejected.append(RejectedListing(listing=listing, reason=PRICE_WITHOUT_EVIDENCE))
            continue

        if price < context.price_floor_brl or price > context.price_ceiling_brl:
            rejected.append(RejectedListing(listing=listing, reason=PRICE_OUT_OF_RANGE))
            continue

        survivors.append(
            listing.model_copy(
                update={
                    "price_brl": price,
                    "condition": condition,
                    "flag_5g_divergent": match.flag_5g_divergent,
                }
            )
        )

    kept, duplicates = dedupe(survivors)
    rejected.extend(RejectedListing(listing=item, reason=DUPLICATE) for item in duplicates)
    return kept, rejected
