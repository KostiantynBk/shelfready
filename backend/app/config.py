"""Settings read from environment variables or a .env file (see .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Works from the repo root (.env) and from backend/ (../.env)
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # Model: live = vLLM endpoint, replay = recorded responses, mock = fake answers for tests
    llm_mode: Literal["live", "replay", "mock"] = "replay"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    vlm_model: str = "gemma4"
    text_model: str = "gemma4"

    # Evolus
    evolus_api_base: str = "https://api.evolus.ai/api/v1"
    evolus_api_key: SecretStr = SecretStr("")
    evolus_app_id: str = ""
    evolus_extractor_id: str = ""
    evolus_workflow_id: str = ""
    evolus_library_rules_id: str = ""
    evolus_library_products_id: str = ""
    webhook_secret: SecretStr = SecretStr("")

    # App
    public_base_url: str = "http://localhost:8080"
    languages: str = "en,es,it,de,fr,pt,pl,uk"
    gpu_hourly_usd: float = 1.99
    data_dir: Path = Path("./data/runtime")

    @property
    def language_list(self) -> list[str]:
        return [code.strip() for code in self.languages.split(",") if code.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
