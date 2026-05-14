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


class FilingSection(Base):
    __tablename__ = "filing_sections"
    __table_args__ = (
        UniqueConstraint("filing_id", "section_key", name="uq_filing_sections_key"),
        UniqueConstraint("filing_id", "section_order", name="uq_filing_sections_order"),
        CheckConstraint(
            "section_order >= 0",
            name="ck_filing_sections_order_nonnegative",
        ),
        CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_filing_sections_offsets",
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 100",
            name="ck_filing_sections_confidence_range",
        ),
    )

    id = Column(Integer, primary_key=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    section_key = Column(String(64), nullable=False, index=True)
    section_title = Column(Text, nullable=False)
    section_order = Column(Integer, nullable=False)
    normalized_text = Column(Text, nullable=False)
    start_offset = Column(Integer, nullable=False)
    end_offset = Column(Integer, nullable=False)
    extraction_confidence = Column(Integer, nullable=False)
    extraction_method = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
