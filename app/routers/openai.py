import hashlib
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.evaluator import evaluate_and_score, extract_score
from app.agents.llm_client import call_llm, stream_llm, get_profiler_llm_client
from app.agents.tutor import build_user_context, get_agent_config
from app.agents.orchestrator import determine_agent
from app.agents.profiler import update_user_from_profile
from app.database import get_db
from app.models.user import User
from app.services.auth_service import decode_access_token
from app.services.progress_service import get_or_create_profile, get_module_progress_map
from app.services.session_cache import (
    load_api_session,
    save_api_session,
    load_session,
    save_session,
)
from app.services.prompt_up_service import (
    save_eval_result,
    reset_clarification,
    advance_clarification,
    build_clarification_suffix,
    needs_first_analysis,
    is_clarification_round,
)
from app.services.scoring_service import calculate_level
from app.utils import run_in_thread

logger = logging.getLogger("prompt_trainer")

_security = HTTPBearer(auto_error=False)


router = APIRouter(tags=["openai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "prompt-up-mode"
    messages: list[ChatMessage]
    stream: bool = False
    conversation_id: str | None = None
    user_id: str | None = None
    temperature: float = 0.6
    max_tokens: int = 800


def _derive_conversation_id(messages: list[ChatMessage]) -> str:
    first_user_msg = next((m.content for m in messages if m.role == "user"), "")
    raw = f"{first_user_msg}:{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _strip_intro(text: str) -> str:
    return re.sub(r'Режим\s*Prompt\s*Up!.*?(?:\n){2,}', '', text, count=1, flags=re.DOTALL).strip()


def _make_response(content: str, conversation_id: str, model: str = "prompt-up-mode") -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _make_chunk(delta: dict, conversation_id: str, model: str = "prompt-up-mode", finish_reason: str | None = None) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


# --- Prompt Up mode ---

async def _build_tutor_response(session, user_context_suffix: str = "") -> str:
    user_context = build_user_context(session)
    if user_context_suffix:
        user_context += user_context_suffix
    system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)
    session.add_assistant_message(response, "TUTOR")
    return _strip_intro(response)


async def _run_prompt_up(session):
    if is_clarification_round(session):
        advance_clarification(session)
        suffix = build_clarification_suffix(session)
        return await _build_tutor_response(session, suffix)

    if needs_first_analysis(session):
        reset_clarification(session)
        suffix = "\n\nИНСТРУКЦИЯ: Пользователь впервые прислал промпт. СНАЧАЛА задай 1-3 уточняющих вопроса (чего не хватает? какая цель? какой формат?), заканчивай [ОЖИДАЕТСЯ УТОЧНЕНИЕ]. НЕ давай улучшенную версию промпта сразу."
        return await _build_tutor_response(session, suffix)

    return await _build_tutor_response(session)


async def _stream_tutor_response(session, conversation_id: str, model: str, user_context_suffix: str = ""):
    user_context = build_user_context(session)
    if user_context_suffix:
        user_context += user_context_suffix
    system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()

    yield _make_chunk({"role": "assistant", "content": ""}, conversation_id, model)
    full_response = []
    async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
        full_response.append(token)
        yield _make_chunk({"content": token}, conversation_id, model)
    response_text = "".join(full_response)
    session.add_assistant_message(response_text, "TUTOR")
    yield _make_chunk({}, conversation_id, model, finish_reason="stop")
    yield "data:\n"


async def _stream_prompt_up(session, model: str):
    conversation_id = session._conversation_id

    if is_clarification_round(session):
        advance_clarification(session)
        suffix = build_clarification_suffix(session)
        async for chunk in _stream_tutor_response(session, conversation_id, model, suffix):
            yield chunk
        await save_api_session(session)
        return

    if needs_first_analysis(session):
        reset_clarification(session)
        suffix = "\n\nИНСТРУКЦИЯ: Пользователь впервые прислал промпт. СНАЧАЛА задай 1-3 уточняющих вопроса (чего не хватает? какая цель? какой формат?), заканчивай [ОЖИДАЕТСЯ УТОЧНЕНИЕ]. НЕ давай улучшенную версию промпта сразу."
        async for chunk in _stream_tutor_response(session, conversation_id, model, suffix):
            yield chunk
        await save_api_session(session)
        return

    async for chunk in _stream_tutor_response(session, conversation_id, model):
        yield chunk
    await save_api_session(session)


# --- Education mode ---

async def _run_education(session, db):
    agent = determine_agent(session)

    if agent == "PROFILER":
        user_context = build_user_context(session)
        system, temp, tokens = get_agent_config("PROFILER", user_context)
        profiler_kwargs = {}
        from app.config import settings as s
        profiler_kwargs["client"] = await get_profiler_llm_client()
        profiler_kwargs["model"] = s.PROFILER_LLM_MODEL or None
        response = await call_llm(system, session.get_openai_messages(), temp, tokens, **profiler_kwargs)
        session.add_assistant_message(response, "PROFILER")
        update_user_from_profile(session, response, "PROFILER")
        if session.user:
            await run_in_thread(db.commit)
        return response

    if agent == "EVALUATOR":
        reset_clarification(session)
        response, score = await evaluate_and_score(session, db)
        session.add_assistant_message(response, "TUTOR")
        save_eval_result(session, response, score)
        if session.user:
            session.add_module_score(session.get_active_module(), score)
            session.profile.level = calculate_level(session.profile.total_score)
            await run_in_thread(db.commit)
        return response

    user_context = build_user_context(session)
    system, temp, tokens = get_agent_config("TUTOR", user_context)
    response = await call_llm(system, session.get_openai_messages(), temp, tokens)
    session.add_assistant_message(response, "TUTOR")
    update_user_from_profile(session, response, "TUTOR")
    if session.user:
        await run_in_thread(db.commit)
    return response


async def _stream_education(session, db, model: str):
    agent = determine_agent(session)
    conversation_id = session._conversation_id

    if agent == "EVALUATOR":
        reset_clarification(session)
        from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
        from app.config import EVALUATOR_TEMPERATURE, EVALUATOR_MAX_TOKENS

        yield _make_chunk({"role": "assistant", "content": ""}, conversation_id, model)
        full_response = []
        async for token in stream_llm(EVALUATOR_SYSTEM_PROMPT, session.get_openai_messages(), EVALUATOR_TEMPERATURE, EVALUATOR_MAX_TOKENS):
            full_response.append(token)
            yield _make_chunk({"content": token}, conversation_id, model)

        response_text = "".join(full_response)
        score = extract_score(response_text)
        session.add_assistant_message(response_text, "TUTOR")
        save_eval_result(session, response_text, score)
        if session.user:
            session.add_module_score(session.get_active_module(), score)
            session.profile.level = calculate_level(session.profile.total_score)
            await run_in_thread(db.commit)

        yield _make_chunk({}, conversation_id, model, finish_reason="stop")
        yield "data:\n"
        await save_api_session(session)
        return

    user_context = build_user_context(session)
    system, temp, tokens = get_agent_config(agent, user_context)

    stream_kwargs = {}
    if agent == "PROFILER":
        from app.config import settings as s
        stream_kwargs["client"] = await get_profiler_llm_client()
        stream_kwargs["model"] = s.PROFILER_LLM_MODEL or None

    yield _make_chunk({"role": "assistant", "content": ""}, conversation_id, model)
    full_response = []
    async for token in stream_llm(system, session.get_openai_messages(), temp, tokens, **stream_kwargs):
        full_response.append(token)
        yield _make_chunk({"content": token}, conversation_id, model)
    response_text = "".join(full_response)
    session.add_assistant_message(response_text, agent)
    update_user_from_profile(session, response_text, agent)
    if session.user:
        await run_in_thread(db.commit)

    yield _make_chunk({}, conversation_id, model, finish_reason="stop")
    yield "data:\n"
    await save_api_session(session)


# --- Endpoints ---

@router.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "prompt-up-mode",
                "object": "model",
                "description": "Агент для улучшения пользовательского промпта",
                "owned_by": "vibe_code_challenge",
            },
            {
                "id": "education-mode",
                "object": "model",
                "description": "Агент для обучения пользователей промпт-инжинирингу",
                "owned_by": "vibe_code_challenge",
            },
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
):
    model = request.model

    if model == "prompt-up-mode":
        cid = request.conversation_id or _derive_conversation_id(request.messages)
        session = await load_api_session(cid, "prompt_up")
        session.add_user_message(request.messages[-1].content)

        if request.stream:
            return StreamingResponse(
                _stream_prompt_up(session, model),
                media_type="text/event-stream",
            )
        text = await _run_prompt_up(session)
        await save_api_session(session)
        return _make_response(text, cid, model)

    if model == "education-mode":
        cid = request.conversation_id or _derive_conversation_id(request.messages)

        if request.user_id:
            if not credentials:
                raise HTTPException(status_code=401, detail="Authorization required when user_id is provided")
            payload = decode_access_token(credentials.credentials)
            if not payload or payload.get("sub") != request.user_id:
                raise HTTPException(status_code=403, detail="Token does not match requested user_id")
            user = db.query(User).filter(User.id == request.user_id).first()
            if not user:
                raise HTTPException(400, f"User not found: {request.user_id}")
            profile = get_or_create_profile(db, user)
            modules = get_module_progress_map(db, user.id)
            session = await load_session(user, profile, modules)
            session._is_api = True
            session._conversation_id = cid
            session.add_user_message(request.messages[-1].content)
        else:
            session = await load_api_session(cid, "lesson")
            session.add_user_message(request.messages[-1].content)

        if request.stream:
            return StreamingResponse(
                _stream_education(session, db, model),
                media_type="text/event-stream",
            )
        text = await _run_education(session, db)
        if request.user_id and session.user:
            await save_session(session)
        else:
            await save_api_session(session)
        return _make_response(text, cid, model)

    raise HTTPException(400, f"Unknown model: {model}. Use 'prompt-up-mode' or 'education-mode'.")
