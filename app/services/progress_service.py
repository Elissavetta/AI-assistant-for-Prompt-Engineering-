from sqlalchemy.orm import Session

from app.models.user import User, UserProfile, ModuleProgress
from app.services.scoring_service import MODULE_ORDER


def get_or_create_profile(db: Session, user: User) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        _ensure_module_progress(db, user.id)
        db.commit()
        db.refresh(profile)
    return profile


def _ensure_module_progress(db: Session, user_id: str):
    existing = {mp.module_id for mp in db.query(ModuleProgress).filter(ModuleProgress.user_id == user_id).all()}
    for module_id in MODULE_ORDER:
        if module_id not in existing:
            db.add(ModuleProgress(user_id=user_id, module_id=module_id))


def get_module_progress_map(db: Session, user_id: str) -> dict[int, ModuleProgress]:
    rows = db.query(ModuleProgress).filter(ModuleProgress.user_id == user_id).all()
    return {mp.module_id: mp for mp in rows}
