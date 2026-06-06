from pydantic import BaseModel


class ProfileOut(BaseModel):
    username: str
    email: str
    level: str
    sphere: str
    goals: str
    total_score: int
    badges: list[str] = []
    created_at: str = ""
    tasks_count: int = 0
    modules_completed: int = 0
    modules_total: int = 6


class ProgressOut(BaseModel):
    module_id: int
    module_name: str
    score: int
    max_score: int
    avg_score: float
    count: int
    completed: bool
    badge: str
