"""Settings read from environment variables or a .env file (see .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Works from the repo root (.env) and from backend/ (../.env)
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # Model (Gemma 4 31B on AMD Token Factory, or our own vLLM on an MI300X droplet).
    # live = call the endpoint; replay = recorded responses only; cached = replay, else live;
    # mock = fake schema-shaped answers for tests
    llm_mode: Literal["live", "replay", "cached", "mock"] = "replay"
    llm_base_url: str = "https://developer.amd.com.cn/radeon/api/v1"
    llm_api_key: SecretStr = SecretStr("")
    vlm_model: str = "Gemma-4-31B-IT"
    text_model: str = "Gemma-4-31B-IT"
    # json_object = schema in the prompt + Pydantic validation (Token Factory);
    # json_schema = guided decoding (our own vLLM)
    llm_json_mode: Literal["json_object", "json_schema"] = "json_object"
    # Token Factory allows 20 requests/min and 8 in flight per account; stay under both
    llm_rpm: int = 18
    llm_concurrency: int = 6
    llm_max_retries: int = 6  # with back-off 2-30 s: ~90 s of patience per call
    # Calls normally take 1-15 s; fail over sooner when the endpoint hangs
    llm_timeout_s: float = 90.0
    # Save every live answer so it can be replayed without the model
    llm_record: bool = True
    replay_dir: Path = Path(__file__).resolve().parents[2] / "data" / "replay"
    # Supplier photos from ABO, downloaded by data/scripts/fetch_abo.py (not committed)
    abo_images_dir: Path = Path(__file__).resolve().parents[2] / "data" / "raw" / "abo" / "images"

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
