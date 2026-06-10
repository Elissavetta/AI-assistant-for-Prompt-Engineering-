import asyncio
import enum
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from app.config import (
    MAX_CONVERSATION_HISTORY,
    SESSION_TTL_SECONDS,
    MARKER_AWAITING_ANSWER,
    MARKER_AWAITING_CHOICE,
    MARKER_AWAITING_CLARIFICATION,
    MARKER_LEVEL,
    MODULE_COMPLETION_SCORE,
)
from app.models.user import User, UserProfile, ModuleProgress
from app.services.scoring_service import MODULE_ORDER

logger = logging.getLogger("prompt_trainer")


class AwaitingState(enum.Enum):
    NONE = ""
    ANSWER = "ANSWER"
    CHOICE = "CHOICE"
    CLARIFICATION = "CLARIFICATION"


# --- SessionState: JSON-сериализуемое состояние ---

@dataclass
class SessionState:
    conversation: list[dict] = field(default_factory=list)
    mode: str = "lesson"
    awaiting_state: str = ""
    current_module_id: Optional[int] = None
    last_eval_context: str = ""
    last_score: Optional[int] = None
    clarification_rounds: int = 0
    is_api: bool = False
    conversation_id: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "SessionState":
        d = json.loads(data)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --- RedisSessionStore ---

class RedisSessionStore:
    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> Optional[SessionState]:
        data = await self._redis.get(key)
        if data:
            return SessionState.from_json(data)
        return None

    async def set(self, key: str, state: SessionState, ttl: int = SESSION_TTL_SECONDS):
        await self._redis.setex(key, ttl, state.to_json())

    async def delete(self, key: str):
        await self._redis.delete(key)

    async def close(self):
        await self._redis.aclose()


class MemorySessionStore:
    def __init__(self):
        self._data: dict[str, tuple[float, SessionState]] = {}

    async def get(self, key: str) -> Optional[SessionState]:
        import time
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, state = entry
        if time.time() > expires_at:
            del self._data[key]
            return None
        return state

    async def set(self, key: str, state: SessionState, ttl: int = SESSION_TTL_SECONDS):
        import time
        self._data[key] = (time.time() + ttl, state)

    async def delete(self, key: str):
        self._data.pop(key, None)

    async def close(self):
        self._data.clear()


_store: Optional[RedisSessionStore | MemorySessionStore] = None
_store_lock = asyncio.Lock()


async def get_store() -> RedisSessionStore | MemorySessionStore:
    global _store
    if _store is not None:
        return _store
    async with _store_lock:
        if _store is not None:
            return _store
        from app.config import settings
        if settings.REDIS_URL:
            try:
                _store = RedisSessionStore(settings.REDIS_URL)
            except Exception:
                logger.warning("Redis unavailable, using in-memory session store")
                _store = MemorySessionStore()
        else:
            _store = MemorySessionStore()
        return _store


async def close_store():
    global _store
    if _store:
        await _store.close()
        _store = None


# --- Key helpers ---

def _user_session_key(user_id: str) -> str:
    return f"session:{user_id}"


def _api_session_key(conversation_id: str) -> str:
    return f"api_session:{conversation_id}"


# --- UserSession: runtime объект ---

class _DummyProfile:
    def __init__(self):
        self.level = ""
        self.sphere = ""
        self.goals = ""
        self.tutor_introduced = False
        self.total_score = 0
        self.current_module_id = None


