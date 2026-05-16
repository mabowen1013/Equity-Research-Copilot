from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, text

from app.db.base import Base


class FilingSection(Base):
    __tablename__ = "filing_sections"

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    section_key = Column(String(128), nullable=False, index=True)
    part = Column(String(64), nullable=True)
    item = Column(String(64), nullable=True)
    title = Column(Text, nullable=True)
    section_order = Column(Integer, nullable=False, index=True)
    start_page = Column(Integer, nullable=True)
    end_page = Column(Integer, nullable=True)
    start_display_page = Column(Integer, nullable=True)
    end_display_page = Column(Integer, nullable=True)
    markdown_text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
