from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "PROMPT UP"
    DATABASE_URL: str = "sqlite:///./prompt_trainer.db"
    SECRET_KEY: str = "super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "qwen-medium"
    LLM_BASE_URL: str = "https://api.ai.beeline.ru/api/v3"
    LLM_SSL_VERIFY: bool = True
    LLM_CERT_PATH: str = ""
    LLM_MAX_TOKENS: int = 800
    MAX_CONVERSATION_HISTORY: int = 10

    @field_validator("LLM_API_KEY", mode="before")
    @classmethod
    def migrate_api_key(cls, v, info):
        if not v and info.data.get("OPENAI_API_KEY"):
            return info.data["OPENAI_API_KEY"]
        return v

    @field_validator("LLM_MODEL", mode="before")
    @classmethod
    def migrate_model(cls, v, info):
        if not v and info.data.get("OPENAI_MODEL"):
            return info.data["OPENAI_MODEL"]
        return v

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
