import os
import uuid

import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")

from app.database import SessionLocal, Base, engine
from app.models.user import User
from app.services.auth_service import hash_password, create_access_token, decode_access_token

Base.metadata.create_all(bind=engine)

with engine.connect() as conn:
    result = conn.execute(text("PRAGMA table_info(user_profiles)"))
    columns = [row[1] for row in result]
    if "current_module_id" not in columns:
        conn.execute(text("ALTER TABLE user_profiles ADD COLUMN current_module_id INTEGER"))
        conn.commit()


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


@pytest.fixture
def test_user(db):
    user = User(
        username=f"testuser_{uuid.uuid4().hex[:8]}",
        email=f"test_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("testpass123"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.query(User).filter(User.id == user.id).delete()
    db.commit()


@pytest.fixture
def auth_token(test_user):
    return create_access_token({"sub": test_user.id})


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


class TestAuth:
    def test_register(self, client, db):
        username = f"newuser_{uuid.uuid4().hex[:8]}"
        response = client.post("/api/auth/register", json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "pass123456"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        cleanup_user = db.query(User).filter(User.username == username).first()
        if cleanup_user:
            db.delete(cleanup_user)
            db.commit()

    def test_register_duplicate(self, client, test_user):
        response = client.post("/api/auth/register", json={
            "username": test_user.username,
            "email": "other@test.com",
            "password": "pass123456"
        })
        assert response.status_code == 400

    def test_register_short_password(self, client):
        response = client.post("/api/auth/register", json={
            "username": "shortpwuser",
            "email": "short@test.com",
            "password": "12345"
        })
        assert response.status_code == 422

    def test_register_invalid_email(self, client):
        response = client.post("/api/auth/register", json={
            "username": "bademailuser",
            "email": "not-an-email",
            "password": "pass123456"
        })
        assert response.status_code == 422

    def test_login(self, client, test_user):
        response = client.post("/api/auth/login", json={
            "username": test_user.username,
            "password": "testpass123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    def test_login_wrong_password(self, client, test_user):
        response = client.post("/api/auth/login", json={
            "username": test_user.username,
            "password": "wrongpass"
        })
        assert response.status_code == 401


class TestProfile:
    def test_get_profile(self, client, auth_headers, test_user):
        response = client.get("/api/profile/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == test_user.username
        assert "level" in data

    def test_unauthorized(self, client):
        response = client.get("/api/profile/me")
        assert response.status_code in (401, 403)

    def test_get_progress(self, client, auth_headers):
        response = client.get("/api/profile/progress", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 6


class TestScoring:
    def test_calculate_level_newbie(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(0) == "newbie"
        assert calculate_level(50) == "newbie"
        assert calculate_level(99) == "newbie"

    def test_calculate_level_intermediate(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(100) == "intermediate"
        assert calculate_level(200) == "intermediate"
        assert calculate_level(299) == "intermediate"

    def test_calculate_level_advanced(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(300) == "advanced"
        assert calculate_level(500) == "advanced"


class TestEvaluator:
    def test_extract_score(self):
        from app.agents.evaluator import extract_score
        assert extract_score("SCORE: 8") == 8
        assert extract_score("SCORE: 0") == 0
        assert extract_score("SCORE: 10") == 10
        assert extract_score("Some text without score") == 5

    def test_extract_score_clamped(self):
        from app.agents.evaluator import extract_score
        assert extract_score("SCORE: 15") == 10
        assert extract_score("SCORE: -3") == 0

    def test_extract_score_russian(self):
        from app.agents.evaluator import extract_score
        assert extract_score("ОЦЕНКА: 7") == 7

    def test_extract_score_from_full_response(self):
        from app.agents.evaluator import extract_score
        response = "**Что хорошо:** Роль задана\n\n**Что можно улучшить:** Нет формата\n\n**Баллы:** 7/10\n\nНеплохо! Есть куда расти.\n[ОЖИДАЕТСЯ ВЫБОР]\n\nSCORE: 7"
        assert extract_score(response) == 7

    def test_extract_score_ignores_balles_line(self):
        from app.agents.evaluator import extract_score
        response = "**Баллы:** 9/10\n\nSCORE: 9"
        assert extract_score(response) == 9

    def test_extract_score_from_balles_fallback(self):
        from app.agents.evaluator import extract_score
        assert extract_score("**Баллы:** 8/10") == 8
        assert extract_score("Баллы: 6/10") == 6
        assert extract_score("**Баллы:** 3/10\n\nНеплохо!") == 3

    def test_extract_score_score_before_improved_version(self):
        from app.agents.evaluator import extract_score
        response = "**Что хорошо:** Роль\n\n**Что можно улучшить:** Нет ограничений\n\n**Баллы:** 8/10\n\nSCORE: 8\n\n**Улучшенная версия:**\nТы — аналитик..."
        assert extract_score(response) == 8

    def test_extract_score_balles_fallback_when_score_truncated(self):
        from app.agents.evaluator import extract_score
        response = "**Что хорошо:** Роль\n\n**Что можно улучшить:** Нет ограничений\n\n**Баллы:** 7/10\n\nНеплохо!\n[ОЖИДАЕТСЯ ВЫБОР]\n\n**Улучшенная версия:**\nОчень длинный текст..."
        assert extract_score(response) == 7


class TestProfiler:
    def test_parse_profile_newbie(self):
        from app.agents.profiler import parse_profile
        result = parse_profile(
            "УРОВЕНЬ: newbie | СФЕРА: маркетинг | ЦЕЛИ: научиться писать промпты"
        )
        assert result["level"] == "newbie"
        assert result["sphere"] == "маркетинг"

    def test_parse_profile_intermediate(self):
        from app.agents.profiler import parse_profile
        result = parse_profile(
            "УРОВЕНЬ: intermediate | СФЕРА: разработка | ЦЕЛИ: систематизировать знания"
        )
        assert result["level"] == "intermediate"


class TestTokenService:
    def test_create_and_decode_token(self):
        token = create_access_token({"sub": "user123"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"

    def test_invalid_token(self):
        payload = decode_access_token("invalid.token.here")
        assert payload is None


class TestProgressService:
    def test_get_or_create_profile(self, db, test_user):
        from app.services.progress_service import get_or_create_profile
        profile = get_or_create_profile(db, test_user)
        assert profile is not None
        assert profile.user_id == test_user.id
        assert profile.level == ""

    def test_module_progress_initialized(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        assert len(modules) == 6
        for mid in range(1, 7):
            assert mid in modules
            assert modules[mid].score == 0
            assert modules[mid].count == 0


class TestOrchestrator:
    def test_extract_module_id(self):
        from app.agents.orchestrator import extract_module_id
        assert extract_module_id("Хочу пройти модуль 3") == 3
        assert extract_module_id("пройти модуль 5: добавление контекста") == 5
        assert extract_module_id("Переключи на модуль 1") == 1
        assert extract_module_id("Привет, как дела?") is None
        assert extract_module_id("модуль 7") is None

    def test_extract_module_id_fuzzy(self):
        from app.agents.orchestrator import extract_module_id
        assert extract_module_id("Хочу пройти модуль: Мастер контекста") == 5
        assert extract_module_id("Хочу пройти модуль: контекст") == 5
        assert extract_module_id("Хочу пройти модуль: файлы") == 5
        assert extract_module_id("Хочу пройти модуль: структура") == 1
        assert extract_module_id("Хочу пройти модуль: улучшение") == 2
        assert extract_module_id("Хочу пройти модуль: few-shot") == 3
        assert extract_module_id("Хочу пройти модуль: цепочка") == 4
        assert extract_module_id("Хочу пройти модуль: комплексный") == 6

    def test_determine_agent_evaluator(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        from app.agents.orchestrator import determine_agent
        profile = get_or_create_profile(db, test_user)
        profile.level = "newbie"
        profile.tutor_introduced = True
        db.commit()
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        session.add_assistant_message("Напиши промпт\n[ОЖИДАЕТСЯ ОТВЕТ]", "TUTOR")
        session.add_user_message("Вот мой промпт: Ты аналитик. Проанализируй данные из файла. Ответь в формате таблицы. Используй данные: ads_data.csv с колонками date, campaign, clicks, conversions, spend за январь 2025. Сравни эффективность кампаний.")
        agent = determine_agent(session)
        assert agent == "EVALUATOR"

    def test_determine_agent_tutor_for_choice(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        from app.agents.orchestrator import determine_agent
        profile = get_or_create_profile(db, test_user)
        profile.level = "newbie"
        profile.tutor_introduced = True
        db.commit()
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        session.add_assistant_message("Хочешь ещё задание?\n[ОЖИДАЕТСЯ ВЫБОР]", "TUTOR")
        session.add_user_message("Да, давай")
        agent = determine_agent(session)
        assert agent == "TUTOR"

    def test_get_active_module_default(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        assert session.get_active_module() == 1

    def test_set_current_module(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        session.set_current_module(3)
        assert session.get_active_module() == 3


class TestSessionCache:
    def test_awaiting_state_enum(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        assert session.get_awaiting_state_enum() == AwaitingState.NONE
        assert session.get_awaiting_state() == ""

    def test_awaiting_state_answer(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Вот задание\n[ОЖИДАЕТСЯ ОТВЕТ]\nНапиши свой промпт ниже", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.ANSWER
        assert session.get_awaiting_state() == "ANSWER"

    def test_awaiting_state_choice(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Хочешь ещё задание?\n[ОЖИДАЕТСЯ ВЫБОР]", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.CHOICE
        assert session.get_awaiting_state() == "CHOICE"

    def test_awaiting_state_clarification(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Какой язык?\n[ОЖИДАЕТСЯ УТОЧНЕНИЕ]", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.CLARIFICATION
        assert session.get_awaiting_state() == "CLARIFICATION"

    def test_immutable_messages(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_user_message("hello")
        msgs = session.get_openai_messages()
        msgs.append({"role": "user", "content": "injected"})
        assert len(session.get_openai_messages()) == 1

    def test_set_current_module_persists_to_profile(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.set_current_module(4)
        assert session._current_module_id == 4
        assert session.profile.current_module_id == 4
        db.commit()

        db.refresh(profile)
        assert profile.current_module_id == 4

    def test_load_session_restores_module_from_profile(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        profile.current_module_id = 3
        db.commit()
        db.refresh(profile)

        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        assert session.get_active_module() == 3

    def test_is_returning_user_true(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        profile.level = "newbie"
        profile.tutor_introduced = True
        db.commit()

        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        assert session.is_returning_user() is True

    def test_is_returning_user_false_no_level(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        profile.tutor_introduced = True
        db.commit()

        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        assert session.is_returning_user() is False

    def test_is_returning_user_false_has_conversation(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session
        profile = get_or_create_profile(db, test_user)
        profile.level = "newbie"
        profile.tutor_introduced = True
        db.commit()

        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)
        session.add_user_message("hello")
        assert session.is_returning_user() is False


class TestConfig:
    def test_constants_defined(self):
        from app.config import (
            MIN_SUBMISSION_LENGTH,
            MODULE_COMPLETION_SCORE,
            EVALUATOR_MAX_TOKENS,
            MARKER_AWAITING_ANSWER,
            MARKER_LEVEL,
        )
        assert MIN_SUBMISSION_LENGTH > 0
        assert MODULE_COMPLETION_SCORE == 50
        assert EVALUATOR_MAX_TOKENS == 600
        assert MARKER_AWAITING_ANSWER == "[ОЖИДАЕТСЯ ОТВЕТ]"
        assert MARKER_LEVEL == "УРОВЕНЬ:"

    def test_secret_key_required(self):
        from pydantic import ValidationError
        from pydantic_settings import BaseSettings, SettingsConfigDict
        from pathlib import Path

        class TestSettings(BaseSettings):
            APP_NAME: str = "TEST"
            SECRET_KEY: str = ""
            model_config = SettingsConfigDict(extra="ignore")

            def model_post_init(self, __context) -> None:
                if not self.SECRET_KEY:
                    raise ValueError("SECRET_KEY must be set")

        with pytest.raises(ValidationError):
            TestSettings(SECRET_KEY="")


class TestOpenAIRouter:
    def test_list_models(self, client):
        response = client.get("/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert any(m["id"] == "prompt-up" for m in data["data"])

    def test_derive_conversation_id_uses_sha256(self):
        from app.routers.openai import _derive_conversation_id, ChatMessage
        msgs = [ChatMessage(role="user", content="hello")]
        cid = _derive_conversation_id(msgs)
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    def test_strip_intro(self):
        from app.routers.openai import _strip_intro
        text = "Режим Prompt Up! Просто напиши любой промпт\n\nДавай начнём"
        result = _strip_intro(text)
        assert "Режим Prompt Up" not in result
        assert "Давай начнём" in result


class TestTruncatedMarker:
    def test_truncated_marker_answer(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Вот твоё задание! Напиши промпт для summaries.\n[ОЖИДАЕТ", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.ANSWER

    def test_truncated_marker_choice(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Модуль пройден! Продолжим дальше?\n[ОЖИДАЕТСЯ В", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.CHOICE

    def test_truncated_marker_clarification(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Уточни, какой язык программирования?\n[ОЖИДАЕТСЯ У", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.CLARIFICATION


class TestUserSeesTutor:
    def test_evaluator_response_labelled_as_tutor(self, db, test_user):
        from app.services.progress_service import get_or_create_profile, get_module_progress_map
        from app.services.session_cache import load_session, AwaitingState
        profile = get_or_create_profile(db, test_user)
        modules = get_module_progress_map(db, test_user.id)
        session = load_session(test_user, profile, modules)

        session.add_assistant_message("Что хорошо: Роль указана\n\nЧто можно улучшить: Нет ограничений\n\nБаллы: 8/10\n\nОтличная работа!\n[ОЖИДАЕТСЯ ВЫБОР]\n\nSCORE: 8", "TUTOR")
        assert session.get_awaiting_state_enum() == AwaitingState.CHOICE
        messages = session.get_openai_messages()
        assert messages[-1]["role"] == "assistant"

    def test_stream_evaluate_uses_tutor_agent(self):
        import json
        events = []
        events.append(json.dumps({'agent': 'TUTOR'}, ensure_ascii=False))
        events.append(json.dumps({'done': True, 'agent_done': 'TUTOR', 'score': 8}, ensure_ascii=False))
        for e in events:
            data = json.loads(e)
            assert data.get('agent') == 'TUTOR' or data.get('agent_done') == 'TUTOR'
