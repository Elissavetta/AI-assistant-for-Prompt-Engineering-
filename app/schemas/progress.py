from pydantic import BaseModel
from typing import Optional, List


class ProfileOut(BaseModel):
    username: str
    email: str
    level: str
    sphere: str
    goals: str
    total_score: int
    badges: List[str] = []


class ProgressOut(BaseModel):
    module_id: int
    module_name: str
    score: int
    max_score: int
    completed: bool
    badge: str


class SubmitAnswer(BaseModel):
    assignment_id: str
    answer: str


class SubmissionOut(BaseModel):
    id: str
    assignment_id: str
    answer: str
    feedback: str
    score: int


class AssignmentOut(BaseModel):
    id: str
    module_id: int
    title: str
    description: str
    task_text: str
    difficulty: str
    hint: str
    order_num: int
