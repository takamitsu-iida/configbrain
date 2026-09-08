from app.settings import Settings


def test_settings_use_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "ConfigBrain"
    assert settings.qdrant_url is None
    assert settings.qdrant_path == "data/qdrant"
    assert settings.html_qdrant_collection == "configbrain_html_documents"
    assert settings.openai_api_key is None
