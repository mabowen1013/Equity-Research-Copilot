from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db_session, get_sessionmaker
from app.models import Company
from app.schemas import CompanyRead, CompanySearchResult, JobRead
from app.services import CompanyLookupError, SecIngestionService, normalize_ticker

router = APIRouter(prefix="/companies", tags=["companies"])


def run_sec_ingestion_job(job_id: int) -> None:
    session = get_sessionmaker()()
    try:
        SecIngestionService(session).run_job(job_id)
    finally:
        session.close()


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


@router.post(
    "/{ticker}/ingest",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_company(
    ticker: str,
    background_tasks: BackgroundTasks,
    refresh: bool = False,
    db: Session = Depends(get_db_session),
):
    try:
        job = SecIngestionService(db).create_job(ticker, refresh=refresh)
    except CompanyLookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_sec_ingestion_job, job.id)
    return job


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
