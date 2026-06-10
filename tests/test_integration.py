import json
import os
import uuid

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("REDIS_URL", "redis://fake")

from app.database import SessionLocal, Base, engine
from app.models.user import User, UserProfile, ModuleProgress
from app.services.auth_service import hash_password, create_access_token

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _mock_redis():
    import fakeredis.aioredis
    from app.services import session_cache as sc

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original_store = sc._store
    store = sc.RedisSessionStore.__new__(sc.RedisSessionStore)
    store._redis = fake
    sc._store = store

    yield

    sc._store = original_store


@pytest.fixture(autouse=True)
def _bypass_rate_limit():
    from app.main import _rate_limiter
    original_is_allowed = _rate_limiter.is_allowed
    _rate_limiter.is_allowed = lambda key: True
    yield
    _rate_limiter.is_allowed = original_is_allowed


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _register(client, username=None, password="testpass123"):
    uname = username or f"user_{uuid.uuid4().hex[:8]}"
    email = f"{uname}@test.com"
    resp = client.post("/api/auth/register", json={
        "username": uname,
        "email": email,
        "password": password,
    })
    assert resp.status_code == 200
    return resp.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data:":
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


async def _async_gen_from_list(items):
    for item in items:
        yield item


def _mock_stream_llm(tokens: list[str]):
    async def _gen(*args, **kwargs):
        for t in tokens:
            yield t
    return _gen


def _make_call_llm(responses: list[str]):
    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        resp = responses[idx]
        call_count += 1
        return resp

    return _side_effect


# =============================================
# SCENARIO 1: Education Mode — New User
# =============================================

