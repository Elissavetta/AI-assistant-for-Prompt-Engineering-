from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    message: str = Field(min_length=1)
    mode: str = "lesson"
