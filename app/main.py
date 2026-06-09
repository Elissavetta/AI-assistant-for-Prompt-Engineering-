import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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


async def _session_cleanup_task():
    from app.services.session_cache import cleanup_expired_sessions
    while True:
        await asyncio.sleep(60)
        cleanup_expired_sessions()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    logger.info("PROMPT UP started")
    task = asyncio.create_task(_session_cleanup_task())
    yield
    task.cancel()
    logger.info("PROMPT UP shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description="PROMPT UP — обучающий тренажёр промпт-инжиниринга",
    version="2.0.0",
    lifespan=lifespan,
)

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
