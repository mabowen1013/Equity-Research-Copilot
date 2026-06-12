from sqlalchemy import Column, DateTime, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class ResearchRunRecord(Base):
    """Persisted research run so every run_id stays auditable after the response."""

    __tablename__ = "research_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    ticker = Column(String(16), nullable=False, index=True)
    question = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    validation_status = Column(String(32), nullable=False)
    duration_ms = Column(Float, nullable=True)
    payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
