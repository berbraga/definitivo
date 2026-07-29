"""Verify a reported price against the API's own cited text.

Only the model reads search-result content; the client receives just URLs,
titles and encrypted content. The citations, however, carry up to 150 verbatim
characters. Requiring the reported price to appear in that verbatim text turns
'the model said R$ 3.050' into 'the API cited the text containing R$ 3.050'.
This is the defence for the minimum and maximum, which are the two numbers the
report exists to produce and the two most exposed to a fabricated value.
"""

import re

from renov_market_scan.filtering.text import digit_signature

PRICE_WITHOUT_EVIDENCE = "preco_sem_evidencia"

# Only currency-marked amounts count as evidence of a price. A bare number in
# advert text is as likely to be a sales count, a date or a phone fragment — the
# same reason filtering/price.py requires the R$ marker when parsing. Each amount
# is signed on its own, because concatenating every digit in the text would let an
# unrelated run contain the price and pass as evidence for it.
# Note: a citation stating the price with no currency marker at all (e.g., "Vendo por 3050")
# is now rejected and the listing is discarded with PRICE_WITHOUT_EVIDENCE. That is the
# safe direction, since accepting it would mean trusting the model's number with no
# independent check.
_MONEY_NUMBER = re.compile(
    r"(?:r\$\s*(\d[\d.,]*))|(?:(\d[\d.,]*)\s*reais)", re.IGNORECASE
)


def _candidate_signatures(price_brl: float) -> set[str]:
    """Digit signatures a human would write for this amount.

    3050.0 can appear as '3.050,00' (305000) or as '3050' (3050), so both
    renderings are accepted.
    """
    with_cents = f"{price_brl:.2f}"
    signatures = {digit_signature(with_cents)}
    if price_brl == int(price_brl):
        signatures.add(digit_signature(str(int(price_brl))))
    return {signature for signature in signatures if signature}


def price_has_evidence(price_brl: float, evidence_texts: list[str]) -> bool:
    """True when some number written in an evidence text is exactly this price."""
    signatures = _candidate_signatures(price_brl)
    if not signatures:
        return False
    return any(
        digit_signature(match.group(1) or match.group(2)) in signatures
        for text in evidence_texts
        for match in _MONEY_NUMBER.finditer(text)
    )
