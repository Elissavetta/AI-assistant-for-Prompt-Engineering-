import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.chat import ChatMessage
from app.services.auth_service import get_current_user
from app.services.progress_service import get_or_create_profile, get_module_progress_map
from app.services.session_cache import load_session
from app.agents.orchestrator import determine_agent
from app.agents.profiler import force_profile_completion, update_user_from_profile
from app.agents.evaluator import evaluate_then_tutor, stream_evaluate_then_tutor
from app.agents.tutor import build_user_context, get_agent_config
from app.agents.llm_client import stream_llm, call_llm

router = APIRouter(prefix="/chat", tags=["chat"])


async def _handle_profiler_then_tutor(session, response: str, db, stream: bool = False):
    if "УРОВЕНЬ:" not in response.upper():
        return None

    session.profile.tutor_introduced = True
    db.commit()

    user_context = build_user_context(session)
    tutor_system, tutor_temp, tutor_tokens = get_agent_config("TUTOR", user_context)
    openai_messages = session.get_openai_messages()

    if not stream:
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
    return None


async def _call_agent(agent_name: str, session, db):
    user_context = build_user_context(session)
    system_prompt, temperature, max_tokens = get_agent_config(agent_name, user_context)
    openai_messages = session.get_openai_messages()
    response = await call_llm(system_prompt, openai_messages, temperature, max_tokens)
    session.add_assistant_message(response, agent_name)
    update_user_from_profile(session, response)
    db.commit()

    profiler_result = await _handle_profiler_then_tutor(session, response, db, stream=False)
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
    user_context = build_user_context(session)
    system_prompt, temperature, max_tokens = get_agent_config(agent_name, user_context)
    openai_messages = session.get_openai_messages()

    async def generate():
        full_response = []
        yield f"data: {json.dumps({'agent': agent_name}, ensure_ascii=False)}\n\n"
        async for token in stream_llm(system_prompt, openai_messages, temperature, max_tokens):
            full_response.append(token)
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        response_text = "".join(full_response)
        session.add_assistant_message(response_text, agent_name)
        update_user_from_profile(session, response_text)
        db.commit()

        if "УРОВЕНЬ:" in response_text.upper():
            session.profile.tutor_introduced = True
            db.commit()

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

    return generate()


@router.post("/message")
async def send_message(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_profile(db, user)
    modules = get_module_progress_map(db, user.id)
    session = load_session(user, profile, modules)
    session.mode = chat_data.mode
    session.add_user_message(chat_data.message)

    force_profile_completion(session)
    db.commit()

    agent_name = determine_agent(session)

    if agent_name == "EVALUATOR_THEN_TUTOR":
        response, score = await evaluate_then_tutor(session, db)
        return {
            "agent": "EVALUATOR_THEN_TUTOR",
            "response": response,
            "score": score,
            "points": score,
            "total_score": session.profile.total_score,
        }

    return await _call_agent(agent_name, session, db)


@router.post("/message/stream")
async def send_message_stream(
    chat_data: ChatMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_profile(db, user)
    modules = get_module_progress_map(db, user.id)
    session = load_session(user, profile, modules)
    session.mode = chat_data.mode
    session.add_user_message(chat_data.message)

    force_profile_completion(session)
    db.commit()

    agent_name = determine_agent(session)

    if agent_name == "EVALUATOR_THEN_TUTOR":
        return StreamingResponse(stream_evaluate_then_tutor(session, db), media_type="text/event-stream")

    generator = await _stream_agent(agent_name, session, db)
    return StreamingResponse(generator, media_type="text/event-stream")
