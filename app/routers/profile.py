from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import MODULE_COMPLETION_SCORE
from app.database import get_db
from app.models.user import User
from app.schemas.progress import ProfileOut, ProgressOut
from app.services.auth_service import get_current_user
from app.services.progress_service import get_or_create_profile, get_module_progress_map
from app.services.scoring_service import MODULE_NAMES, MODULE_ORDER, get_module_badge

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, user)
    modules = get_module_progress_map(db, user.id)

    badges = []
    for mid in MODULE_ORDER:
        mp = modules.get(mid)
        if mp and mp.score >= MODULE_COMPLETION_SCORE:
            badge = get_module_badge(mid, mp.score)
            if badge:
                badges.append(badge)

    return ProfileOut(
        username=user.username,
        email=user.email,
        level=profile.level,
        sphere=profile.sphere,
        goals=profile.goals,
        total_score=profile.total_score,
        badges=badges,
        created_at=profile.created_at.isoformat() if profile.created_at else "",
        tasks_count=sum(mp.count for mp in modules.values()),
        modules_completed=sum(1 for mp in modules.values() if mp.is_completed),
        modules_total=len(MODULE_ORDER),
        tutor_introduced=profile.tutor_introduced,
    )


@router.get("/progress", response_model=list[ProgressOut])
def get_progress(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, user)
    modules = get_module_progress_map(db, user.id)
    result = []
    for mid in MODULE_ORDER:
        mp = modules.get(mid)
        score = mp.score if mp else 0
        count = mp.count if mp else 0
        avg = mp.avg if mp else 0.0
        completed = mp.is_completed if mp else False
        badge = get_module_badge(mid, score)
        result.append(ProgressOut(
            module_id=mid,
            module_name=MODULE_NAMES.get(mid, ""),
            score=score,
            max_score=50,
            avg_score=round(avg, 1),
            count=count,
            completed=completed,
            badge=badge,
        ))
    return result
