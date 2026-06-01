import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer
from sqlalchemy.orm import relationship

from app.database import Base

_utcnow = lambda: datetime.now(timezone.utc)


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    module_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    task_text = Column(Text, nullable=False)
    difficulty = Column(String, default="newbie")
    hint = Column(Text, default="")
    criteria = Column(Text, default="")
    order_num = Column(Integer, default=0)

    submissions = relationship("Submission", back_populates="assignment")


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    assignment_id = Column(String, ForeignKey("assignments.id"), nullable=False)
    answer = Column(Text, nullable=False)
    feedback = Column(Text, default="")
    score = Column(Integer, default=0)
    submitted_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="submissions")
    assignment = relationship("Assignment", back_populates="submissions")
