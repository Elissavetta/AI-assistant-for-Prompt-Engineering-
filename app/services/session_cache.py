import time

from app.models.user import User, UserProfile, ModuleProgress
from app.services.scoring_service import MODULE_NAMES, MODULE_ORDER

MAX_CONVERSATION_HISTORY = 20
SESSION_TTL_SECONDS = 1800

AWAITING_ANSWER = "[ОЖИДАЕТСЯ ОТВЕТ]"
AWAITING_CHOICE = "[ОЖИДАЕТСЯ ВЫБОР]"
AWAITING_CLARIFICATION = "[ОЖИДАЕТСЯ УТОЧНЕНИЕ]"


class UserSession:
    def __init__(self, user: User, profile: UserProfile, modules: dict[int, ModuleProgress]):
        self.user = user
        self.profile = profile
        self.modules = modules
        self.conversation: list[dict] = []
        self.mode: str = "lesson"
        self._current_module_id: int | None = None
        self._is_api: bool = False
        self._last_eval_context: str = ""
        self._last_score: int | None = None
        self._clarification_rounds: int = 0
        self._last_activity: float = time.time()

    def add_user_message(self, content: str):
        self.conversation.append({"role": "user", "content": content})
        self._last_activity = time.time()
        self._trim()

    def add_assistant_message(self, content: str, agent: str = ""):
        msg = {"role": "assistant", "content": content}
        if agent:
            msg["agent"] = agent
        self.conversation.append(msg)
        self._last_activity = time.time()
        self._trim()

    def get_openai_messages(self) -> list[dict]:
        return self.conversation

    def get_last_assistant_message(self) -> str:
        for msg in reversed(self.conversation):
            if msg.get("role") == "assistant":
                return msg.get("content", "")
        return ""

    def _trim(self):
        if len(self.conversation) > MAX_CONVERSATION_HISTORY:
            self.conversation = self.conversation[-MAX_CONVERSATION_HISTORY:]

    def get_awaiting_state(self) -> str:
        last = self.get_last_assistant_message()
        if not last:
            return ""
        if AWAITING_CLARIFICATION in last:
            return "CLARIFICATION"
        if AWAITING_ANSWER in last:
            return "ANSWER"
        if AWAITING_CHOICE in last:
            return "CHOICE"
        if any(kw in last for kw in ["🎯 **Задание:**", "🎯**Задание:**", "Задание:**"]):
            if "SCORE:" not in last:
                return "ANSWER"
        return ""

    def get_module_score(self, module_id: int) -> int:
        mp = self.modules.get(module_id)
        return mp.score if mp else 0

    def get_module_count(self, module_id: int) -> int:
        mp = self.modules.get(module_id)
        return mp.count if mp else 0

    def add_module_score(self, module_id: int, score: int):
        mp = self.modules.get(module_id)
        if mp:
            mp.score += score
            mp.count += 1
            self.profile.total_score += score

    def is_module_completed(self, module_id: int) -> bool:
        return self.get_module_score(module_id) >= 50

    def get_next_module(self) -> int:
        for mid in MODULE_ORDER:
            if not self.is_module_completed(mid):
                return mid
        return MODULE_ORDER[-1]

    def set_current_module(self, module_id: int):
        self._current_module_id = module_id

    def get_active_module(self) -> int:
        if self._current_module_id is not None:
            return self._current_module_id
        return self.get_next_module()

    def has_profiler_level(self) -> bool:
        return self.profile.level != ""

    def completed_modules_count(self) -> int:
        return sum(1 for mid in MODULE_ORDER if self.is_module_completed(mid))

    def tasks_done_count(self) -> int:
        return sum(self.get_module_count(mid) for mid in MODULE_ORDER)


_sessions: dict[str, UserSession] = {}


def get_session(user_id: str) -> UserSession | None:
    return _sessions.get(user_id)


def load_session(user: User, profile: UserProfile, modules: dict[int, ModuleProgress]) -> UserSession:
    session = _sessions.get(user.id)
    if session:
        session.user = user
        session.profile = profile
        session.modules = modules
        return session
    session = UserSession(user, profile, modules)
    _sessions[user.id] = session
    return session


def clear_session(user_id: str):
    _sessions.pop(user_id, None)


_openai_sessions: dict[str, UserSession] = {}


def get_or_create_openai_session(conversation_id: str) -> UserSession:
    session = _openai_sessions.get(conversation_id)
    if session:
        return session
    profile = UserProfile(level="", sphere="", goals="")
    session = UserSession(user=None, profile=profile, modules={})
    session.mode = "prompt_up"
    _openai_sessions[conversation_id] = session
    return session


def clear_openai_session(conversation_id: str):
    _openai_sessions.pop(conversation_id, None)


def cleanup_expired_sessions():
    now = time.time()
    expired = [
        cid for cid, session in _openai_sessions.items()
        if now - session._last_activity > SESSION_TTL_SECONDS
    ]
    for cid in expired:
        del _openai_sessions[cid]
