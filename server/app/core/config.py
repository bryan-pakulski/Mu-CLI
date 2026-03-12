import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mu-CLI Server"
    database_url: str = "sqlite+aiosqlite:///./mu_cli.db"
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama3.1"
    provider_max_retries: int = 2
    test_mode: bool = False
    workspace_index_refresh_interval_s: int = 300

    model_config = SettingsConfigDict(env_prefix="MUCLI_")


settings = Settings()


def is_test_mode_enabled() -> bool:
    raw = os.getenv("MUCLI_TEST_MODE")
    if raw is None:
        return settings.test_mode
    return raw.strip().lower() in {"1", "true", "yes", "on"}