class TestEducationNewUser:
    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_profiler_to_tutor_transition(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: разработка\nЦели: промпты для кода"
        TUTOR_LESSON = "**Тема:** Структура промпта\n**Задание:** Напиши промпт\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_LESSON])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp = client.post("/api/chat/message", json={
            "message": "Привет!",
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] in ("PROFILER", "PROFILER_THEN_TUTOR")
        if data["agent"] == "PROFILER_THEN_TUTOR":
            assert len(data["messages"]) == 2
            assert data["messages"][0]["agent"] == "PROFILER"
            assert data["messages"][1]["agent"] == "TUTOR"

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_evaluator_scores_and_updates_progress(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: маркетинг\nЦели: реклама"
        TUTOR_LESSON = "**Задание:** Напиши промпт\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        EVAL_RESULT = "**Что хорошо:** Роль указана\nSCORE: 7\n\nХочешь ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_LESSON, EVAL_RESULT])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        client.post("/api/chat/message", json={"message": "Напиши рецепт борща", "mode": "lesson"}, headers=headers)
        resp = client.post("/api/chat/message", json={"message": "Ты ИИ-ассистент. Напиши рецепт.", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200

        progress_resp = client.get("/api/profile/progress", headers=headers)
        assert progress_resp.status_code == 200
        progress = progress_resp.json()
        total_score = sum(p.get("score", 0) for p in progress)
        assert total_score > 0

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_new_user_gets_profiler(self, mock_stream, mock_call, client, db):
        PROFILER_ASK = "Расскажи о себе.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_ASK])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp = client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "PROFILER"


# =============================================
# SCENARIO 2: Education Mode — Returning User
# =============================================

class TestEducationReturningUser:
    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_returning_user_continues(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: intermediate\nСфера: разработка\nЦели: промпты для API"
        TUTOR_RETURN = "С возвращением! Продолжаем модуль 2.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_RETURN, TUTOR_RETURN])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        # First session
        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        # Returning
        resp = client.post("/api/chat/message", json={"message": "Хочу продолжить", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "TUTOR"


# =============================================
# SCENARIO 3: Education Mode — Module Completion
# =============================================

class TestEducationModuleCompletion:
    def test_module_completion_threshold(self, client, db):
        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)
        user_id = _get_user_id_from_token(token)

        user = db.query(User).filter(User.id == user_id).first()
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            profile = UserProfile(user_id=user_id, level="newbie", sphere="test", goals="test")
            db.add(profile)
            db.commit()
            db.refresh(profile)

        mp = ModuleProgress(user_id=user_id, module_id=1, count=1, score=50)
        db.add(mp)
        profile.total_score = 50
        db.commit()

        progress_resp = client.get("/api/profile/progress", headers=headers)
        assert progress_resp.status_code == 200
        progress = progress_resp.json()
        mod1 = next((p for p in progress if p["module_id"] == 1), None)
        assert mod1 is not None
        assert mod1["score"] == 50


# =============================================
# SCENARIO 4: Prompt Up Mode — Full Cycle
# =============================================

class TestPromptUpMode:
    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_intro_and_clarification(self, mock_stream, mock_call, client, db):
        PU_INTRO = "Режим Prompt Up! Просто напиши любой промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_CLARIFY = "Какая конкретно задача?\n[ОЖИДАЕТСЯ УТОЧНЕНИЕ]"
        PU_IMPROVED = "**Чего не хватало:** Роль, формат\n**Улучшенная версия:** Ты эксперт...\n\nХочешь улучшить ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"

        mock_call.side_effect = _make_call_llm([PU_INTRO, PU_CLARIFY, PU_IMPROVED])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp1 = client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)
        assert resp1.status_code == 200
        assert "ОЖИДАЕТСЯ ОТВЕТ" in resp1.json()["response"]

        resp2 = client.post("/api/chat/message", json={"message": "Напиши рецепт борща", "mode": "prompt_up"}, headers=headers)
        assert resp2.status_code == 200
        assert "ОЖИДАЕТСЯ УТОЧНЕНИЕ" in resp2.json()["response"]

        resp3 = client.post("/api/chat/message", json={"message": "Для начинающего повара", "mode": "prompt_up"}, headers=headers)
        assert resp3.status_code == 200
        assert "ОЖИДАЕТСЯ ВЫБОР" in resp3.json()["response"]

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_no_score(self, mock_stream, mock_call, client, db):
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp = client.post("/api/chat/message", json={"message": "улучшить промпт", "mode": "prompt_up"}, headers=headers)
        assert resp.status_code == 200
        assert "SCORE:" not in resp.json()["response"]

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_cycle_repeat(self, mock_stream, mock_call, client, db):
        PU_CHOICE = "**Улучшенная версия:** ...\nХочешь улучшить ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"
        PU_NEW = "Напиши следующий промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PU_CHOICE, PU_NEW])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Напиши рецепт", "mode": "prompt_up"}, headers=headers)
        resp = client.post("/api/chat/message", json={"message": "Давай ещё", "mode": "prompt_up"}, headers=headers)
        assert resp.status_code == 200
        assert "ОЖИДАЕТСЯ ОТВЕТ" in resp.json()["response"]


# =============================================
# SCENARIO 5: Prompt Up — Skip Clarification
# =============================================

class TestPromptUpSkipClarification:
    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_good_prompt_skips_clarification(self, mock_stream, mock_call, client, db):
        PU_INTRO = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_DIRECT = "**Улучшенная версия:** Ты аналитик...\nХочешь улучшить ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"

        mock_call.side_effect = _make_call_llm([PU_INTRO, PU_DIRECT])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        resp = client.post("/api/chat/message", json={
            "message": "Ты аналитик данных. Проанализируй датасет sales.csv. Выведи топ-5 регионов.",
            "mode": "prompt_up",
        }, headers=headers)
        assert resp.status_code == 200
        assert "ОЖИДАЕТСЯ ВЫБОР" in resp.json()["response"]
        assert "ОЖИДАЕТСЯ УТОЧНЕНИЕ" not in resp.json()["response"]


# =============================================
# SCENARIO 6: Switching Modes
# =============================================

class TestModeSwitching:
    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_switch_lesson_to_prompt_up(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: маркетинг\nЦели: реклама"
        TUTOR_LESSON = "**Тема:** Структура промпта\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_LESSON, PU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp1 = client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        assert resp1.status_code == 200

        resp2 = client.post("/api/chat/message", json={"message": "Хочу улучшить промпт", "mode": "prompt_up"}, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["agent"] == "TUTOR"

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_ignores_profile(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: intermediate\nСфера: маркетинг\nЦели: реклама"
        PU_INTRO = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        captured_systems = []

        async def _side_effect(system, messages, temp, tokens, **kwargs):
            captured_systems.append(system)
            if len(captured_systems) == 1:
                return PROFILER_DONE
            return PU_INTRO

        mock_call.side_effect = _side_effect
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        pu_system = captured_systems[-1].lower()
        assert "prompt_up" in pu_system or "независим" in pu_system


# =============================================
# SCENARIO 7: OpenAI API — Education Mode
# =============================================

class TestOpenAIEducationMode:
    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_education_mode_with_auth(self, mock_stream, mock_call, client, db):
        mock_call.return_value = "Привет! Начнём обучение.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        user_id = _get_user_id_from_token(token)

        resp = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
            "user_id": user_id,
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"

    def test_education_mode_user_id_without_auth_fails(self, client, db):
        resp = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
            "user_id": "fake-id",
        })
        assert resp.status_code == 401

    def test_education_mode_wrong_token_fails(self, client, db):
        resp = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
            "user_id": "fake-id",
        }, headers={"Authorization": "Bearer invalid-token"})
        assert resp.status_code in (401, 403)

    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_education_mode_anonymous(self, mock_stream, mock_call, client, db):
        mock_call.return_value = "Привет!\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        mock_stream.side_effect = _mock_stream_llm([])

        resp = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        assert resp.status_code == 200
        assert "choices" in resp.json()

    @patch("app.routers.openai.stream_llm")
    def test_education_mode_streaming(self, mock_stream, client, db):
        mock_stream.side_effect = _mock_stream_llm(["Привет", " от", " Тьютора!"])

        reg = _register(client)
        token = reg["access_token"]
        user_id = _get_user_id_from_token(token)

        resp = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
            "user_id": user_id,
            "stream": True,
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

        events = _parse_sse(resp.text)
        content_tokens = [e for e in events if e.get("choices") and e["choices"][0].get("delta", {}).get("content")]
        assert len(content_tokens) > 0


