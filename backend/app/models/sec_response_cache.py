from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class SecResponseCache(Base):
    __tablename__ = "sec_response_cache"
    __table_args__ = (
        CheckConstraint(
            "status_code >= 100 AND status_code <= 599",
            name="ck_sec_response_cache_status_code_range",
        ),
    )

    id = Column(Integer, primary_key=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    response_json = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    status_code = Column(Integer, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
