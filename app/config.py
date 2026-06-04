import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "PROMPT UP"
    DATABASE_URL: str = "sqlite:///./prompt_trainer.db"
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

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""

    @model_validator(mode="after")
    def migrate_legacy_env(self):
        if not self.LLM_API_KEY:
            if self.OPENAI_API_KEY:
                object.__setattr__(self, "LLM_API_KEY", self.OPENAI_API_KEY)
            elif os.environ.get("BEELINE_AI_API_KEY"):
                object.__setattr__(self, "LLM_API_KEY", os.environ["BEELINE_AI_API_KEY"])
        if self.LLM_MODEL == "Qwen3.5-35B-A3B" and self.OPENAI_MODEL:
            object.__setattr__(self, "LLM_MODEL", self.OPENAI_MODEL)
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
