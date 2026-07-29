from renov_market_scan.filtering.price import (
    PRICE_ABSENT,
    PRICE_INSTALLMENT,
    extract_price,
    parse_brl,
)


def test_parses_thousands_and_decimals():
    assert parse_brl("R$ 1.234,56") == 1234.56


def test_parses_thousands_without_decimals():
    assert parse_brl("R$ 1.190,00") == 1190.0
    assert parse_brl("R$ 3.050,00") == 3050.0


def test_parses_dot_as_thousands_separator_when_three_digits_follow():
    assert parse_brl("R$ 1.190") == 1190.0


def test_parses_a_bare_integer():
    assert parse_brl("R$ 3050") == 3050.0


def test_returns_none_without_the_currency_marker():
    assert parse_brl("3050") is None
    assert parse_brl("Saude de bateria 90%") is None


def test_real_olx_title_yields_the_cash_price():
    title = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    price, reason = extract_price(title)
    assert price == 3050.0
    assert reason is None


def test_installment_alone_is_rejected():
    price, reason = extract_price("Galaxy S23 256GB 12x R$ 199,90 sem juros")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_de_is_rejected():
    price, reason = extract_price("iPhone 12 em 12x de R$ 254,17")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_space_before_x_is_rejected():
    price, reason = extract_price("Moto G84 10 x R$ 99,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_cash_price_wins_when_both_appear():
    title = "iPhone 13 128GB R$ 3.050,00 ou 12x R$ 254,17 sem juros"
    price, reason = extract_price(title)
    assert price == 3050.0
    assert reason is None


def test_absent_price_is_reported_as_absent_not_installment():
    price, reason = extract_price("iPhone 13 128GB seminovo, tratar por telefone")
    assert price is None
    assert reason == PRICE_ABSENT


def test_parcelas_de_phrasing_is_rejected():
    price, reason = extract_price("Redmi Note 12 parcelas de R$ 120,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_connector_words_before_amount():
    """Connector words like 'sem juros de' between count and amount must be recognized."""
    price, reason = extract_price("iPhone 13 128GB em ate 12x sem juros de R$ 254,17")
    assert price is None
    assert reason == PRICE_INSTALLMENT

    price, reason = extract_price("iPhone 13 128GB em até 12x sem juros de R$ 254,17")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_cartao_variants():
    """Both accented and unaccented 'cartão' variants must be recognized."""
    price, reason = extract_price("iPhone 13 128GB 10x no cartão de R$ 199,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT

    price, reason = extract_price("iPhone 13 128GB 10x no cartao de R$ 199,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_parcelado_phrasing_is_rejected():
    """'Parcelado' without explicit count must still be recognized as installment."""
    price, reason = extract_price("Redmi Note 12 parcelado sem juros R$ 120,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_high_digit_count_is_not_installment():
    """Camera specs like 'Space Zoom 100x' must not be treated as installment counts."""
    price, reason = extract_price("Samsung Galaxy S23 Ultra 256GB Space Zoom 100x R$ 4.500,00")
    assert price == 4500.0
    assert reason is None


def test_cash_price_with_sem_juros_suffix_is_not_rejected():
    """'sem juros' after a cash price without preceding installment count is not rejection."""
    price, reason = extract_price("iPhone 13 128GB R$ 3.050,00 sem juros no cartão")
    assert price == 3050.0
    assert reason is None


def test_cash_price_wins_over_installment_with_connectors():
    """Cash price must win even when installment has connector words."""
    title = "iPhone 13 128GB em 12x sem juros de R$ 254,17 ou R$ 2.900,00 a vista"
    price, reason = extract_price(title)
    assert price == 2900.0
    assert reason is None
