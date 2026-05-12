from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import Company
from app.schemas import CompanyRead, CompanySearchResult
from app.services import CompanyLookupError, normalize_ticker

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/search", response_model=list[CompanySearchResult])
def search_companies(
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db_session),
) -> list[Company]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query must not be empty")

    pattern = f"%{query}%"
    statement = (
        select(Company)
        .where(
            or_(
                Company.ticker.ilike(pattern),
                Company.name.ilike(pattern),
            )
        )
        .order_by(Company.ticker)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get("/{ticker}", response_model=CompanyRead)
def get_company(
    ticker: str,
    db: Session = Depends(get_db_session),
) -> Company:
    try:
        normalized_ticker = normalize_ticker(ticker)
    except CompanyLookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    statement = select(Company).where(Company.ticker == normalized_ticker)
    company = db.scalar(statement)

    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    return company
