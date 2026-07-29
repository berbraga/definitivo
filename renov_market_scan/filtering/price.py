"""BRL price parsing. Installment values are never treated as cash prices."""

import re

PRICE_ABSENT = "preco_ausente"
PRICE_INSTALLMENT = "preco_parcelado"

# Only amounts explicitly marked as currency count. A bare number in a title is
# usually battery health, screen size or camera count, not a price.
_MONEY = re.compile(r"r\$\s*(\d[\d.,]*)", re.IGNORECASE)

# 'Nx', 'N x', 'Nx de', 'em Nx de' immediately before the amount.
_INSTALLMENT_PREFIX = re.compile(r"(\d+\s*x\s*(de\s*)?|parcelas?\s+de\s*)$", re.IGNORECASE)

# Appears after the amount in installment offers.
_INSTALLMENT_SUFFIX = re.compile(r"^\s*(sem\s+juros|no\s+cart[ao][eo]?)", re.IGNORECASE)

_TRAILING_SEPARATORS = ".,"


def _to_float(raw: str) -> float | None:
    """Convert a pt-BR amount body (no currency marker) to a float."""
    body = raw.strip().rstrip(_TRAILING_SEPARATORS)
    if not body:
        return None
    try:
        if "," in body:
            return float(body.replace(".", "").replace(",", "."))
        if "." in body:
            _, _, tail = body.rpartition(".")
            if len(tail) == 3:
                return float(body.replace(".", ""))
        return float(body)
    except ValueError:
        return None


def parse_brl(raw: str) -> float | None:
    """Parse the first BRL amount in the text, or None if there is none."""
    match = _MONEY.search(raw)
    if match is None:
        return None
    return _to_float(match.group(1))


def extract_price(text: str) -> tuple[float | None, str | None]:
    """Return the cash price, or None plus a rejection reason.

    Every currency-marked amount is classified as installment or cash by the
    text immediately around it. The largest cash amount wins: an installment
    value is always smaller than the total it belongs to, so taking the maximum
    of the surviving candidates picks the total price.
    """
    cash: list[float] = []
    saw_installment = False

    for match in _MONEY.finditer(text):
        value = _to_float(match.group(1))
        if value is None:
            continue
        before = text[max(0, match.start() - 24) : match.start()]
        after = text[match.end() : match.end() + 24]
        if _INSTALLMENT_PREFIX.search(before) or _INSTALLMENT_SUFFIX.match(after):
            saw_installment = True
            continue
        cash.append(value)

    if cash:
        return max(cash), None
    if saw_installment:
        return None, PRICE_INSTALLMENT
    return None, PRICE_ABSENT
