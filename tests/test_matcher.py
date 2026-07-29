from pathlib import Path

from renov_market_scan.filtering.matcher import (
    MODEL_MISMATCH,
    capacity_present,
    load_brand_aliases,
    model_matches,
    qualifiers_in,
    years_in,
)


def test_qualifiers_are_detected():
    assert qualifiers_in("iPhone 13 Pro Max") == {"pro", "max"}
    assert qualifiers_in("iPhone 13") == set()
    assert qualifiers_in("Galaxy S23 Ultra") == {"ultra"}
    assert qualifiers_in("MOTOROLA EDGE+") == {"plus"}
    assert qualifiers_in("11 LITE 5G NE") == {"lite", "ne"}
    assert qualifiers_in("EDGE 5G UW") == {"uw"}
    assert qualifiers_in("EDGE 70 BY SWAROVSK") == {"swarovski"}


def test_five_g_is_not_a_strict_qualifier():
    assert "5g" not in qualifiers_in("Galaxy A17 5G")


def test_years_are_detected_only_in_plausible_range():
    assert years_in("iPhone SE (2022)") == {"2022"}
    assert years_in("iPhone 13 128GB") == set()
    assert years_in("Moto 1024gb") == set()


def test_pro_max_is_rejected_for_a_plain_target():
    result = model_matches("APPLE", "IPHONE 13", "iPhone 13 Pro Max 128GB Gold - seminovo")
    assert result.matches is False
    assert result.reason == MODEL_MISMATCH


def test_plain_is_rejected_for_a_pro_target():
    result = model_matches("APPLE", "IPHONE 13 PRO", "iPhone 13 128GB seminovo")
    assert result.matches is False


def test_ultra_is_rejected_for_a_plain_galaxy_target():
    result = model_matches("SAMSUNG", "GALAXY S23", "Samsung Galaxy S23 Ultra 256gb Usado")
    assert result.matches is False


def test_exact_model_is_accepted():
    result = model_matches("APPLE", "IPHONE 13", "iPhone 13 128GB seminovo bateria 90%")
    assert result.matches is True
    assert result.flag_5g_divergent is False
    assert result.reason is None


def test_five_g_mismatch_is_tolerated_and_flagged():
    result = model_matches("SAMSUNG", "GALAXY A17", "Samsung Galaxy A17 5G 128GB usado")
    assert result.matches is True
    assert result.flag_5g_divergent is True


def test_five_g_target_accepts_a_title_without_it_and_flags():
    result = model_matches("SAMSUNG", "GALAXY A17 5G", "Samsung Galaxy A17 128GB usado")
    assert result.matches is True
    assert result.flag_5g_divergent is True


def test_five_g_on_both_sides_is_not_flagged():
    result = model_matches("SAMSUNG", "GALAXY A17 5G", "Galaxy A17 5G 128gb")
    assert result.matches is True
    assert result.flag_5g_divergent is False


def test_target_year_is_mandatory_when_present():
    good = model_matches("APPLE", "IPHONE SE (2022)", "iPhone SE 2022 64GB seminovo")
    bad = model_matches("APPLE", "IPHONE SE (2022)", "iPhone SE 2020 64GB seminovo")
    assert good.matches is True
    assert bad.matches is False


def test_grade_suffix_in_the_target_is_ignored():
    result = model_matches("SAMSUNG", "GALAXY A17 A0", "Galaxy A17 128gb")
    assert result.matches is True


def test_different_model_number_is_rejected():
    result = model_matches("SAMSUNG", "GALAXY A17", "Samsung Galaxy A15 128GB")
    assert result.matches is False


def test_different_device_entirely_is_rejected():
    result = model_matches("MOTOROLA", "MOTO G17", "Realme C63 128GB usado")
    assert result.matches is False


def test_edge_plus_matches_the_written_out_form():
    result = model_matches("MOTOROLA", "EDGE+", "Motorola Edge Plus 256gb")
    assert result.matches is True


def test_capacity_found_in_the_title():
    assert capacity_present(128, "iPhone 13 128GB seminovo", "https://x") is True
    assert capacity_present(128, "iPhone 13 128 GB seminovo", "https://x") is True
    assert capacity_present(128, "iPhone 13 128 Gigas", "https://x") is True


def test_capacity_found_only_in_the_url():
    assert capacity_present(
        128, "iPhone 13 seminovo", "https://www.olx.com.br/celulares/apple/iphone-13/128gb"
    ) is True


def test_terabyte_target_matches_a_tb_spelled_title():
    assert capacity_present(1024, "iPhone 15 Pro 1TB", "https://x") is True
    assert capacity_present(1024, "iPhone 15 Pro 1024GB", "https://x") is True


def test_missing_capacity_is_detected():
    assert capacity_present(128, "iPhone 13 seminovo", "https://olx.com.br/iphone-13") is False


def test_wrong_capacity_is_detected():
    assert capacity_present(128, "iPhone 13 256GB seminovo", "https://x") is False


def test_brand_aliases_load_from_yaml():
    aliases = load_brand_aliases(Path("marcas.yaml"))
    assert "xiaomi redmi" in aliases["REDMI"]
    assert aliases["JOVI"] == ["vivo", "jovi"]
