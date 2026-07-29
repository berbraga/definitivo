"""Text normalization primitives shared by every filtering rule.

Every rule compares normalized text, never raw text, so that accents,
punctuation and capacity spelling cannot cause a false mismatch.
"""

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_GIGAS = re.compile(r"(?<![a-z])gigas?\b")
_CAPACITY_SPACING = re.compile(r"(\d+)\s*(gb|tb)\b")
_ONE_TB = re.compile(r"\b1tb\b")
_TWO_TB = re.compile(r"\b2tb\b")
_DIGITS = re.compile(r"\D+")


def normalize_text(text: str) -> str:
    """Lowercase, de-accent, unify capacity spelling, drop punctuation.

    '+' becomes the word 'plus' so that EDGE+ and EDGE PLUS compare equal.
    1TB and 2TB expand to 1024gb and 2048gb so that a title using either
    spelling matches a target stored in gigabytes.
    """
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plussed = unaccented.replace("+", " plus ")
    cleaned = _NON_ALNUM.sub(" ", plussed)
    unified = _GIGAS.sub("gb", cleaned)
    unified = _CAPACITY_SPACING.sub(r"\1\2", unified)
    unified = _ONE_TB.sub("1024gb", unified)
    unified = _TWO_TB.sub("2048gb", unified)
    return " ".join(unified.split())


def tokens(text: str) -> list[str]:
    """Normalized whitespace-separated tokens."""
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def digit_signature(text: str) -> str:
    """Every digit in the text, in order. Used to compare prices to evidence."""
    return _DIGITS.sub("", text)
