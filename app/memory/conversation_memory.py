from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message
from app.config import settings


def create_conversation(db: Session, user_id: str, title: str = "New Conversation") -> Conversation:
    conv = Conversation(user_id=user_id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversation(db: Session, conversation_id: str) -> Conversation | None:
    return db.query(Conversation).filter(Conversation.id == conversation_id).first()


def get_user_conversations(db: Session, user_id: str) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .all()
    )


def add_message(db: Session, conversation_id: str, role: str, content: str, agent_name: str = "") -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        agent_name=agent_name,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_conversation_messages(db: Session, conversation_id: str, limit: int = None) -> list[Message]:
    query = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


def messages_to_openai_format(messages: list[Message]) -> list[dict]:
    result = []
    for msg in messages[-settings.MAX_CONVERSATION_HISTORY:]:
        result.append({
            "role": msg.role,
            "content": msg.content,
        })
    return result
