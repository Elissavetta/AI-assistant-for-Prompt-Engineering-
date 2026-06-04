import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.models.progress import Progress
from app.schemas.chat import ChatMessage, MessageOut
from app.services.auth_service import decode_access_token
from app.services.scoring_service import calculate_level, score_to_points, MODULE_NAMES, MODULE_BADGES, get_module_badge
from app.memory.conversation_memory import (
    create_conversation,
    get_conversation,
    get_user_conversations,
    add_message,
    get_conversation_messages,
    messages_to_openai_format,
)
from app.agents.llm_client import stream_llm, call_llm
from app.agents.profiler import ProfilerAgent
from app.agents.evaluator import EvaluatorAgent

router = APIRouter(prefix="/chat", tags=["chat"])
security = HTTPBearer()

AWAITING_ANSWER = "[ОЖИДАЕТСЯ ОТВЕТ]"
AWAITING_CHOICE = "[ОЖИДАЕТСЯ ВЫБОР]"
PROFILER_MAX_TURNS = 5


def _force_profile_completion(db: Session, user: User, openai_messages: list[dict]) -> bool:
    if user.level:
        return False
    assistant_turns = sum(1 for m in openai_messages if m.get("role") == "assistant")
    if assistant_turns >= PROFILER_MAX_TURNS:
        user.level = "newbie"
        if not user.sphere:
            user.sphere = "общее"
        if not user.goals:
            user.goals = "научиться писать промпты"
        db.commit()
        return True
    return False


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _detect_awaiting_state(messages: list[dict]) -> str:
    last_assistant = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg.get("content", "")
            break
    if not last_assistant:
        return ""
    if AWAITING_ANSWER in last_assistant:
        return "ANSWER"
    if AWAITING_CHOICE in last_assistant:
        return "CHOICE"
    if any(kw in last_assistant for kw in ["🎯 **Задание:**", "🎯**Задание:**", "Задание:**"]):
        if "SCORE:" not in last_assistant:
            return "ANSWER"
    return ""


def _is_user_submission(user_message: str) -> bool:
    return len(user_message.strip()) > 30


def _is_user_wants_more(user_message: str) -> bool:
    positive = ["да", "хочу", "давай", "ещё", "еще", "конечно", "давай", "yes", "next", "дальше"]
    text = user_message.strip().lower()
    return any(p in text for p in positive)


def _determine_agent(user_level: str, openai_messages: list[dict], user_message: str = "") -> str:
    if not user_level:
        return "PROFILER"

    state = _detect_awaiting_state(openai_messages)

    if state == "ANSWER" and _is_user_submission(user_message):
        return "EVALUATOR_THEN_TUTOR"

    if state == "CHOICE" and _is_user_wants_more(user_message):
        return "TUTOR"

    return "TUTOR"


MODULE_ORDER = [1, 2, 3, 4, 5, 6]


def _get_next_module(user_id: str, db: Session) -> int:
    for mid in MODULE_ORDER:
        progress = db.query(Progress).filter(
            Progress.user_id == user_id,
            Progress.module_id == mid,
        ).first()
        if not progress or not progress.completed:
            return mid
    return MODULE_ORDER[-1]


def _build_user_context(user: User, db: Session, openai_messages: list[dict] = None, eval_context: str = "") -> str:
    ctx = f"Уровень: {user.level}, Сфера: {user.sphere}, Цели: {user.goals}"
    current_module = _get_next_module(user.id, db)
    progress = db.query(Progress).filter(
        Progress.user_id == user.id,
        Progress.module_id == current_module,
    ).first()
    if progress:
        ctx += f", Текущий модуль: {current_module} ({MODULE_NAMES.get(current_module, '')}), Баллов: {progress.score}/{progress.max_score}"
        if progress.completed:
            ctx += " [ЗАВЕРШЁН]"
    else:
        ctx += f", Текущий модуль: {current_module} ({MODULE_NAMES.get(current_module, '')}), Новый модуль"

    completed_ids = []
    for mid in MODULE_ORDER:
        p = db.query(Progress).filter(
            Progress.user_id == user.id,
            Progress.module_id == mid,
        ).first()
        if p and p.completed:
            completed_ids.append(mid)
    if completed_ids:
        ctx += f", Завершённые модули: {completed_ids}"

    if openai_messages:
        last_assistant = ""
        for msg in reversed(openai_messages):
            if msg.get("role") == "assistant":
                last_assistant = msg.get("content", "")
                break
        if "УРОВЕНЬ:" in last_assistant.upper():
            ctx += "\n\nFIRST_TUTOR: да"

    if eval_context:
        ctx += f"\n\nРЕЗУЛЬТАТ ОЦЕНКИ:\n{eval_context}"

    return ctx


