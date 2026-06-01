from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.profile import router as profile_router
from app.routers.assignments import router as assignments_router

__all__ = ["auth_router", "chat_router", "profile_router", "assignments_router"]
