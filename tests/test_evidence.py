from renov_market_scan.filtering.evidence import price_has_evidence


def test_price_present_verbatim_in_the_cited_text():
    evidence = ["iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica"]
    assert price_has_evidence(3050.0, evidence) is True


def test_price_present_with_a_different_separator_style():
    assert price_has_evidence(3050.0, ["Vendo por R$ 3050"]) is True
    assert price_has_evidence(1234.56, ["preco R$ 1.234,56 a vista"]) is True


def test_integer_price_matches_a_two_decimal_rendering():
    assert price_has_evidence(1190.0, ["R$ 1.190,00 no pix"]) is True


def test_price_absent_from_every_evidence_text_is_rejected():
    evidence = ["iPhone 13 128GB Branco, tratar por telefone", "Celulares APPLE no Brasil"]
    assert price_has_evidence(3050.0, evidence) is False


def test_a_different_price_in_the_evidence_does_not_count():
    assert price_has_evidence(3050.0, ["R$ 2.300,00 a vista"]) is False


def test_empty_evidence_is_rejected():
    assert price_has_evidence(3050.0, []) is False
    assert price_has_evidence(3050.0, ["", "   "]) is False


def test_price_must_be_whole_number_not_fragment():
    """Prices must appear as separate numbers, not fragments of larger ones."""
    # 3050 sits inside the phone number
    assert price_has_evidence(3050.0, ["iPhone 13 128GB R$ 2.800,00 zap 11930501234"]) is False
    # 305000 sits inside "1305000" from "R$ 13.050,00"
    assert price_has_evidence(3050.0, ["R$ 13.050,00"]) is False
