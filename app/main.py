from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import init_db, SessionLocal
from app.models.assignment import Assignment
from app.prompts import SEED_ASSIGNMENTS
from app.routers import auth_router, chat_router, profile_router, assignments_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_assignments()
    yield


def _seed_assignments():
    db = SessionLocal()
    try:
        existing = db.query(Assignment).first()
        if not existing:
            for a_data in SEED_ASSIGNMENTS:
                assignment = Assignment(**a_data)
                db.add(assignment)
            db.commit()
    finally:
        db.close()


app = FastAPI(
    title=settings.APP_NAME,
    description="PROMPT UP — обучающий тренажёр промпт-инжиниринга",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(assignments_router, prefix="/api")

frontend_path = Path(__file__).parent.parent / "frontend"

if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path / "css")), name="static-css")
    app.mount("/js", StaticFiles(directory=str(frontend_path / "js")), name="static-js")


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


@app.get("/dashboard")
async def serve_dashboard():
    dashboard = frontend_path / "dashboard.html"
    if dashboard.exists():
        return FileResponse(str(dashboard))
    return {"message": "Dashboard page not found"}
