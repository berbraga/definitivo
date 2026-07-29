"""BRL price parsing. Installment values are never treated as cash prices."""

import re

PRICE_ABSENT = "preco_ausente"
PRICE_INSTALLMENT = "preco_parcelado"

# Only amounts explicitly marked as currency count. A bare number in a title is
# usually battery health, screen size or camera count, not a price.
_MONEY = re.compile(r"r\$\s*(\d[\d.,]*)", re.IGNORECASE)

# Connector words that may sit between an installment count and the amount.
# Accented forms are spelled out because this module reads raw advert text.
_CONNECTORS = r"(?:de|em|sem|s/|juros|no|nos|na|ate|at[eé]|vezes|cart[aã]o|cart[oõ]es|parcelas?)"

# 'Nx', 'N x', 'Nx de', 'em até Nx sem juros de', 'Nx no cartão de', 'parcelas de',
# 'parcelado sem juros' — a count (or an explicit installment word) followed only by
# connectors, immediately before the amount. The count is capped at two digits so that
# a camera spec like 'Space Zoom 100x' is not read as an installment count.
_INSTALLMENT_PREFIX = re.compile(
    rf"(?:(?<!\d)\d{{1,2}}\s*x|parcelas?\s+de|parcelad[oa]s?)\s*(?:{_CONNECTORS}\s*)*$",
    re.IGNORECASE,
)

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

    Where phrasing is genuinely ambiguous (e.g. '12x sem juros R$ 3.050,00'),
    classification errs toward rejecting the amount as an installment. A
    rejection is recorded with its reason and stays auditable; an installment
    accepted as a cash price silently corrupts the statistics.
    """
    cash: list[float] = []
    saw_installment = False

    for match in _MONEY.finditer(text):
        value = _to_float(match.group(1))
        if value is None:
            continue
        before = text[max(0, match.start() - 40) : match.start()]
        if _INSTALLMENT_PREFIX.search(before):
            saw_installment = True
            continue
        cash.append(value)

    if cash:
        return max(cash), None
    if saw_installment:
        return None, PRICE_INSTALLMENT
    return None, PRICE_ABSENT
