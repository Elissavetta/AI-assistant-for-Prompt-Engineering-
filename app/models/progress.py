import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, Boolean
from sqlalchemy.orm import relationship

from app.database import Base

_utcnow = lambda: datetime.now(timezone.utc)


class Progress(Base):
    __tablename__ = "progress"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    module_id = Column(Integer, nullable=False)
    module_name = Column(String, nullable=False)
    score = Column(Integer, default=0)
    max_score = Column(Integer, default=50)
    completed = Column(Boolean, default=False)
    badge = Column(String, default="")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="progress_records")
