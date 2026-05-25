from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)

from app.db.base import Base


class FinancialFact(Base):
    __tablename__ = "financial_facts"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id"), nullable=False, index=True)
    canonical_metric_key = Column(String(64), nullable=False, index=True)
    taxonomy_tag = Column(String(128), nullable=False, index=True)
    label = Column(Text, nullable=False)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=False, index=True)
    source_fiscal_year = Column(Integer, nullable=True, index=True)
    fact_fiscal_year = Column(Integer, nullable=True, index=True)
    fiscal_period = Column(String(16), nullable=True, index=True)
    form_type = Column(String(16), nullable=True, index=True)
    filed_date = Column(Date, nullable=True)
    unit = Column(String(32), nullable=False)
    value = Column(Numeric(28, 6), nullable=False)
    source_accession_number = Column(String(32), nullable=True, index=True)
    source_filing_id = Column(ForeignKey("filings.id"), nullable=True, index=True)
    source_filing_url = Column(Text, nullable=True)
    source_fact_id = Column(String(255), nullable=False, index=True)
    is_computed = Column(Boolean, nullable=False, server_default=text("false"))
    calculation_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