def _get_agent_config(agent_name: str, user_context: str, assignment_context: str = "") -> tuple[str, float, int]:
    if agent_name == "PROFILER":
        from app.prompts.profiler_prompt import PROFILER_SYSTEM_PROMPT
        return PROFILER_SYSTEM_PROMPT, 0.5, 250
    elif agent_name == "EVALUATOR":
        from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
        return EVALUATOR_SYSTEM_PROMPT, 0.3, 450
    else:
        from app.prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT
        system = TUTOR_SYSTEM_PROMPT
        if user_context:
            system += f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {user_context}"
        return system, 0.6, 500


async def _evaluate_then_tutor(
    openai_messages: list[dict],
    user: User,
    db: Session,
    conv_id: str,
) -> tuple[str, int, int]:
    eval_system, eval_temp, eval_tokens = _get_agent_config("EVALUATOR")
    eval_response = await call_llm(eval_system, openai_messages, eval_temp, eval_tokens)

    score = EvaluatorAgent.extract_score(eval_response)
    points = score_to_points(score)

    current_total = int(user.total_score) + points
    user.total_score = str(current_total)
    user.level = calculate_level(current_total)

    module_id = _get_next_module(user.id, db)
    progress = db.query(Progress).filter(
        Progress.user_id == user.id,
        Progress.module_id == module_id,
    ).first()
    if not progress:
        progress = Progress(
            user_id=user.id,
            module_id=module_id,
            module_name=MODULE_NAMES.get(module_id, ""),
            score=points,
            max_score=50,
        )
        db.add(progress)
    else:
        progress.score += points
    progress.completed = progress.score >= progress.max_score * 0.7
    progress.badge = get_module_badge(module_id, progress.score, progress.max_score)
    db.commit()

    add_message(db, conv_id, "assistant", eval_response, "EVALUATOR")

    user_context = _build_user_context(user, db, openai_messages, eval_context=eval_response)
    tutor_system, tutor_temp, tutor_tokens = _get_agent_config("TUTOR", user_context)

    tutor_messages = openai_messages + [{"role": "assistant", "content": eval_response}]
    tutor_response = await call_llm(tutor_system, tutor_messages, tutor_temp, tutor_tokens)

    add_message(db, conv_id, "assistant", tutor_response, "TUTOR")

    return tutor_response, score, points


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

    _force_profile_completion(db, user, openai_messages)
    agent_name = _determine_agent(user.level, openai_messages, chat_data.message)

    if agent_name == "EVALUATOR_THEN_TUTOR":
        response, score, points = await _evaluate_then_tutor(
            openai_messages, user, db, conv.id,
        )
        return {
            "conversation_id": conv.id,
            "agent": "EVALUATOR_THEN_TUTOR",
            "response": response,
            "score": score,
            "points": points,
            "total_score": int(user.total_score),
        }

    user_context = _build_user_context(user, db, openai_messages)
    system_prompt, temperature, max_tokens = _get_agent_config(agent_name, user_context)
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)

    add_message(db, conv.id, "assistant", response, agent_name)
    _update_user(db, user, agent_name, response)

    if agent_name == "PROFILER" and "УРОВЕНЬ:" in response.upper():
        db_messages = get_conversation_messages(db, conv.id)
        openai_messages = messages_to_openai_format(db_messages)
        user_context = _build_user_context(user, db, openai_messages)
        tutor_system, tutor_temp, tutor_tokens = _get_agent_config("TUTOR", user_context)
        tutor_response = await call_llm(tutor_system, openai_messages, tutor_temp, tutor_tokens)
        add_message(db, conv.id, "assistant", tutor_response, "TUTOR")

        return {
            "conversation_id": conv.id,
            "agent": "PROFILER_THEN_TUTOR",
            "messages": [
                {"agent": "PROFILER", "response": response},
                {"agent": "TUTOR", "response": tutor_response},
            ],
            "score": None,
            "points": 0,
            "total_score": int(user.total_score),
        }

    return {
        "conversation_id": conv.id,
        "agent": agent_name,
        "response": response,
        "score": None,
        "points": 0,
        "total_score": int(user.total_score),
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

    _force_profile_completion(db, user, openai_messages)
    agent_name = _determine_agent(user.level, openai_messages, chat_data.message)

    conv_id = conv.id

    if agent_name == "EVALUATOR_THEN_TUTOR":
        async def generate_eval():
            eval_system, eval_temp, eval_tokens = _get_agent_config("EVALUATOR")
            eval_response = await call_llm(eval_system, openai_messages, eval_temp, eval_tokens)

            score = EvaluatorAgent.extract_score(eval_response)
            points = score_to_points(score)

            current_total = int(user.total_score) + points
            user.total_score = str(current_total)
            user.level = calculate_level(current_total)

            module_id = _get_next_module(user.id, db)
            progress = db.query(Progress).filter(
                Progress.user_id == user.id,
                Progress.module_id == module_id,
            ).first()
            if not progress:
                progress = Progress(
                    user_id=user.id,
                    module_id=module_id,
                    module_name=MODULE_NAMES.get(module_id, ""),
                    score=points,
                    max_score=50,
                )
                db.add(progress)
            else:
                progress.score += points
            progress.completed = progress.score >= progress.max_score * 0.7
            progress.badge = get_module_badge(module_id, progress.score, progress.max_score)
            db.commit()

            add_message(db, conv_id, "assistant", eval_response, "EVALUATOR")

            user_context = _build_user_context(user, db, openai_messages, eval_context=eval_response)
            tutor_system, tutor_temp, tutor_tokens = _get_agent_config("TUTOR", user_context)

            tutor_messages = openai_messages + [{"role": "assistant", "content": eval_response}]

            yield f"data: {json.dumps({'agent': 'TUTOR', 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

            full_response = []
            async for token in stream_llm(tutor_system, tutor_messages, tutor_temp, tutor_tokens):
                full_response.append(token)
                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

            response_text = "".join(full_response)
            add_message(db, conv_id, "assistant", response_text, "TUTOR")

            yield f"data: {json.dumps({'done': True, 'score': score, 'points': points, 'total_score': int(user.total_score), 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

        return StreamingResponse(generate_eval(), media_type="text/event-stream")

    user_context = _build_user_context(user, db, openai_messages)
    system_prompt, temperature, max_tokens = _get_agent_config(agent_name, user_context)

    async def generate():
        full_response = []
        yield f"data: {json.dumps({'agent': agent_name, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
            full_response.append(token)
            yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        response_text = "".join(full_response)
        add_message(db, conv_id, "assistant", response_text, agent_name)
        _update_user(db, user, agent_name, response_text)

        if agent_name == "PROFILER" and "УРОВЕНЬ:" in response_text.upper():
            yield f"data: {json.dumps({'done': True, 'agent_done': 'PROFILER', 'score': None, 'points': 0, 'total_score': int(user.total_score), 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

            db_messages = get_conversation_messages(db, conv_id)
            openai_messages_updated = messages_to_openai_format(db_messages)
            user_context = _build_user_context(user, db, openai_messages_updated)
            tutor_system, tutor_temp, tutor_tokens = _get_agent_config("TUTOR", user_context)

            tutor_full = []
            yield f"data: {json.dumps({'agent': 'TUTOR', 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
            async for token in stream_llm(tutor_system, openai_messages_updated, tutor_temp, tutor_tokens):
                tutor_full.append(token)
                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
            tutor_text = "".join(tutor_full)
            add_message(db, conv_id, "assistant", tutor_text, "TUTOR")

            yield f"data: {json.dumps({'done': True, 'agent_done': 'TUTOR', 'score': None, 'points': 0, 'total_score': int(user.total_score), 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'done': True, 'score': None, 'points': 0, 'total_score': int(user.total_score), 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

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