class UserSession:
    def __init__(self, user: User | None, profile: UserProfile, modules: dict[int, ModuleProgress], state: SessionState | None = None):
        self.user = user
        self.profile = profile
        self.modules = modules
        s = state or SessionState()
        self.conversation: list[dict] = s.conversation
        self.mode: str = s.mode
        self._current_module_id: int | None = s.current_module_id if s.current_module_id is not None else profile.current_module_id
        self._is_api: bool = s.is_api
        self._last_eval_context: str = s.last_eval_context
        self._last_score: int | None = s.last_score
        self._clarification_rounds: int = s.clarification_rounds
        self._awaiting_state: AwaitingState = AwaitingState(s.awaiting_state)
        self._conversation_id: str = s.conversation_id

    def to_state(self) -> SessionState:
        return SessionState(
            conversation=self.conversation,
            mode=self.mode,
            awaiting_state=self._awaiting_state.value,
            current_module_id=self._current_module_id,
            last_eval_context=self._last_eval_context,
            last_score=self._last_score,
            clarification_rounds=self._clarification_rounds,
            is_api=self._is_api,
            conversation_id=self._conversation_id,
        )

    def add_user_message(self, content: str):
        self.conversation.append({"role": "user", "content": content})
        self._trim()

    def add_assistant_message(self, content: str, agent: str = ""):
        msg = {"role": "assistant", "content": content}
        if agent:
            msg["agent"] = agent
        self.conversation.append(msg)
        self._update_awaiting_state(content)
        self._trim()

    def get_openai_messages(self) -> list[dict]:
        return [dict(m) for m in self.conversation]

    def get_last_assistant_message(self) -> str:
        for msg in reversed(self.conversation):
            if msg.get("role") == "assistant":
                return msg.get("content", "")
        return ""

    def _trim(self):
        if len(self.conversation) > MAX_CONVERSATION_HISTORY:
            self.conversation = self.conversation[-MAX_CONVERSATION_HISTORY:]

    def _update_awaiting_state(self, last_message: str):
        if MARKER_AWAITING_CLARIFICATION in last_message:
            self._awaiting_state = AwaitingState.CLARIFICATION
        elif MARKER_AWAITING_ANSWER in last_message:
            self._awaiting_state = AwaitingState.ANSWER
        elif MARKER_AWAITING_CHOICE in last_message:
            self._awaiting_state = AwaitingState.CHOICE
        elif re.search(r'\[ОЖИДАЕТ', last_message):
            logger.warning("Truncated marker detected in response")
            lower = last_message.lower()
            if any(kw in lower for kw in ["задани", "напиши", "промпт", "попробуй", "составь"]):
                self._awaiting_state = AwaitingState.ANSWER
            elif any(kw in lower for kw in ["продолжим", "хочешь", "переходим", "дальше", "ещё", "следующ"]):
                self._awaiting_state = AwaitingState.CHOICE
            elif any(kw in lower for kw in ["уточни", "какой", "какая", "какие", "сколько"]):
                self._awaiting_state = AwaitingState.CLARIFICATION
            else:
                self._awaiting_state = AwaitingState.CHOICE
        else:
            if any(kw in last_message for kw in ["🎯 **Задание:**", "🎯**Задание:**", "Задание:**"]):
                if "SCORE:" not in last_message:
                    self._awaiting_state = AwaitingState.ANSWER
                    return
            self._awaiting_state = AwaitingState.NONE

    def get_awaiting_state_enum(self) -> AwaitingState:
        return self._awaiting_state

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
        return self.get_module_score(module_id) >= MODULE_COMPLETION_SCORE

    def get_next_module(self) -> int:
        for mid in MODULE_ORDER:
            if not self.is_module_completed(mid):
                return mid
        return MODULE_ORDER[-1]

    def set_current_module(self, module_id: int):
        self._current_module_id = module_id
        self.profile.current_module_id = module_id

    def get_active_module(self) -> int:
        if self._current_module_id is not None:
            if not self.is_module_completed(self._current_module_id):
                return self._current_module_id
        return self.get_next_module()

    def has_profiler_level(self) -> bool:
        return self.profile.level != ""

    def is_returning_user(self) -> bool:
        if not self.profile.tutor_introduced or not self.profile.level:
            return False
        has_tutor_msg = any(
            m.get("role") == "assistant" and m.get("agent") == "TUTOR"
            for m in self.conversation
        )
        user_msg_count = sum(1 for m in self.conversation if m.get("role") == "user")
        return has_tutor_msg and user_msg_count <= 2


# --- High-level async helpers ---

async def load_session(user: User, profile: UserProfile, modules: dict[int, ModuleProgress]) -> UserSession:
    store = await get_store()
    key = _user_session_key(user.id)
    state = await store.get(key)
    if state and profile.current_module_id is not None:
        state.current_module_id = profile.current_module_id
    session = UserSession(user, profile, modules, state)
    return session


async def save_session(session: UserSession):
    store = await get_store()
    if session.user:
        key = _user_session_key(session.user.id)
        await store.set(key, session.to_state())


async def load_api_session(conversation_id: str, mode: str = "prompt_up") -> UserSession:
    store = await get_store()
    key = _api_session_key(conversation_id)
    state = await store.get(key)
    if not state:
        state = SessionState(mode=mode)
    session = UserSession(None, _DummyProfile(), {}, state)
    session._is_api = True
    session._conversation_id = conversation_id
    return session


async def save_api_session(session: UserSession):
    if session._conversation_id:
        store = await get_store()
        key = _api_session_key(session._conversation_id)
        await store.set(key, session.to_state())
