import pytest

from renov_market_scan.config import Settings


def test_defaults_are_the_documented_ones():
    s = Settings(anthropic_api_key="sk-test")
    assert s.model == "claude-sonnet-5"
    assert s.web_search_tool_version == "web_search_20260209"
    assert s.max_uses_per_call == 3
    assert s.concurrency == 4
    assert s.price_floor_brl == 80.0
    assert s.price_ceiling_brl == 15000.0
    assert s.user_location_country == "BR"


def test_modern_tool_with_modern_model_is_accepted():
    validate_model_tool_pair("claude-sonnet-5", "web_search_20260209")
    validate_model_tool_pair("claude-opus-5", "web_search_20260318")


def test_basic_tool_is_accepted_by_any_model():
    validate_model_tool_pair("claude-haiku-4-5", "web_search_20250305")
    validate_model_tool_pair("claude-sonnet-5", "web_search_20250305")


def test_modern_tool_with_haiku_is_rejected():
    with pytest.raises(ValueError, match="web_search_20260209"):
        validate_model_tool_pair("claude-haiku-4-5", "web_search_20260209")


def test_unknown_tool_version_is_rejected():
    with pytest.raises(ValueError, match="desconhecida"):
        validate_model_tool_pair("claude-sonnet-5", "web_search_19990101")


def test_settings_validates_the_pair_on_construction():
    with pytest.raises(ValueError):
        Settings(
            anthropic_api_key="sk-test",
            model="claude-haiku-4-5",
            web_search_tool_version="web_search_20260209",
        )


def test_settings_no_longer_require_an_api_key():
    settings = Settings()  # type: ignore[call-arg]
    assert settings.model == "claude-sonnet-5"
    assert settings.concurrency == 4


def test_settings_has_no_api_key_field():
    settings = Settings()  # type: ignore[call-arg]
    assert not hasattr(settings, "anthropic_api_key")
    assert not hasattr(settings, "web_search_tool_version")
    assert not hasattr(settings, "max_uses_per_call")
