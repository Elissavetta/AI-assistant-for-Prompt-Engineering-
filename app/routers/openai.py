import hashlib
import json
import re
import time
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.evaluator import extract_score
from app.agents.llm_client import call_llm, stream_llm
from app.agents.tutor import build_user_context, get_agent_config
from app.agents.orchestrator import is_user_submission
from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
from app.services.session_cache import get_or_create_openai_session

MAX_CLARIFICATION_ROUNDS = 2

router = APIRouter(tags=["openai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "prompt-up"
    messages: list[ChatMessage]
    stream: bool = False
    conversation_id: str | None = None
    temperature: float = 0.6
    max_tokens: int = 800


def _derive_conversation_id(messages: list[ChatMessage]) -> str:
    first_user_msg = next((m.content for m in messages if m.role == "user"), "")
    return hashlib.md5(first_user_msg.encode()).hexdigest()[:16]


def _strip_intro(text: str) -> str:
    return re.sub(r'🚀\s*Режим\s*Prompt\s*Up!.*?(?:\n){2,}', '', text, count=1, flags=re.DOTALL).strip()


def _make_response(content: str, conversation_id: str, model: str = "prompt-up") -> dict:
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


def _make_chunk(delta: dict, conversation_id: str, model: str = "prompt-up", finish_reason: str | None = None) -> str:
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


async def _run_prompt_up(session):
    user_message = session.conversation[-1].get("content", "") if session.conversation else ""
    state = session.get_awaiting_state()

    if state == "CLARIFICATION" and session._clarification_rounds < MAX_CLARIFICATION_ROUNDS:
        session._clarification_rounds += 1
        user_context = build_user_context(
            session,
            eval_context=session._last_eval_context,
            score=session._last_score,
        )
        if session._clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
            user_context += "\n\nПОСЛЕДНИЙ РАУНД: больше НЕ задавай уточняющие вопросы. Дай улучшенную версию промпта с тем что есть. Укажи чего не хватало."

        system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
        openai_messages = session.get_openai_messages()
        response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)
        session.add_assistant_message(response, "TUTOR")
        return _strip_intro(response)

    if is_user_submission(user_message) and state != "CHOICE":
        session._clarification_rounds = 0

        openai_messages = session.get_openai_messages()
        eval_response = await call_llm(EVALUATOR_SYSTEM_PROMPT, openai_messages, 0.3, 450)
        score = extract_score(eval_response)
        session.add_assistant_message(eval_response, "EVALUATOR")

        session._last_eval_context = eval_response
        session._last_score = score

        user_context = build_user_context(session, eval_context=eval_response, score=score)
        system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
        openai_messages = session.get_openai_messages()
        response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)
        session.add_assistant_message(response, "TUTOR")
        return _strip_intro(response)

    user_context = build_user_context(session)
    system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)
    session.add_assistant_message(response, "TUTOR")
    return _strip_intro(response)


async def _stream_prompt_up(session):
    user_message = session.conversation[-1].get("content", "") if session.conversation else ""
    state = session.get_awaiting_state()
    conversation_id = getattr(session, "_conversation_id", "unknown")

    if state == "CLARIFICATION" and session._clarification_rounds < MAX_CLARIFICATION_ROUNDS:
        session._clarification_rounds += 1
        user_context = build_user_context(
            session,
            eval_context=session._last_eval_context,
            score=session._last_score,
        )
        if session._clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
            user_context += "\n\nПОСЛЕДНИЙ РАУНД: больше НЕ задавай уточняющие вопросы. Дай улучшенную версию промпта с тем что есть. Укажи чего не хватало."

        system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
        openai_messages = session.get_openai_messages()

        yield _make_chunk({"role": "assistant", "content": ""}, conversation_id)
        full_response = []
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
            full_response.append(token)
            yield _make_chunk({"content": token}, conversation_id)
        response_text = "".join(full_response)
        session.add_assistant_message(response_text, "TUTOR")
        yield _make_chunk({}, conversation_id, finish_reason="stop")
        yield "data:\n"
        return

    if is_user_submission(user_message) and state != "CHOICE":
        session._clarification_rounds = 0

        openai_messages = session.get_openai_messages()
        eval_response = await call_llm(EVALUATOR_SYSTEM_PROMPT, openai_messages, 0.3, 450)
        score = extract_score(eval_response)
        session.add_assistant_message(eval_response, "EVALUATOR")

        session._last_eval_context = eval_response
        session._last_score = score

        user_context = build_user_context(session, eval_context=eval_response, score=score)
        system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
        openai_messages = session.get_openai_messages()

        yield _make_chunk({"role": "assistant", "content": ""}, conversation_id)
        full_response = []
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
            full_response.append(token)
            yield _make_chunk({"content": token}, conversation_id)
        response_text = "".join(full_response)
        session.add_assistant_message(response_text, "TUTOR")
        yield _make_chunk({}, conversation_id, finish_reason="stop")
        yield "data:\n"
        return

    user_context = build_user_context(session)
    system_prompt, temperature, max_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()

    yield _make_chunk({"role": "assistant", "content": ""}, conversation_id)
    full_response = []
    async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
        full_response.append(token)
        yield _make_chunk({"content": token}, conversation_id)
    response_text = "".join(full_response)
    session.add_assistant_message(response_text, "TUTOR")
    yield _make_chunk({}, conversation_id, finish_reason="stop")
    yield "data:\n"


@router.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "prompt-up",
                "object": "model",
                "owned_by": "vibe_code_challenge",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    conversation_id = request.conversation_id or _derive_conversation_id(request.messages)
    session = get_or_create_openai_session(conversation_id)
    session._is_api = True
    session._conversation_id = conversation_id

    last_user_msg = request.messages[-1].content if request.messages else ""
    session.add_user_message(last_user_msg)

    if request.stream:
        return StreamingResponse(
            _stream_prompt_up(session),
            media_type="text/event-stream",
        )

    response_text = await _run_prompt_up(session)
    return _make_response(response_text, conversation_id)