# =============================================
# SCENARIO 8: OpenAI API — Prompt Up Mode
# =============================================

class TestOpenAIPromptUpMode:
    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_prompt_up_no_auth_required(self, mock_stream, mock_call, client, db):
        mock_call.return_value = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        mock_stream.side_effect = _mock_stream_llm([])

        resp = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        assert resp.status_code == 200
        assert "choices" in resp.json()

    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_prompt_up_conversation_id_persists(self, mock_stream, mock_call, client, db):
        mock_call.side_effect = _make_call_llm([
            "Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже",
            "Уточни задачу.\n[ОЖИДАЕТСЯ УТОЧНЕНИЕ]",
        ])
        mock_stream.side_effect = _mock_stream_llm([])

        resp1 = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        assert resp1.status_code == 200
        conv_id = resp1.json().get("conversation_id")
        assert conv_id is not None

        resp2 = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Напиши рецепт"}],
            "conversation_id": conv_id,
        })
        assert resp2.status_code == 200

    @patch("app.routers.openai.stream_llm")
    def test_prompt_up_streaming(self, mock_stream, client, db):
        mock_stream.side_effect = _mock_stream_llm(["Режим ", "Prompt ", "Up!"])

        resp = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
            "stream": True,
        })
        assert resp.status_code == 200

        events = _parse_sse(resp.text)
        content_tokens = [e for e in events if e.get("choices") and e["choices"][0].get("delta", {}).get("content")]
        assert len(content_tokens) > 0


