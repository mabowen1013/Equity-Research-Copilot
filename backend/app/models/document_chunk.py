from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.db.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("section_id", "chunk_index", name="uq_document_chunks_section_index"),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        CheckConstraint(
            "token_count > 0",
            name="ck_document_chunks_token_count_positive",
        ),
        CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_document_chunks_offsets",
        ),
    )

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    section_id = Column(
        ForeignKey("filing_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False)
    start_offset = Column(Integer, nullable=False)
    end_offset = Column(Integer, nullable=False)
    text_hash = Column(String(64), nullable=False)
    accession_number = Column(String(32), nullable=False, index=True)
    form_type = Column(String(16), nullable=False, index=True)
    filing_date = Column(Date, nullable=False, index=True)
    section_key = Column(String(64), nullable=False, index=True)
    sec_url = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
