"""Application settings and validation of the model/tool-version pair."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_TOOL_VERSIONS: frozenset[str] = frozenset(
    {"web_search_20250305", "web_search_20260209", "web_search_20260318"}
)

# Versions from 20260209 onward require a model that supports dynamic filtering.
TOOL_VERSIONS_REQUIRING_MODERN_MODEL: frozenset[str] = frozenset(
    {"web_search_20260209", "web_search_20260318"}
)

MODELS_SUPPORTING_MODERN_TOOL: frozenset[str] = frozenset(
    {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
    }
)


def validate_model_tool_pair(model: str, tool_version: str) -> None:
    """Raise ValueError if the model cannot use the requested tool version."""
    if tool_version not in KNOWN_TOOL_VERSIONS:
        raise ValueError(
            f"Versao de web search tool desconhecida: {tool_version!r}. "
            f"Conhecidas: {sorted(KNOWN_TOOL_VERSIONS)}"
        )
    if (
        tool_version in TOOL_VERSIONS_REQUIRING_MODERN_MODEL
        and model not in MODELS_SUPPORTING_MODERN_TOOL
    ):
        raise ValueError(
            f"O modelo {model!r} nao suporta {tool_version!r}. "
            f"Use web_search_20250305 ou um modelo em "
            f"{sorted(MODELS_SUPPORTING_MODERN_TOOL)}"
        )


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str
    model: str = "claude-sonnet-5"
    web_search_tool_version: str = "web_search_20260209"
    max_uses_per_call: int = 3
    concurrency: int = 4
    price_floor_brl: float = 80.0
    price_ceiling_brl: float = 15000.0
    user_location_country: str = "BR"

    @model_validator(mode="after")
    def _check_model_tool_pair(self) -> "Settings":
        validate_model_tool_pair(self.model, self.web_search_tool_version)
        return self