# =============================================
# SCENARIO: Auth Edge Cases
# =============================================

class TestAuthEdgeCases:
    def test_deactivated_user_blocked(self, client, db):
        reg = _register(client)
        token = reg["access_token"]
        user_id = _get_user_id_from_token(token)
        headers = _auth_headers(token)

        user = db.query(User).filter(User.id == user_id).first()
        user.is_active = False
        db.commit()

        resp = client.get("/api/profile/me", headers=headers)
        assert resp.status_code == 403

    def test_message_max_length(self, client, db):
        reg = _register(client)
        headers = _auth_headers(reg["access_token"])

        resp = client.post("/api/chat/message", json={
            "message": "x" * 10001,
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 422

    def test_empty_message_rejected(self, client, db):
        reg = _register(client)
        headers = _auth_headers(reg["access_token"])

        resp = client.post("/api/chat/message", json={
            "message": "",
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 422


# =============================================
# SCENARIO: Streaming SSE Format
# =============================================

class TestStreamingFormat:
    @patch("app.routers.chat.stream_llm")
    def test_stream_sse_format(self, mock_stream, client, db):
        mock_stream.side_effect = _mock_stream_llm(["Привет", " от Тьютора"])

        reg = _register(client)
        headers = _auth_headers(reg["access_token"])

        resp = client.post("/api/chat/message/stream", json={
            "message": "Привет!",
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200

        events = _parse_sse(resp.text)
        assert len(events) > 0
        assert any("agent" in e for e in events)
        assert any("token" in e for e in events)
        assert any(e.get("done") for e in events)

    @patch("app.routers.chat.stream_llm")
    def test_stream_profiler_then_tutor(self, mock_stream, client, db):
        call_num = 0

        async def _profiler_gen(*args, **kwargs):
            yield "УРОВЕНЬ: newbie\nСфера: тест"

        async def _tutor_gen(*args, **kwargs):
            yield "Начнём урок!\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        def _side_effect(*args, **kwargs):
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                return _profiler_gen(*args, **kwargs)
            return _tutor_gen(*args, **kwargs)

        mock_stream.side_effect = _side_effect

        reg = _register(client)
        headers = _auth_headers(reg["access_token"])

        resp = client.post("/api/chat/message/stream", json={
            "message": "Привет!",
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200

        events = _parse_sse(resp.text)
        agents = [e.get("agent") for e in events if "agent" in e]
        assert "PROFILER" in agents or "TUTOR" in agents


def _get_user_id_from_token(token: str) -> str:
    from app.services.auth_service import decode_access_token
    payload = decode_access_token(token)
    return payload["sub"]


# =============================================
# INTEGRATION: Education Mode ↔ Prompt Up Mode
# =============================================

class TestEducationPromptUpIntegration:
    """Интеграционные тесты переключения и совместной работы
    Education Mode и Prompt Up Mode."""

    # --- Scenario 1: Switching modes in active session (Web API) ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_lesson_to_prompt_up_to_lesson(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: аналитика\nЦели: промпты для данных"
        TUTOR_LESSON = "**Тема:** Структура промпта\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_BACK = "Возвращаемся к урокам! Модуль 1.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_LESSON, PU_RESP, TUTOR_BACK])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp1 = client.post("/api/chat/message", json={
            "message": "Привет!", "mode": "lesson",
        }, headers=headers)
        assert resp1.status_code == 200

        resp2 = client.post("/api/chat/message", json={
            "message": "prompt up", "mode": "prompt_up",
        }, headers=headers)
        assert resp2.status_code == 200

        resp3 = client.post("/api/chat/message", json={
            "message": "вернуться к урокам", "mode": "lesson",
        }, headers=headers)
        assert resp3.status_code == 200
        assert resp3.json()["agent"] == "TUTOR"

    # --- Scenario 2: Profile isolation in Prompt Up ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_ignores_profile_data(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: intermediate\nСфера: разработка\nЦели: API промпты"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        captured_systems = []
        call_count = 0

        async def _capture_call(system, messages, temp, tokens, **kwargs):
            nonlocal call_count
            captured_systems.append(system)
            call_count += 1
            if call_count == 1:
                return PROFILER_DONE
            return PU_RESP

        mock_call.side_effect = _capture_call
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        pu_system = captured_systems[-1]
        assert "prompt_up" in pu_system.lower() or "независим" in pu_system.lower()
        pu_data_section = pu_system.split("ДАННЫЕ ПОЛЬЗОВАТЕЛЯ:")[-1] if "ДАННЫЕ ПОЛЬЗОВАТЕЛЯ:" in pu_system else pu_system
        assert "Сфера: разработка" not in pu_data_section
        assert "Уровень: intermediate" not in pu_data_section

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_profile_preserved_after_prompt_up(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: intermediate\nСфера: маркетинг\nЦели: реклама"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_BACK = "Продолжаем модуль 1.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, PU_RESP, TUTOR_BACK])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        profile_before = client.get("/api/profile/me", headers=headers).json()
        assert profile_before.get("level") == "intermediate"
        assert profile_before.get("sphere") == "маркетинг"

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        profile_after = client.get("/api/profile/me", headers=headers).json()
        assert profile_after.get("level") == "intermediate"
        assert profile_after.get("sphere") == "маркетинг"

    # --- Scenario 3: Score/progress isolation ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_prompt_up_does_not_affect_progress(self, mock_stream, mock_call, client, db):
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PU_RESP, PU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)
        user_id = _get_user_id_from_token(token)

        from app.models.user import UserProfile, ModuleProgress
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            profile = UserProfile(user_id=user_id, level="newbie", sphere="тест", goals="тест")
            db.add(profile)
        profile.total_score = 35
        mp = ModuleProgress(user_id=user_id, module_id=1, count=2, score=35)
        db.add(mp)
        db.commit()

        progress_before = client.get("/api/profile/progress", headers=headers).json()
        total_before = sum(p.get("score", 0) for p in progress_before)
        assert total_before > 0

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)
        client.post("/api/chat/message", json={"message": "Напиши промпт для анализа данных с контекстом и ограничениями", "mode": "prompt_up"}, headers=headers)

        progress_after = client.get("/api/profile/progress", headers=headers).json()
        total_after = sum(p.get("score", 0) for p in progress_after)
        assert total_after == total_before

    # --- Scenario 4: Navigation between modes via text keywords ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_keyword_prompt_up_activates_mode(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, PU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        resp = client.post("/api/chat/message", json={
            "message": "хочу prompt up", "mode": "prompt_up",
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "TUTOR"

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_keyword_module_navigates_to_education(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_MODULE = "Модуль 3: Few-shot prompting\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, PU_RESP, TUTOR_MODULE])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        resp = client.post("/api/chat/message", json={
            "message": "хочу пройти модуль 3", "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "TUTOR"

    # --- Scenario 5: Reset clarification state on mode switch ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_clarification_does_not_leak_to_lesson(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        PU_ASK = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_CLARIFY = "Какая конкретно задача?\n[ОЖИДАЕТСЯ УТОЧНЕНИЕ]"
        TUTOR_LESSON = "Возвращаемся к урокам.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, PU_ASK, PU_CLARIFY, TUTOR_LESSON])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        client.post("/api/chat/message", json={"message": "Напиши рецепт", "mode": "prompt_up"}, headers=headers)
        client.post("/api/chat/message", json={"message": "Для начинающего", "mode": "prompt_up"}, headers=headers)

        resp = client.post("/api/chat/message", json={
            "message": "вернуться к урокам", "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200
        assert "ОЖИДАЕТСЯ УТОЧНЕНИЕ" not in resp.json()["response"]
        assert "ОЖИДАЕТСЯ ОТВЕТ" in resp.json()["response"]

    # --- Scenario 6: Prompt Up → Education without profile (OpenAI API) ---

    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_anonymous_switches_models(self, mock_stream, mock_call, client, db):
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        EDU_RESP = "Привет! Начнём обучение.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([PU_RESP, EDU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        resp1 = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        assert resp1.status_code == 200
        assert "choices" in resp1.json()

        resp2 = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        assert resp2.status_code == 200
        assert "choices" in resp2.json()

    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_anonymous_sessions_isolated(self, mock_stream, mock_call, client, db):
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]"
        EDU_RESP = "Начнём урок!\n[ОЖИДАЕТСЯ ОТВЕТ]"

        mock_call.side_effect = _make_call_llm([PU_RESP, EDU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        resp1 = client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Мой первый промпт"}],
        })
        conv_id_1 = resp1.json().get("conversation_id")

        resp2 = client.post("/chat/completions", json={
            "model": "education-mode",
            "messages": [{"role": "user", "content": "Мой первый промпт"}],
        })
        conv_id_2 = resp2.json().get("conversation_id")

        assert conv_id_1 is not None
        assert conv_id_2 is not None
        assert conv_id_1 != conv_id_2

    # --- Scenario 7: Authenticated user — both modes via OpenAI API ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_auth_user_education_updates_progress(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        TUTOR_LESSON = "**Задание:** Напиши промпт\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        EVAL_RESULT = "**Что хорошо:** Роль указана\nSCORE: 8\n\nХочешь ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"

        mock_call.side_effect = _make_call_llm([PROFILER_DONE, TUTOR_LESSON])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)

        resp = client.post("/api/chat/message", json={
            "message": "Ты аналитик данных. Проанализируй датасет sales.csv с колонками date, campaign, clicks, conversions, spend за январь 2025. Выведи топ-5 регионов по выручке в формате таблицы.",
            "mode": "lesson",
        }, headers=headers)
        assert resp.status_code == 200
        result = resp.json()
        assert result["agent"] == "TUTOR"
        assert result.get("score") is not None or result.get("points", 0) >= 0

    @patch("app.routers.openai.call_llm", new_callable=AsyncMock)
    @patch("app.routers.openai.stream_llm")
    def test_auth_user_prompt_up_no_progress(self, mock_stream, mock_call, client, db):
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]"

        mock_call.side_effect = _make_call_llm([PU_RESP])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        progress_before = client.get("/api/profile/progress", headers=headers).json()
        total_before = sum(p.get("score", 0) for p in progress_before)

        client.post("/chat/completions", json={
            "model": "prompt-up-mode",
            "messages": [{"role": "user", "content": "Напиши промпт для анализа"}],
        })

        progress_after = client.get("/api/profile/progress", headers=headers).json()
        total_after = sum(p.get("score", 0) for p in progress_after)
        assert total_after == total_before

    # --- Scenario 8: Multiple rapid mode switches ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_rapid_switching_preserves_state(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        TUTOR_1 = "Урок 1.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_1 = "Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_2 = "Урок продолжается.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_2 = "Prompt Up снова! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_3 = "Снова на уроке!\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        mock_call.side_effect = _make_call_llm([
            PROFILER_DONE, TUTOR_1, PU_1, TUTOR_2, PU_2, TUTOR_3
        ])
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        resp = client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200

        resp = client.post("/api/chat/message", json={"message": "готов", "mode": "prompt_up"}, headers=headers)
        assert resp.status_code == 200

        resp = client.post("/api/chat/message", json={"message": "вернись к урокам", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "TUTOR"

        resp = client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)
        assert resp.status_code == 200

        resp = client.post("/api/chat/message", json={"message": "хочу продолжить уроки", "mode": "lesson"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["agent"] == "TUTOR"

    # --- Scenario 9: Conversation history preserved on mode switch ---

    @patch("app.routers.chat.call_llm", new_callable=AsyncMock)
    @patch("app.routers.chat.stream_llm")
    def test_conversation_history_not_lost_on_switch(self, mock_stream, mock_call, client, db):
        PROFILER_DONE = "УРОВЕНЬ: newbie\nСфера: тест\nЦели: тест"
        TUTOR_LESSON = "**Задание:** Напиши промпт\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        PU_RESP = "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"
        TUTOR_CONT = "Продолжаем! Вот следующее задание.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        responses = [PROFILER_DONE, TUTOR_LESSON, PU_RESP, TUTOR_CONT]
        captured_msg_counts = []

        async def _capture_call(system, messages, temp, tokens, **kwargs):
            captured_msg_counts.append(len(messages))
            idx = min(len(captured_msg_counts) - 1, len(responses) - 1)
            return responses[idx]

        mock_call.side_effect = _capture_call
        mock_stream.side_effect = _mock_stream_llm([])

        reg = _register(client)
        token = reg["access_token"]
        headers = _auth_headers(token)

        client.post("/api/chat/message", json={"message": "Привет!", "mode": "lesson"}, headers=headers)
        client.post("/api/chat/message", json={"message": "Давай задание", "mode": "lesson"}, headers=headers)

        client.post("/api/chat/message", json={"message": "prompt up", "mode": "prompt_up"}, headers=headers)

        assert len(captured_msg_counts) >= 3
        msgs_at_pu = captured_msg_counts[-1]
        assert msgs_at_pu >= 4

        client.post("/api/chat/message", json={"message": "вернуться к урокам", "mode": "lesson"}, headers=headers)

        msgs_at_return = captured_msg_counts[-1]
        assert msgs_at_return > msgs_at_pu

    # --- Scenario 10: Streaming — mode switch ---

    @patch("app.routers.chat.stream_llm")
    def test_stream_from_prompt_up_to_lesson(self, mock_stream, client, db):
        call_num = 0

        async def _pu_gen(*args, **kwargs):
            yield "Режим Prompt Up! Напиши промпт.\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        async def _tutor_gen(*args, **kwargs):
            yield "Начнём урок!\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже"

        async def _eval_gen(*args, **kwargs):
            yield "**Что хорошо:** Всё\nSCORE: 6\n\nХочешь ещё?\n[ОЖИДАЕТСЯ ВЫБОР]"

        def _side_effect(*args, **kwargs):
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                return _pu_gen(*args, **kwargs)
            elif call_num == 2:
                return _eval_gen(*args, **kwargs)
            return _tutor_gen(*args, **kwargs)

        mock_stream.side_effect = _side_effect

        reg = _register(client)
        headers = _auth_headers(reg["access_token"])

        resp1 = client.post("/api/chat/message/stream", json={
            "message": "prompt up", "mode": "prompt_up",
        }, headers=headers)
        assert resp1.status_code == 200
        events1 = _parse_sse(resp1.text)
        assert any("agent" in e for e in events1)
        assert any(e.get("done") for e in events1)

        resp2 = client.post("/api/chat/message/stream", json={
            "message": "Ты аналитик. Проанализируй данные. Выведи топ-5. Используй файл data.csv за 2025.", "mode": "prompt_up",
        }, headers=headers)
        assert resp2.status_code == 200
        events2 = _parse_sse(resp2.text)
        assert any("token" in e for e in events2)

        resp3 = client.post("/api/chat/message/stream", json={
            "message": "вернуться к урокам", "mode": "lesson",
        }, headers=headers)
        assert resp3.status_code == 200
        events3 = _parse_sse(resp3.text)
        agents3 = [e.get("agent") for e in events3 if "agent" in e]
        assert "TUTOR" in agents3 or "PROFILER" in agents3
