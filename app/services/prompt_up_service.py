import logging

from app.config import MAX_CLARIFICATION_ROUNDS
from app.services.session_cache import AwaitingState

logger = logging.getLogger("prompt_trainer")


def save_eval_result(session, response: str, score: int):
    session._last_eval_context = response
    session._last_score = score
    state = session.get_awaiting_state_enum()
    if state == AwaitingState.CLARIFICATION:
        session._clarification_rounds += 1


def reset_clarification(session):
    session._clarification_rounds = 0


def advance_clarification(session):
    session._clarification_rounds += 1


def build_clarification_suffix(session) -> str:
    if session._clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        return "\n\nПОСЛЕДНИЙ РАУНД: больше НЕ задавай уточняющие вопросы. Дай улучшенную версию промпта с тем что есть. Укажи чего не хватало."
    if session._last_eval_context:
        return f"\n\nРЕЗУЛЬТАТ ОЦЕНКИ:\n{session._last_eval_context}\n\nФАКТИЧЕСКИЙ БАЛЛ: {session._last_score}/10"
    return "\n\nПользователь ответил на уточняющие вопросы. Дай улучшенную версию промпта, учитывая его ответы. Укажи чего не хватало."


def needs_first_analysis(session) -> bool:
    from app.agents.orchestrator import is_user_submission
    user_message = session.conversation[-1].get("content", "") if session.conversation else ""
    state = session.get_awaiting_state_enum()
    return is_user_submission(user_message) and state != AwaitingState.CHOICE


def is_clarification_round(session) -> bool:
    state = session.get_awaiting_state_enum()
    return state == AwaitingState.CLARIFICATION and session._clarification_rounds < MAX_CLARIFICATION_ROUNDS
