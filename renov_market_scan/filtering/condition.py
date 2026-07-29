"""Classify the advertised condition of a listing.

Precedence (four-step, strongest to weakest):
1. NEW_MARKERS — markers that unambiguously mean new (lacrado, novo na caixa, etc.)
2. SEMI_NEW_MARKERS — markers for refurbished or display units (seminovo, vitrine,
   quase novo, etc.)
3. NEW_TOKEN — bare token 'novo' checked after seminovo, never as substring, and
   only when unqualified. 'quase novo' and 'mais novo' do not read as new.
4. USED_MARKERS — markers for used condition (usado, usada, etc.)

A title that says 'lacrado' and also 'aceito seu usado na troca' is a sealed unit,
so the strongest signal wins rather than the first one found.

Known limitations, both deliberate trade-offs:
- A title mentioning the city 'Novo Hamburgo' will be read as new and dropped
  from the sample. The opposite error (new phone misread as used) would put a
  new-phone price into used statistics and inflate the maximum, so this loses
  fewer listings.
- A title like 'iPhone 12 usado, troco por iPhone novo' still resolves to novo
  (the new phone wins). This is deliberate: the legitimate mirror case 'iPhone 13
  novo, aceito seu usado na troca' is a new phone accepting a used trade-in and
  must stay novo. Letting an explicit used marker override an unqualified novo
  token would break that case.
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
    "quase novo",
    "como novo",
    "praticamente novo",
    "vitrine",
    "recondicionado",
    "refurbished",
)
USED_MARKERS: tuple[str, ...] = ("usado", "usada", "de segunda mao")

# Qualifiers that prevent a 'novo' token from indicating new condition.
_NOVO_QUALIFIERS: frozenset[str] = frozenset({"mais", "quase", "como", "praticamente"})

# Checked as a whole token, never as a substring, so 'seminovo' is not read as
# 'novo'. Checked after the seminovo markers so that 'semi novo' — two tokens —
# resolves to seminovo rather than new.
NEW_TOKEN = "novo"


def _has_new_token(title_tokens: list[str]) -> bool:
    """True when 'novo' appears unqualified as a whole token."""
    return any(
        token == NEW_TOKEN and (index == 0 or title_tokens[index - 1] not in _NOVO_QUALIFIERS)
        for index, token in enumerate(title_tokens)
    )


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def classify_condition(title: str) -> Condition:
    """Return novo, seminovo, usado or desconhecido."""
    normalized = normalize_text(title)
    if _contains_any(normalized, NEW_MARKERS):
        return "novo"
    if _contains_any(normalized, SEMI_NEW_MARKERS):
        return "seminovo"
    if _has_new_token(normalized.split()):
        return "novo"
    if _contains_any(normalized, USED_MARKERS):
        return "usado"
    return "desconhecido"
