from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    section_id = Column(ForeignKey("filing_sections.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False, index=True)
    chunk_text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False, server_default="0")
    accession_number = Column(String(32), nullable=False, index=True)
    form_type = Column(String(16), nullable=False, index=True)
    filing_date = Column(Date, nullable=False, index=True)
    section_label = Column(Text, nullable=False)
    sec_url = Column(Text, nullable=False)
    start_page = Column(Integer, nullable=True)
    end_page = Column(Integer, nullable=True)
    start_display_page = Column(Integer, nullable=True)
    end_display_page = Column(Integer, nullable=True)
    element_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    xbrl_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_start_offset = Column(Integer, nullable=True)
    source_end_offset = Column(Integer, nullable=True)
    has_table = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
