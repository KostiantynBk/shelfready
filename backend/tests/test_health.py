from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_healthz_ok():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_mode"] in {"live", "replay", "mock"}


def test_settings_parse_languages_and_hide_secrets():
    settings = Settings(_env_file=None, languages="en, es ,uk", evolus_api_key="secret-value")
    assert settings.language_list == ["en", "es", "uk"]
    assert "secret-value" not in repr(settings)
