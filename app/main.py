import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings, logger
from app.database import init_db
from app.routers import auth_router, chat_router, profile_router
from app.routers.openai import router as openai_router


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class _RateLimitEntry:
    __slots__ = ("timestamps",)

    def __init__(self):
        self.timestamps: list[float] = []


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clients: dict[str, _RateLimitEntry] = defaultdict(_RateLimitEntry)

    def is_allowed(self, key: str) -> bool:
        entry = self._clients[key]
        now = time.monotonic()
        cutoff = now - self.window_seconds
        entry.timestamps = [t for t in entry.timestamps if t > cutoff]
        if len(entry.timestamps) >= self.max_requests:
            return False
        entry.timestamps.append(now)
        return True


_rate_limiter = RateLimiter(max_requests=settings.RATE_LIMIT_PER_MINUTE)

_RATE_LIMITED_PATHS = ("/api/chat/message", "/chat/completions")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    logger.info("PROMPT UP started")
    yield
    from app.services.session_cache import close_store
    await close_store()
    logger.info("PROMPT UP shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description="PROMPT UP — обучающий тренажёр промпт-инжиниринга",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if any(request.url.path.startswith(p) for p in _RATE_LIMITED_PATHS):
        client_key = request.client.host if request.client else "unknown"
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            client_key = auth[7:27]
        if not _rate_limiter.is_allowed(client_key):
            return Response(content="Rate limit exceeded", status_code=429)
    return await call_next(request)

app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(openai_router)

frontend_path = Path(__file__).parent.parent / "frontend"

if frontend_path.exists():
    app.mount("/css", StaticFiles(directory=str(frontend_path / "css")), name="static-css")
    app.mount("/js", StaticFiles(directory=str(frontend_path / "js")), name="static-js")
    app.mount("/sans", StaticFiles(directory=str(frontend_path / "sans")), name="static-sans")
    app.mount("/webfonts", StaticFiles(directory=str(frontend_path / "webfonts")), name="static-webfonts")


@app.get("/")
async def serve_index():
    index = frontend_path / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AI Prompt Trainer API", "docs": "/docs"}


@app.get("/login")
async def serve_login():
    login = frontend_path / "login.html"
    if login.exists():
        return FileResponse(str(login))
    return {"message": "Login page not found"}


@app.get("/register")
async def serve_register():
    register = frontend_path / "index.html"
    if register.exists():
        return FileResponse(str(register))
    return {"message": "Register page not found"}


@app.get("/dashboard")
async def serve_dashboard():
    dashboard = frontend_path / "dashboard.html"
    if dashboard.exists():
        return FileResponse(str(dashboard))
    return {"message": "Dashboard page not found"}
