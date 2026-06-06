import re

from app.agents.llm_client import call_llm, stream_llm
from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
from app.services.scoring_service import calculate_level


def extract_score(text: str) -> int:
    patterns = [
        r'SCORE:\s*(-?\d+)',
        r'ОЦЕНКА:\s*(-?\d+)',
        r'⭐\s*Оценка:\s*(-?\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            score = int(match.group(1))
            return min(max(score, 0), 10)
    return 5


async def evaluate_and_score(session, db) -> tuple[str, int]:
    openai_messages = session.get_openai_messages()
    eval_response = await call_llm(EVALUATOR_SYSTEM_PROMPT, openai_messages, 0.3, 450)

    score = extract_score(eval_response)
    module_id = session.get_active_module()

    session.add_module_score(module_id, score)
    session.profile.level = calculate_level(session.profile.total_score)
    db.commit()

    return eval_response, score


async def evaluate_then_tutor(session, db) -> tuple[str, int]:
    from app.agents.tutor import build_user_context, get_agent_config

    eval_response, score = await evaluate_and_score(session, db)

    session.add_assistant_message(eval_response, "EVALUATOR")

    user_context = build_user_context(session, eval_context=eval_response, score=score, module_id=session.get_next_module())
    tutor_system, tutor_temp, tutor_tokens = get_agent_config("TUTOR", user_context)
    tutor_messages = session.get_openai_messages()

    tutor_response = await call_llm(tutor_system, tutor_messages, tutor_temp, tutor_tokens)
    session.add_assistant_message(tutor_response, "TUTOR")

    return tutor_response, score


async def stream_evaluate_then_tutor(session, db):
    import json
    from app.agents.tutor import build_user_context, get_agent_config

    eval_response, score = await evaluate_and_score(session, db)

    session.add_assistant_message(eval_response, "EVALUATOR")

    user_context = build_user_context(session, eval_context=eval_response, score=score, module_id=session.get_next_module())
    tutor_system, tutor_temp, tutor_tokens = get_agent_config("TUTOR", user_context)
    tutor_messages = session.get_openai_messages()

    yield f"data: {json.dumps({'agent': 'TUTOR'}, ensure_ascii=False)}\n\n"

    full_response = []
    async for token in stream_llm(tutor_system, tutor_messages, tutor_temp, tutor_tokens):
        full_response.append(token)
        yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

    response_text = "".join(full_response)
    session.add_assistant_message(response_text, "TUTOR")

    yield f"data: {json.dumps({'done': True, 'score': score, 'points': score, 'total_score': session.profile.total_score}, ensure_ascii=False)}\n\n"
