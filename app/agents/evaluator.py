import logging

from app.agents.llm_client import call_llm, stream_llm
from app.config import EVALUATOR_MAX_TOKENS, EVALUATOR_TEMPERATURE
from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
from app.services.scoring_service import calculate_level
from app.services.prompt_up_service import save_eval_result, reset_clarification
from app.utils import run_in_thread

logger = logging.getLogger("prompt_trainer")


def extract_score(text: str) -> int:
    import re
    patterns = [
        r'SCORE:\s*(-?\d+)',
        r'ОЦЕНКА:\s*(-?\d+)',
        r'\*\*Баллы:\*\*\s*(\d+)',
        r'Баллы:\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            score = int(match.group(1))
            return min(max(score, 0), 10)
    logger.debug("extract_score: no pattern found, returning 0")
    return 0


async def evaluate_and_score(session, db) -> tuple[str, int]:
    openai_messages = session.get_openai_messages()
    eval_response = await call_llm(
        EVALUATOR_SYSTEM_PROMPT, openai_messages,
        EVALUATOR_TEMPERATURE, EVALUATOR_MAX_TOKENS,
    )

    score = extract_score(eval_response)
    module_id = session.get_active_module()

    logger.debug("Evaluator response (last 50 chars): %s", eval_response[-50:])
    logger.info("Evaluated module %d: score=%d, total=%d", module_id, score, session.profile.total_score)
    return eval_response, score


async def stream_evaluate(session, db):
    import json

    reset_clarification(session)
    openai_messages = session.get_openai_messages()

    full_response = []
    yield f"data: {json.dumps({'agent': 'TUTOR'}, ensure_ascii=False)}\n\n"
    async for token in stream_llm(EVALUATOR_SYSTEM_PROMPT, openai_messages, EVALUATOR_TEMPERATURE, EVALUATOR_MAX_TOKENS):
        full_response.append(token)
        yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

    response_text = "".join(full_response)
    score = extract_score(response_text)
    module_id = session.get_active_module()

    session.add_assistant_message(response_text, "TUTOR")
    save_eval_result(session, response_text, score)
    session.add_module_score(module_id, score)
    session.profile.level = calculate_level(session.profile.total_score)
    await run_in_thread(db.commit)

    logger.info("Evaluated module %d: score=%d, total=%d", module_id, score, session.profile.total_score)

    yield f"data: {json.dumps({'done': True, 'agent_done': 'TUTOR', 'score': score, 'points': score, 'total_score': session.profile.total_score}, ensure_ascii=False)}\n\n"

    from app.services.session_cache import save_session
    await save_session(session)
