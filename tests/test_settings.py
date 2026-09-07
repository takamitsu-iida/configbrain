from app.settings import Settings


def test_settings_use_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "ConfigBrain"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.openai_api_key is None
