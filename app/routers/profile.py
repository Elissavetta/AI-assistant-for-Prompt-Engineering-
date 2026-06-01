from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.chat import ChatMessage
from app.schemas.progress import ProfileOut, ProgressOut
from app.services.auth_service import decode_access_token
from app.services.scoring_service import calculate_level, MODULE_NAMES
from app.memory.conversation_memory import (
    create_conversation,
    get_conversation,
    get_user_conversations,
    add_message,
    get_conversation_messages,
    messages_to_openai_format,
)
from app.agents.orchestrator import OrchestratorAgent

router = APIRouter(prefix="/profile", tags=["profile"])
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


@router.get("/me", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user)):
    badges = []
    for pr in user.progress_records:
        if pr.badge:
            badges.append(pr.badge)
    return ProfileOut(
        username=user.username,
        email=user.email,
        level=user.level,
        sphere=user.sphere,
        goals=user.goals,
        total_score=int(user.total_score),
        badges=badges,
    )


@router.get("/progress", response_model=list[ProgressOut])
def get_progress(user: User = Depends(get_current_user)):
    result = []
    for pr in user.progress_records:
        result.append(ProgressOut(
            module_id=pr.module_id,
            module_name=pr.module_name,
            score=pr.score,
            max_score=pr.max_score,
            completed=pr.completed,
            badge=pr.badge,
        ))
    return result
