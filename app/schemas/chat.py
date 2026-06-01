from pydantic import BaseModel
from typing import Optional, List


class ChatMessage(BaseModel):
    conversation_id: Optional[str] = None
    message: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    agent_name: str = ""
