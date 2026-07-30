"""Strict model matching and capacity verification.

The qualifier set present in the advert title must equal the qualifier set of
the target model. That single rule is what keeps a Pro Max out of a plain
iPhone 13 sample, and a plain S23 out of an S23 Ultra sample.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from renov_market_scan.filtering.text import normalize_text, tokens

MODEL_MISMATCH = "modelo_divergente"

# Qualifiers that change the device, and therefore the price. 'ne', 'uw' and
# 'swarovski' were added after measuring the real sheet.
QUALIFIERS: frozenset[str] = frozenset(
    {
        "pro",
        "max",
        "plus",
        "mini",
        "ultra",
        "neo",
        "fusion",
        "lite",
        "fe",
        "se",
        "power",
        "play",
        "air",
        "ne",
        "uw",
        "swarovski",
    }
)

# 5g is deliberately outside the strict set: in the source sheet the A17 and
# A17 5G rows carry an identical price, so splitting them halves the sample for
# no gain. The divergence is recorded on the listing instead.
TOLERATED_VARIANT = "5g"

_YEAR = re.compile(r"^20[0-2]\d$")
_GRADE_TOKEN = "a0"

# 'BY SWAROVSK' is spelled truncated in the sheet. One pass, so that an
# already-normalized 'swarovski' is not extended into 'swarovskii'.
_SWAROVSKI = re.compile(r"\b(?:by\s+)?swarovsk\w*")


@dataclass(frozen=True)
class MatchResult:
    """Outcome of comparing an advert title against a target model."""

    matches: bool
    flag_5g_divergent: bool = False
    reason: str | None = None


def _canonical(text: str) -> str:
    """Normalize and fold the Swarovski spellings into one token."""
    return _SWAROVSKI.sub("swarovski", normalize_text(text))


def qualifiers_in(text: str) -> set[str]:
    """The set of price-changing qualifiers present in the text."""
    return {token for token in _canonical(text).split() if token in QUALIFIERS}


def _qualifiers_near(text: str, anchors: set[str]) -> set[str]:
    """Qualifiers immediately adjacent to an anchor token.

    A title's free text can mention an unrelated qualifier far from the
    model name itself (an advert offering trade-ins: "aceito Pro Max na
    troca"). Scoping the scan to tokens next to the model name's own core
    tokens avoids reading that kind of mention as the advert's own model.
    """
    title_tokens = _canonical(text).split()
    found: set[str] = set()
    for index, token in enumerate(title_tokens):
        if token not in QUALIFIERS:
            continue
        before = title_tokens[index - 1] if index > 0 else None
        after = title_tokens[index + 1] if index + 1 < len(title_tokens) else None
        if before in anchors or after in anchors:
            found.add(token)
    return found


def years_in(text: str) -> set[str]:
    """Four-digit years in a plausible model-year range."""
    return {token for token in tokens(text) if _YEAR.match(token)}


def _core_tokens(text: str) -> set[str]:
    """Model-identifying tokens: not qualifiers, not years, not grade codes."""
    result: set[str] = set()
    for token in _canonical(text).split():
        if token in QUALIFIERS or token in {TOLERATED_VARIANT, _GRADE_TOKEN}:
            continue
        if _YEAR.match(token):
            continue
        result.add(token)
    return result


def model_matches(manufacturer: str, model: str, title: str) -> MatchResult:
    """Compare an advert title against the target model.

    The manufacturer is not required to appear in the title: real OLX adverts
    write 'iPhone 13 128GB' with no 'Apple'. The domain filter and the query
    already constrain the brand.
    """
    target_core = _core_tokens(model)
    title_core = _core_tokens(title)
    if not target_core.issubset(title_core):
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_qualifiers = qualifiers_in(model)
    title_qualifiers = _qualifiers_near(title, target_core)
    if target_qualifiers != title_qualifiers:
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_years = years_in(model)
    if target_years and not target_years.issubset(years_in(title)):
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_has_5g = TOLERATED_VARIANT in _canonical(model).split()
    title_has_5g = TOLERATED_VARIANT in _canonical(title).split()
    return MatchResult(matches=True, flag_5g_divergent=target_has_5g != title_has_5g)


def capacity_present(storage_gb: int, title: str, url: str) -> bool:
    """True when the exact capacity appears in the title or in the URL.

    Capacity moves the price too much to infer, so an advert that does not state
    it is discarded rather than assumed.
    """
    pattern = rf"(?<!\d){storage_gb}gb\b"
    return bool(
        re.search(pattern, normalize_text(title)) or re.search(pattern, normalize_text(url))
    )


def load_brand_aliases(path: Path) -> dict[str, list[str]]:
    """Load the manufacturer alias map used when building queries."""
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return {str(key): [str(item) for item in value] for key, value in loaded.items()}
