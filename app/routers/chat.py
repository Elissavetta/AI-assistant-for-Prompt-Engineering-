import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.models.assignment import Assignment, Submission
from app.models.progress import Progress
from app.schemas.chat import ChatMessage, MessageOut
from app.services.auth_service import decode_access_token
from app.services.scoring_service import calculate_level, score_to_points, MODULE_NAMES, MODULE_BADGES
from app.memory.conversation_memory import (
    create_conversation,
    get_conversation,
    get_user_conversations,
    add_message,
    get_conversation_messages,
    messages_to_openai_format,
)
from app.agents.orchestrator import OrchestratorAgent

router = APIRouter(prefix="/chat", tags=["chat"])
security = HTTPBearer()

orchestrator = OrchestratorAgent()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = get_user_conversations(db, user.id)
    return [
        {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat()}
        for c in convs
    ]


@router.post("/message")
async def send_message(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not chat_data.conversation_id:
        conv = create_conversation(db, user.id)
        chat_data.conversation_id = conv.id
    else:
        conv = get_conversation(db, chat_data.conversation_id)
        if not conv or conv.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")

    add_message(db, conv.id, "user", chat_data.message)

    db_messages = get_conversation_messages(db, conv.id)
    openai_messages = messages_to_openai_format(db_messages)

    is_submission = "EVALUATE_SUBMISSION" in chat_data.message or _detect_submission(openai_messages)

    user_context = f"Уровень: {user.level}, Сфера: {user.sphere}, Цели: {user.goals}"

    assignment_context = ""
    current_assignment = db.query(Assignment).filter(
        Assignment.difficulty == user.level
    ).order_by(Assignment.order_num).first()
    if current_assignment:
        assignment_context = f"Текущее задание: {current_assignment.title}\nКритерии: {current_assignment.criteria}"

    result = await orchestrator.route(
        messages=openai_messages,
        user_level=user.level,
        user_context=user_context,
        assignment_context=assignment_context,
        is_submission=is_submission,
    )

    add_message(db, conv.id, "assistant", result["response"], result["agent"])

    if result["agent"] == "PROFILER":
        profile_data = result.get("profile_data", {})
        if profile_data.get("level") and "УРОВЕНЬ:" in result["response"].upper():
            user.level = profile_data["level"]
            if profile_data.get("sphere"):
                user.sphere = profile_data["sphere"]
            if profile_data.get("goals"):
                user.goals = profile_data["goals"]
            db.commit()

    if result["agent"] == "EVALUATOR":
        score = result.get("score", 0)
        points = score_to_points(score)
        current_total = int(user.total_score) + points
        user.total_score = str(current_total)
        user.level = calculate_level(current_total)
        db.commit()

    return {
        "conversation_id": conv.id,
        "agent": result["agent"],
        "response": result["response"],
        "score": result.get("score"),
    }


@router.get("/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = get_conversation(db, conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = get_conversation_messages(db, conversation_id)
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            agent_name=m.agent_name,
        )
        for m in messages
    ]


def _detect_submission(messages: list[dict]) -> bool:
    if len(messages) < 2:
        return False
    for msg in messages[-3:]:
        if msg["role"] == "assistant" and "Задание:" in msg["content"]:
            return True
    return False
