from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyRead(BaseModel):
    id: int
    ticker: str
    cik: str
    name: str
    exchange: str | None
    sic: str | None
    sic_description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompanySearchResult(BaseModel):
    id: int
    ticker: str
    cik: str
    name: str
    exchange: str | None

    model_config = ConfigDict(from_attributes=True)
