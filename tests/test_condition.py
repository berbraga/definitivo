from renov_market_scan.filtering.condition import classify_condition


def test_new_is_detected():
    for title in (
        "iPhone 13 128GB lacrado na caixa",
        "iPhone 13 novo na caixa nota fiscal",
        "Galaxy A17 128gb NOVO LACRADO",
    ):
        assert classify_condition(title) == "novo", title


def test_semi_new_is_detected():
    for title in (
        "iPhone 13 128GB seminovo",
        "iPhone 13 Semi Novo 128gb",
        "Galaxy S23 vitrine 256gb",
        "iPhone 12 recondicionado 64gb",
    ):
        assert classify_condition(title) == "seminovo", title


def test_used_is_detected():
    for title in (
        "iPhone 13 128GB usado",
        "Galaxy A17 usado excelente estado",
    ):
        assert classify_condition(title) == "usado", title


def test_unknown_when_nothing_says_so():
    assert classify_condition("iPhone 13 128GB Branco R$ 3.050,00") == "desconhecido"


def test_new_wins_over_used_when_both_appear():
    """'novo na caixa' plus 'usado' in the same title: lacrado is decisive."""
    assert classify_condition("iPhone 13 lacrado, aceito seu usado na troca") == "novo"


def test_seminovo_wins_over_usado():
    assert classify_condition("iPhone 13 seminovo, pouco usado") == "seminovo"


def test_bare_novo_token_is_recognized():
    """Bare 'novo' token (not 'novo na caixa' or similar) is detected when present."""
    assert classify_condition("iPhone 13 128GB novo, nunca usado") == "novo"
    assert classify_condition("iPhone 13 novo") == "novo"


def test_bare_novo_token_does_not_swallow_seminovo():
    """The bare 'novo' token must be checked after seminovo markers.

    'Semi Novo' splits into ['semi', 'novo'] tokens, and 'novo' alone is the last
    step of the precedence. If bare 'novo' were checked first, 'semi novo' would
    be misread as new instead of seminovo.
    """
    assert classify_condition("iPhone 13 Semi Novo 128gb") == "seminovo"
    assert classify_condition("iPhone 13 128gb seminovo, pouco usado") == "seminovo"


def test_quase_novo_and_mais_novo_are_not_new():
    """Qualified 'novo' tokens must not read as new condition.

    'quase novo' (almost new), 'como novo' (like new), and 'praticamente novo'
    (practically new) are the most common ways a Brazilian seller describes
    a well-kept used phone. 'mais novo' appears in trade language describing what
    the seller wants, not what they are selling. All must be rejected when bare
    'novo' is the only signal.
    """
    assert classify_condition("iPhone 13 quase novo") == "seminovo"
    assert classify_condition("iPhone 12 usado, aceito troca por um mais novo") == "usado"
    assert classify_condition("iPhone 13 praticamente novo") == "seminovo"
    assert classify_condition("iPhone 12 usado, esta como novo") == "seminovo"
