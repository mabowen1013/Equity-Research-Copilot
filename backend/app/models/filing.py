from sqlalchemy import (
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


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_filings_accession_number"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id"), nullable=False, index=True)
    accession_number = Column(String(32), nullable=False, index=True)
    form_type = Column(String(16), nullable=False, index=True)
    filing_date = Column(Date, nullable=False, index=True)
    report_date = Column(Date, nullable=True)
    primary_document = Column(String(255), nullable=True)
    sec_filing_url = Column(Text, nullable=False)
    sec_primary_document_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
