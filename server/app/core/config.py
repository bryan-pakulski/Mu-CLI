from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mu-CLI Server"
    database_url: str = "sqlite+aiosqlite:///./mu_cli.db"
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama3.1"
    provider_max_retries: int = 2

    model_config = SettingsConfigDict(env_prefix="MUCLI_")


settings = Settings()
