import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.database import SessionLocal, Base, engine
from app.models.user import User
from app.services.auth_service import hash_password, create_access_token, decode_access_token


Base.metadata.create_all(bind=engine)


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
        username="testuser",
        email="test@test.com",
        hashed_password=hash_password("testpass123"),
        level="newbie",
        sphere="",
        goals="",
        total_score="0",
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
        response = client.post("/api/auth/register", json={
            "username": "newuser",
            "email": "new@test.com",
            "password": "pass123456"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        cleanup_user = db.query(User).filter(User.username == "newuser").first()
        if cleanup_user:
            db.delete(cleanup_user)
            db.commit()

    def test_register_duplicate(self, client, test_user):
        response = client.post("/api/auth/register", json={
            "username": "testuser",
            "email": "other@test.com",
            "password": "pass123456"
        })
        assert response.status_code == 400

    def test_login(self, client, test_user):
        response = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpass123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    def test_login_wrong_password(self, client, test_user):
        response = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "wrongpass"
        })
        assert response.status_code == 401


class TestProfile:
    def test_get_profile(self, client, auth_headers, test_user):
        response = client.get("/api/profile/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"
        assert data["level"] == "newbie"

    def test_unauthorized(self, client):
        response = client.get("/api/profile/me")
        assert response.status_code in (401, 403)


class TestAssignments:
    def test_list_assignments(self, client, auth_headers):
        response = client.get("/api/assignments/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_assignments_by_module(self, client, auth_headers):
        response = client.get("/api/assignments/?module_id=1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert all(a["module_id"] == 1 for a in data)


class TestScoring:
    def test_calculate_level_newbie(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(0) == "newbie"
        assert calculate_level(15) == "newbie"
        assert calculate_level(30) == "newbie"

    def test_calculate_level_intermediate(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(31) == "intermediate"
        assert calculate_level(50) == "intermediate"

    def test_calculate_level_advanced(self):
        from app.services.scoring_service import calculate_level
        assert calculate_level(71) == "advanced"
        assert calculate_level(100) == "advanced"


class TestEvaluator:
    def test_extract_score(self):
        from app.agents.evaluator import EvaluatorAgent
        evaluator = EvaluatorAgent()

        assert evaluator.extract_score("SCORE: 8") == 8
        assert evaluator.extract_score("SCORE: 0") == 0
        assert evaluator.extract_score("SCORE: 10") == 10
        assert evaluator.extract_score("Some text without score") == 5

    def test_extract_score_clamped(self):
        from app.agents.evaluator import EvaluatorAgent
        evaluator = EvaluatorAgent()
        assert evaluator.extract_score("SCORE: 15") == 10
        assert evaluator.extract_score("SCORE: -3") == 0


class TestProfiler:
    def test_parse_profile_newbie(self):
        from app.agents.profiler import ProfilerAgent
        profiler = ProfilerAgent()
        result = profiler.parse_profile(
            "УРОВЕНЬ: newbie | СФЕРА: маркетинг | ЦЕЛИ: научиться писать промпты | ОБОСНОВАНИЕ: нет опыта"
        )
        assert result["level"] == "newbie"
        assert result["sphere"] == "маркетинг"

    def test_parse_profile_intermediate(self):
        from app.agents.profiler import ProfilerAgent
        profiler = ProfilerAgent()
        result = profiler.parse_profile(
            "УРОВЕНЬ: intermediate | СФЕРА: разработка | ЦЕЛИ: систематизировать знания"
        )
        assert result["level"] == "intermediate"

    def test_parse_profile_russian(self):
        from app.agents.profiler import ProfilerAgent
        profiler = ProfilerAgent()
        result = profiler.parse_profile(
            "УРОВЕНЬ: средний | СФЕРА: аналитика | ЦЕЛИ: продвинутые техники"
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
