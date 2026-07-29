from renov_market_scan.filtering.text import digit_signature, normalize_text, tokens


def test_lowercases_and_strips_accents():
    assert normalize_text("Capinha SILICONE Câmera Traseira") == (
        "capinha silicone camera traseira"
    )


def test_collapses_whitespace():
    assert normalize_text("  iPhone   13\n128GB  ") == "iphone 13 128gb"


def test_plus_becomes_the_word_plus():
    assert normalize_text("MOTOROLA EDGE+ 256GB") == "motorola edge plus 256gb"


def test_gigas_is_unified_to_gb():
    assert normalize_text("Iphone 13 128 Gigas") == "iphone 13 128gb"
    assert normalize_text("128 GB") == "128gb"


def test_terabytes_expand_to_gigabytes():
    assert normalize_text("iPhone 15 Pro 1TB") == "iphone 15 pro 1024gb"
    assert normalize_text("2 TB") == "2048gb"


def test_punctuation_is_dropped_but_digits_survive():
    assert normalize_text("iPhone SE (2022) - 64GB!") == "iphone se 2022 64gb"


def test_tokens_splits_the_normalized_text():
    assert tokens("iPhone SE (2022) 64GB") == ["iphone", "se", "2022", "64gb"]


def test_digit_signature_keeps_only_digits():
    assert digit_signature("R$ 3.050,00") == "305000"
    assert digit_signature("R$ 1.234,56") == "123456"
    assert digit_signature("sem numeros") == ""
