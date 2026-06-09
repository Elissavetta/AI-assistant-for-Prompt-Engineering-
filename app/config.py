import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger("prompt_trainer")

MIN_SUBMISSION_LENGTH = 30
MODULE_COMPLETION_SCORE = 50
EVALUATOR_MAX_TOKENS = 600
EVALUATOR_TEMPERATURE = 0.3
PROFILER_MAX_TURNS = 5
MAX_CLARIFICATION_ROUNDS = 2
SESSION_TTL_SECONDS = 1800
MAX_CONVERSATION_HISTORY = 20

MARKER_AWAITING_ANSWER = "[ОЖИДАЕТСЯ ОТВЕТ]"
MARKER_AWAITING_CHOICE = "[ОЖИДАЕТСЯ ВЫБОР]"
MARKER_AWAITING_CLARIFICATION = "[ОЖИДАЕТСЯ УТОЧНЕНИЕ]"
MARKER_LEVEL = "УРОВЕНЬ:"

LEVEL_NEWBIE = "newbie"
LEVEL_INTERMEDIATE = "intermediate"
LEVEL_ADVANCED = "advanced"


class Settings(BaseSettings):
    APP_NAME: str = "PROMPT UP"
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'prompt_trainer.db'}"
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "DeepSeek-V4-Pro"
    LLM_BASE_URL: str = "https://api.ai.beeline.ru/api/v3"
    LLM_SSL_VERIFY: bool = True
    LLM_CERT_PATH: str = ""
    LLM_MAX_TOKENS: int = 800
    MAX_CONVERSATION_HISTORY: int = MAX_CONVERSATION_HISTORY
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    LLM_RETRY_ATTEMPTS: int = 3
    LLM_RETRY_BACKOFF: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def model_post_init(self, __context) -> None:
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be set via environment variable or .env file")


settings = Settings()
