"""Classify the advertised condition of a listing.

Precedence is novo, then seminovo, then usado. A title that says 'lacrado' and
also 'aceito seu usado na troca' is a sealed unit, so the strongest signal wins
rather than the first one found.
"""

from renov_market_scan.filtering.text import normalize_text
from renov_market_scan.models import Condition

NEW_MARKERS: tuple[str, ...] = ("lacrado", "novo na caixa", "novo lacrado", "selado")
SEMI_NEW_MARKERS: tuple[str, ...] = (
    "seminovo",
    "semi novo",
    "vitrine",
    "recondicionado",
    "refurbished",
)
USED_MARKERS: tuple[str, ...] = ("usado", "usada", "de segunda mao")


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def classify_condition(title: str) -> Condition:
    """Return novo, seminovo, usado or desconhecido."""
    normalized = normalize_text(title)
    if _contains_any(normalized, NEW_MARKERS):
        return "novo"
    if _contains_any(normalized, SEMI_NEW_MARKERS):
        return "seminovo"
    if _contains_any(normalized, USED_MARKERS):
        return "usado"
    return "desconhecido"
