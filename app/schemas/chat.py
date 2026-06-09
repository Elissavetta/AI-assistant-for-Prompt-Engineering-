from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    mode: str = "lesson"
