"""Classify the advertised condition of a listing.

Precedence (four-step, strongest to weakest):
1. NEW_MARKERS — markers that unambiguously mean new (lacrado, novo na caixa, etc.)
2. SEMI_NEW_MARKERS — markers for refurbished or display units (seminovo, vitrine, etc.)
3. NEW_TOKEN — bare token 'novo' checked after seminovo, never as substring, so
   'seminovo' is not misread as 'novo'. Known limitation: a title mentioning Novo
   Hamburgo will be read as new and dropped from the sample. The opposite error
   (new phone misread as used) would put a new-phone price into used statistics,
   so this trade is deliberate.
4. USED_MARKERS — markers for used condition (usado, usada, etc.)

A title that says 'lacrado' and also 'aceito seu usado na troca' is a sealed unit,
so the strongest signal wins rather than the first one found.
"""

from renov_market_scan.filtering.text import normalize_text
from renov_market_scan.models import Condition

NEW_MARKERS: tuple[str, ...] = (
    "lacrado",
    "novo na caixa",
    "novo lacrado",
    "selado",
    "nunca usado",
    "nunca aberto",
)
SEMI_NEW_MARKERS: tuple[str, ...] = (
    "seminovo",
    "semi novo",
    "vitrine",
    "recondicionado",
    "refurbished",
)
USED_MARKERS: tuple[str, ...] = ("usado", "usada", "de segunda mao")

# Checked as a whole token, never as a substring, so 'seminovo' is not read as
# 'novo'. Checked after the seminovo markers so that 'semi novo' — two tokens —
# resolves to seminovo rather than new.
NEW_TOKEN = "novo"


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def classify_condition(title: str) -> Condition:
    """Return novo, seminovo, usado or desconhecido."""
    normalized = normalize_text(title)
    if _contains_any(normalized, NEW_MARKERS):
        return "novo"
    if _contains_any(normalized, SEMI_NEW_MARKERS):
        return "seminovo"
    if NEW_TOKEN in normalized.split():
        return "novo"
    if _contains_any(normalized, USED_MARKERS):
        return "usado"
    return "desconhecido"
