import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import MARKER_LEVEL
from app.database import get_db
from app.models.user import User
from app.schemas.chat import ChatMessage
from app.services.auth_service import get_current_user
from app.services.progress_service import get_or_create_profile, get_module_progress_map
from app.services.session_cache import load_session, save_session, AwaitingState
from app.agents.orchestrator import determine_agent
from app.agents.profiler import force_profile_completion, update_user_from_profile
from app.agents.evaluator import evaluate_and_score, stream_evaluate
from app.agents.tutor import build_user_context, get_agent_config
from app.agents.llm_client import stream_llm, call_llm, get_profiler_llm_client
from app.services.prompt_up_service import (
    save_eval_result,
    reset_clarification,
    advance_clarification,
    build_clarification_suffix,
)
from app.utils import run_in_thread

logger = logging.getLogger("prompt_trainer")

router = APIRouter(prefix="/chat", tags=["chat"])


async def _handle_profiler_then_tutor(session, response: str, db):
    if MARKER_LEVEL not in response.upper():
        return None

    session.profile.tutor_introduced = True
    await run_in_thread(db.commit)

    user_context = build_user_context(session)
    tutor_system, tutor_temp, tutor_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()

    tutor_response = await call_llm(tutor_system, openai_messages, tutor_temp, tutor_tokens)
    session.add_assistant_message(tutor_response, "TUTOR")
    return {
        "agent": "PROFILER_THEN_TUTOR",
        "messages": [
            {"agent": "PROFILER", "response": response},
            {"agent": "TUTOR", "response": tutor_response},
        ],
        "score": None,
        "points": 0,
        "total_score": session.profile.total_score,
    }


def _build_tutor_context(session) -> str:
    if session.get_awaiting_state_enum() == AwaitingState.CLARIFICATION:
        advance_clarification(session)
        suffix = build_clarification_suffix(session)
        if session.mode == "prompt_up":
            ctx = build_user_context(session)
        else:
            ctx = build_user_context(session, session._last_eval_context, session._last_score)
        if suffix:
            ctx += suffix
        return ctx
    return build_user_context(session)


async def _call_agent(agent_name: str, session, db):
    if agent_name == "EVALUATOR":
        reset_clarification(session)
        response, score = await evaluate_and_score(session, db)
        session.add_assistant_message(response, "TUTOR")
        save_eval_result(session, response, score)
        module_id = session.get_active_module()
        session.add_module_score(module_id, score)
        from app.services.scoring_service import calculate_level
        session.profile.level = calculate_level(session.profile.total_score)
        await run_in_thread(db.commit)
        return {
            "agent": "TUTOR",
            "response": response,
            "score": score,
            "points": score,
            "total_score": session.profile.total_score,
        }

    user_context = _build_tutor_context(session)
    system_prompt, temperature, max_tokens = get_agent_config(agent_name, user_context)
    openai_messages = session.get_openai_messages()
    llm_kwargs = {}
    if agent_name == "PROFILER":
        from app.config import settings as s
        llm_kwargs["client"] = await get_profiler_llm_client()
        llm_kwargs["model"] = s.PROFILER_LLM_MODEL or None
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens, **llm_kwargs)
    session.add_assistant_message(response, agent_name)
    update_user_from_profile(session, response, agent_name)
    await run_in_thread(db.commit)

    profiler_result = await _handle_profiler_then_tutor(session, response, db)
    if profiler_result:
        return profiler_result

    return {
        "agent": agent_name,
        "response": response,
        "score": None,
        "points": 0,
        "total_score": session.profile.total_score,
    }


async def _stream_agent(agent_name: str, session, db):
    if agent_name == "EVALUATOR":
        return stream_evaluate(session, db)

    user_context = _build_tutor_context(session)
    system_prompt, temperature, max_tokens = get_agent_config(agent_name, user_context)
    openai_messages = session.get_openai_messages()
    stream_kwargs = {}
    if agent_name == "PROFILER":
        from app.config import settings as s
        stream_kwargs["client"] = await get_profiler_llm_client()
        stream_kwargs["model"] = s.PROFILER_LLM_MODEL or None

    async def generate():
        full_response = []
        yield f"data: {json.dumps({'agent': agent_name}, ensure_ascii=False)}\n\n"
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens, **stream_kwargs):
            full_response.append(token)
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        response_text = "".join(full_response)
        session.add_assistant_message(response_text, agent_name)
        update_user_from_profile(session, response_text, agent_name)
        await run_in_thread(db.commit)

        if MARKER_LEVEL in response_text.upper():
            session.profile.tutor_introduced = True
            await run_in_thread(db.commit)

            yield f"data: {json.dumps({'done': True, 'agent_done': 'PROFILER', 'score': None, 'points': 0, 'total_score': session.profile.total_score}, ensure_ascii=False)}\n\n"

            user_context = build_user_context(session)
            tutor_system, tutor_temp, tutor_tokens = get_agent_config("TUTOR", user_context)
            openai_messages_updated = session.get_openai_messages()

            tutor_full = []
            yield f"data: {json.dumps({'agent': 'TUTOR'}, ensure_ascii=False)}\n\n"
            async for token in stream_llm(tutor_system, openai_messages_updated, tutor_temp, tutor_tokens):
                tutor_full.append(token)
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            tutor_text = "".join(tutor_full)
            session.add_assistant_message(tutor_text, "TUTOR")

            yield f"data: {json.dumps({'done': True, 'agent_done': 'TUTOR', 'score': None, 'points': 0, 'total_score': session.profile.total_score}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'done': True, 'score': None, 'points': 0, 'total_score': session.profile.total_score}, ensure_ascii=False)}\n\n"

        await save_session(session)

    return generate()


@router.post("/message")
async def send_message(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = await run_in_thread(get_or_create_profile, db, user)
    modules = await run_in_thread(get_module_progress_map, db, user.id)
    session = await load_session(user, profile, modules)
    session.mode = chat_data.mode
    session.add_user_message(chat_data.message)

    force_profile_completion(session)
    await run_in_thread(db.commit)

    agent_name = determine_agent(session)
    logger.info("User %s → agent: %s", user.id, agent_name)

    result = await _call_agent(agent_name, session, db)
    await save_session(session)
    return result


@router.post("/message/stream")
async def send_message_stream(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = await run_in_thread(get_or_create_profile, db, user)
    modules = await run_in_thread(get_module_progress_map, db, user.id)
    session = await load_session(user, profile, modules)
    session.mode = chat_data.mode
    session.add_user_message(chat_data.message)

    force_profile_completion(session)
    await run_in_thread(db.commit)

    agent_name = determine_agent(session)
    logger.info("User %s → agent: %s (stream)", user.id, agent_name)

    generator = await _stream_agent(agent_name, session, db)
    return StreamingResponse(generator, media_type="text/event-stream")
