
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Moroccan Markets AI Analyst"
    app_env: str = "development"
    debug: bool = True

    data_source: str = "local"
    raw_data_dir: Path = Path("data/raw")
    processed_data_dir: Path = Path("data/processed")

    market_api_key: str | None = Field(default=None)


settings = Settings()
