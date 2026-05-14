from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.db.base import Base


class FilingDocument(Base):
    __tablename__ = "filing_documents"
    __table_args__ = (
        UniqueConstraint("filing_id", name="uq_filing_documents_filing_id"),
        CheckConstraint(
            "status IN ('pending', 'downloaded', 'failed')",
            name="ck_filing_documents_status",
        ),
        CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_filing_documents_byte_size_nonnegative",
        ),
    )

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    source_url = Column(Text, nullable=False)
    cache_path = Column(Text, nullable=True)
    content_sha256 = Column(String(64), nullable=True)
    content_type = Column(String(128), nullable=True)
    byte_size = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, server_default="pending", index=True)
    parser_version = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    downloaded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
