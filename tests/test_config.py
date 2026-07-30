from renov_market_scan.config import Settings


def test_defaults_are_the_documented_ones():
    s = Settings()
    assert s.model == "claude-sonnet-5"
    assert s.concurrency == 4
    assert s.price_floor_brl == 80.0
    assert s.price_ceiling_brl == 15000.0
    assert s.user_location_country == "BR"


def test_settings_no_longer_require_an_api_key():
    settings = Settings()  # type: ignore[call-arg]
    assert settings.model == "claude-sonnet-5"
    assert settings.concurrency == 4


def test_settings_has_no_api_key_field():
    settings = Settings()  # type: ignore[call-arg]
    assert not hasattr(settings, "anthropic_api_key")
    assert not hasattr(settings, "web_search_tool_version")
    assert not hasattr(settings, "max_uses_per_call")
