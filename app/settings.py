from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ConfigBrain"
    environment: str = "development"
    qdrant_url: str | None = None
    qdrant_path: str = "data/qdrant"
    qdrant_collection: str = "configbrain_documents"
    html_qdrant_collection: str = "configbrain_html_documents"
    openai_api_key: str | None = Field(default=None, repr=False)
    embedding_model: str = "text-embedding-3-large"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
