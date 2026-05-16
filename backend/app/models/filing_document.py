from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text

from app.db.base import Base


class FilingDocument(Base):
    __tablename__ = "filing_documents"
    __table_args__ = (
        UniqueConstraint("filing_id", name="uq_filing_documents_filing_id"),
    )

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    raw_html = Column(Text, nullable=False)
    annotated_html = Column(Text, nullable=True)
    source_url = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    parser_name = Column(String(64), nullable=False, server_default="sec2md")
    parser_version = Column(String(32), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    parsed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
