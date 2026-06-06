import re

from app.services.session_cache import AWAITING_ANSWER, AWAITING_CHOICE, AWAITING_CLARIFICATION

PROMPT_UP_KEYWORDS = ["prompt up", "promptup", "промпт ап", "режим prompt", "режим prompt up", "свободный режим"]
MODULE_KEYWORDS = ["хочу пройти модуль", "пройти модуль", "переключи на модуль", "вернуться к урокам", "вернись к урокам", "хочу вернуться к урокам", "научиться писать"]
NAV_KEYWORDS = MODULE_KEYWORDS + PROMPT_UP_KEYWORDS

POSITIVE_WORDS = ["да", "хочу", "давай", "ещё", "еще", "конечно", "yes", "next", "дальше"]


def is_user_submission(user_message: str) -> bool:
    return len(user_message.strip()) > 30


def is_user_wants_more(user_message: str) -> bool:
    text = user_message.strip().lower()
    return any(p in text for p in POSITIVE_WORDS)


def is_navigation_message(user_message: str) -> bool:
    text = user_message.strip().lower()
    return any(kw in text for kw in NAV_KEYWORDS)


def extract_module_id(user_message: str) -> int | None:
    match = re.search(r'модул[ьея]\s+(\d+)', user_message.lower())
    if match:
        mid = int(match.group(1))
        if 1 <= mid <= 6:
            return mid
    return None


def determine_agent(session) -> str:
    if not session.has_profiler_level():
        return "PROFILER"

    user_message = session.conversation[-1].get("content", "") if session.conversation else ""

    if is_navigation_message(user_message):
        mid = extract_module_id(user_message)
        if mid:
            session.set_current_module(mid)
        return "TUTOR"

    state = session.get_awaiting_state()

    if state == "CLARIFICATION":
        return "TUTOR"

    if state == "ANSWER" and is_user_submission(user_message):
        return "EVALUATOR_THEN_TUTOR"

    if state == "CHOICE" and is_user_wants_more(user_message):
        return "TUTOR"

    return "TUTOR"
