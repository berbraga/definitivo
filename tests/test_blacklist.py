from renov_market_scan.filtering.blacklist import is_contextual_part, is_hard_blacklisted


def test_accessories_are_rejected():
    for title in (
        "Capa capinha silicone iPhone 13",
        "Pelicula de vidro 3D iPhone 13",
        "Película Cerâmica iPhone 13",
        "Carcaça traseira iPhone 13",
        "Cabo carregador turbo 20W",
        "Fone de ouvido para iPhone",
        "Suporte veicular para celular",
        "Chip TIM 5G",
    ):
        assert is_hard_blacklisted(title) is True, title


def test_parts_and_junk_are_rejected():
    for title in (
        "Placa mae iPhone 13 com defeito",
        "Flex conector de carga iPhone 13",
        "Camera traseira original iPhone 13",
        "Alto-falante iPhone 13",
        "Botao home iPhone 7",
        "Aparelho para retirada de pecas",
        "iPhone 13 nao liga, sucata",
        "iPhone 13 sem funcionar",
        "iPhone 13 replica primeira linha",
        "Celular clone similar generico",
    ):
        assert is_hard_blacklisted(title) is True, title


def test_a_legitimate_advert_is_not_hard_blacklisted():
    title = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    assert is_hard_blacklisted(title) is False


def test_battery_health_is_exempt_from_the_contextual_rule():
    for title in (
        "iPhone 13 128GB Saude de bateria 90%",
        "iPhone 13 128GB bateria 100%",
        "iPhone 13 128gb bateria com 87% de capacidade",
        "iPhone 11 64gb 320 ciclos de bateria",
    ):
        assert is_contextual_part(title) is False, title


def test_a_battery_being_sold_is_caught_by_the_contextual_rule():
    assert is_contextual_part("Bateria original iPhone 13 nova") is True


def test_screen_size_is_exempt():
    for title in (
        "Galaxy A17 128gb tela de 6.7 polegadas",
        "Moto G84 tela amoled 120hz",
    ):
        assert is_contextual_part(title) is False, title


def test_a_screen_being_sold_is_caught():
    assert is_contextual_part("Tela display frontal iPhone 13 original") is True
    assert is_contextual_part("Display Galaxy A17 com aro") is True


def test_amoled_screen_is_caught_as_a_part():
    """An amoled screen sold as a part must not be exempted just for naming its panel technology."""
    assert is_contextual_part("Tela Amoled Original Galaxy A17 128GB") is True


def test_clean_titles_pass_both_rules():
    title = "Samsung Galaxy A17 5G 128GB usado excelente estado"
    assert is_hard_blacklisted(title) is False
    assert is_contextual_part(title) is False
