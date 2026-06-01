from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.assignment import Assignment, Submission
from app.models.progress import Progress
from app.schemas.progress import AssignmentOut, SubmitAnswer, SubmissionOut
from app.services.auth_service import decode_access_token
from app.services.scoring_service import (
    calculate_level, score_to_points, MODULE_NAMES, MODULE_BADGES, get_module_badge,
)
from app.agents.evaluator import EvaluatorAgent

router = APIRouter(prefix="/assignments", tags=["assignments"])
security = HTTPBearer()

evaluator = EvaluatorAgent()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/", response_model=list[AssignmentOut])
def list_assignments(
    module_id: int = None,
    difficulty: str = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Assignment)
    if module_id:
        query = query.filter(Assignment.module_id == module_id)
    if difficulty:
        query = query.filter(Assignment.difficulty == difficulty)
    assignments = query.order_by(Assignment.module_id, Assignment.order_num).all()
    return [
        AssignmentOut(
            id=a.id,
            module_id=a.module_id,
            title=a.title,
            description=a.description,
            task_text=a.task_text,
            difficulty=a.difficulty,
            hint=a.hint,
            order_num=a.order_num,
        )
        for a in assignments
    ]


@router.get("/{assignment_id}", response_model=AssignmentOut)
def get_assignment(
    assignment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    a = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return AssignmentOut(
        id=a.id,
        module_id=a.module_id,
        title=a.title,
        description=a.description,
        task_text=a.task_text,
        difficulty=a.difficulty,
        hint=a.hint,
        order_num=a.order_num,
    )


@router.post("/{assignment_id}/submit", response_model=SubmissionOut)
async def submit_answer(
    assignment_id: str,
    data: SubmitAnswer,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    eval_messages = [
        {"role": "system", "content": f"Задание: {assignment.task_text}\nКритерии оценки: {assignment.criteria}"},
        {"role": "user", "content": f"Вот мой ответ на задание:\n\n{data.answer}"},
    ]

    feedback = await evaluator.evaluate(eval_messages, assignment.criteria)
    score = evaluator.extract_score(feedback)
    points = score_to_points(score)

    submission = Submission(
        user_id=user.id,
        assignment_id=assignment_id,
        answer=data.answer,
        feedback=feedback,
        score=score,
    )
    db.add(submission)

    current_total = int(user.total_score) + points
    user.total_score = str(current_total)
    user.level = calculate_level(current_total)

    progress = db.query(Progress).filter(
        Progress.user_id == user.id,
        Progress.module_id == assignment.module_id,
    ).first()

    if not progress:
        progress = Progress(
            user_id=user.id,
            module_id=assignment.module_id,
            module_name=MODULE_NAMES.get(assignment.module_id, f"Module {assignment.module_id}"),
            score=points,
        )
        db.add(progress)
    else:
        progress.score += points

    module_assignments = db.query(Assignment).filter(Assignment.module_id == assignment.module_id).all()
    max_possible = len(module_assignments) * 10
    progress.max_score = max_possible
    progress.completed = progress.score >= max_possible * 0.7
    progress.badge = get_module_badge(assignment.module_id, progress.score, max_possible)

    db.commit()
    db.refresh(submission)

    return SubmissionOut(
        id=submission.id,
        assignment_id=submission.assignment_id,
        answer=submission.answer,
        feedback=submission.feedback,
        score=submission.score,
    )


@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    subs = db.query(Submission).filter(Submission.user_id == user.id).all()
    return [
        SubmissionOut(
            id=s.id,
            assignment_id=s.assignment_id,
            answer=s.answer,
            feedback=s.feedback,
            score=s.score,
        )
        for s in subs
    ]
