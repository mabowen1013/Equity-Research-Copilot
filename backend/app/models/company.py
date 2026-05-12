from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, text

from app.db.base import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_companies_ticker"),
        UniqueConstraint("cik", name="uq_companies_cik"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(16), nullable=False, index=True)
    cik = Column(String(10), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    exchange = Column(String(64), nullable=True)
    sic = Column(String(16), nullable=True)
    sic_description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
