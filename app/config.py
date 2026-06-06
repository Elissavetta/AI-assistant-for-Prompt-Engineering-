from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    APP_NAME: str = "PROMPT UP"
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'prompt_trainer.db'}"
    SECRET_KEY: str = "super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "DeepSeek-V4-Pro"
    LLM_BASE_URL: str = "https://api.ai.beeline.ru/api/v3"
    LLM_SSL_VERIFY: bool = True
    LLM_CERT_PATH: str = ""
    LLM_MAX_TOKENS: int = 800
    MAX_CONVERSATION_HISTORY: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
