from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "AI Prompt Trainer"
    DATABASE_URL: str = "sqlite:///./prompt_trainer.db"
    SECRET_KEY: str = "super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    MAX_CONVERSATION_HISTORY: int = 20

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
