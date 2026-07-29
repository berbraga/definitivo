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
