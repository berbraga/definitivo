"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    model: str = "claude-sonnet-5"
    concurrency: int = 4
    price_floor_brl: float = 80.0
    price_ceiling_brl: float = 15000.0
    user_location_country: str = "BR"
