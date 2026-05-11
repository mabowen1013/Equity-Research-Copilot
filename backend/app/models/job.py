from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_jobs_progress_range",
        ),
        CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count_nonnegative"),
    )

    id = Column(Integer, primary_key=True)
    job_type = Column(String(64), nullable=False, index=True)
    company_id = Column(Integer, nullable=True, index=True)
    status = Column(String(32), nullable=False, server_default="pending", index=True)
    progress = Column(Integer, nullable=False, server_default="0")
    retry_count = Column(Integer, nullable=False, server_default="0")
    payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
