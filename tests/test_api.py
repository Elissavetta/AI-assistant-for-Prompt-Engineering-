import pytest
from fastapi.testclient import TestClient

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
