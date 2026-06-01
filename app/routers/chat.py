import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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
from app.agents.llm_client import stream_llm
from app.agents.profiler import ProfilerAgent
from app.agents.evaluator import EvaluatorAgent

router = APIRouter(prefix="/chat", tags=["chat"])
security = HTTPBearer()

AWAITING_RESPONSE = "[ОЖИДАЕТСЯ ОТВЕТ]"


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _determine_agent(user_level: str, openai_messages: list[dict], is_submission_keyword: bool) -> str:
    if not user_level:
        return "PROFILER"

    if _is_awaiting_submission(openai_messages):
        return "EVALUATOR"

    return "TUTOR"


def _is_awaiting_submission(messages: list[dict]) -> bool:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return AWAITING_RESPONSE in msg.get("content", "")
    return False


def _get_agent_config(agent_name: str, user_context: str, assignment_context: str) -> tuple[str, float, int]:
    if agent_name == "PROFILER":
        from app.prompts.profiler_prompt import PROFILER_SYSTEM_PROMPT
        return PROFILER_SYSTEM_PROMPT, 0.5, 250
    elif agent_name == "EVALUATOR":
        from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
        system = EVALUATOR_SYSTEM_PROMPT
        if assignment_context:
            system += f"\n\nКОНТЕКСТ ЗАДАНИЯ:\n{assignment_context}"
        return system, 0.3, 450
    else:
        from app.prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT
        system = TUTOR_SYSTEM_PROMPT
        if user_context:
            system += f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {user_context}"
        return system, 0.6, 500


def _update_user(db: Session, user: User, agent_name: str, response: str):
    if agent_name == "PROFILER":
        profile_data = ProfilerAgent.parse_profile(response)
        if profile_data.get("level") and "УРОВЕНЬ:" in response.upper():
            user.level = profile_data["level"]
            if profile_data.get("sphere"):
                user.sphere = profile_data["sphere"]
            if profile_data.get("goals"):
                user.goals = profile_data["goals"]
            db.commit()
    elif agent_name == "EVALUATOR":
        score = EvaluatorAgent.extract_score(response)
        points = score_to_points(score)
        current_total = int(user.total_score) + points
        user.total_score = str(current_total)
        user.level = calculate_level(current_total)
        db.commit()


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
    conv = _ensure_conversation(db, chat_data, user)
    add_message(db, conv.id, "user", chat_data.message)

    db_messages = get_conversation_messages(db, conv.id)
    openai_messages = messages_to_openai_format(db_messages)

    is_submission_keyword = "EVALUATE_SUBMISSION" in chat_data.message
    agent_name = _determine_agent(user.level, openai_messages, is_submission_keyword)

    user_context = f"Уровень: {user.level}, Сфера: {user.sphere}, Цели: {user.goals}"

    assignment_context = ""
    current_assignment = db.query(Assignment).filter(
        Assignment.difficulty == user.level
    ).order_by(Assignment.order_num).first()
    if current_assignment:
        assignment_context = f"Текущее задание: {current_assignment.title}\nКритерии: {current_assignment.criteria}"

    system_prompt, temperature, max_tokens = _get_agent_config(agent_name, user_context, assignment_context)

    from app.agents.llm_client import call_llm
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)

    add_message(db, conv.id, "assistant", response, agent_name)
    _update_user(db, user, agent_name, response)

    score = None
    if agent_name == "EVALUATOR":
        score = EvaluatorAgent.extract_score(response)

    return {
        "conversation_id": conv.id,
        "agent": agent_name,
        "response": response,
        "score": score,
    }


@router.post("/message/stream")
async def send_message_stream(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = _ensure_conversation(db, chat_data, user)
    add_message(db, conv.id, "user", chat_data.message)

    db_messages = get_conversation_messages(db, conv.id)
    openai_messages = messages_to_openai_format(db_messages)

    is_submission_keyword = "EVALUATE_SUBMISSION" in chat_data.message
    agent_name = _determine_agent(user.level, openai_messages, is_submission_keyword)

    user_context = f"Уровень: {user.level}, Сфера: {user.sphere}, Цели: {user.goals}"

    assignment_context = ""
    current_assignment = db.query(Assignment).filter(
        Assignment.difficulty == user.level
    ).order_by(Assignment.order_num).first()
    if current_assignment:
        assignment_context = f"Текущее задание: {current_assignment.title}\nКритерии: {current_assignment.criteria}"

    system_prompt, temperature, max_tokens = _get_agent_config(agent_name, user_context, assignment_context)

    async def generate():
        full_response = []
        yield f"data: {json.dumps({'agent': agent_name}, ensure_ascii=False)}\n\n"
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
            full_response.append(token)
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        response_text = "".join(full_response)
        add_message(db, conv.id, "assistant", response_text, agent_name)
        _update_user(db, user, agent_name, response_text)
        score = None
        if agent_name == "EVALUATOR":
            score = EvaluatorAgent.extract_score(response_text)
        yield f"data: {json.dumps({'done': True, 'score': score}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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


def _ensure_conversation(db: Session, chat_data: ChatMessage, user: User) -> Conversation:
    if not chat_data.conversation_id:
        conv = create_conversation(db, user.id)
        chat_data.conversation_id = conv.id
        return conv
    conv = get_conversation(db, chat_data.conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv
