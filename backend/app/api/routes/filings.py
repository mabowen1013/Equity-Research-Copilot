from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db_session, get_sessionmaker
from app.schemas import JobRead
from app.services import (
    FilingProcessingFilingNotFoundError,
    FilingProcessingService,
)

router = APIRouter(prefix="/filings", tags=["filings"])


def run_filing_processing_job(job_id: int) -> None:
    session = get_sessionmaker()()
    try:
        FilingProcessingService(session).run_job(job_id)
    finally:
        session.close()


@router.post(
    "/{filing_id}/process",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def process_filing(
    filing_id: int,
    background_tasks: BackgroundTasks,
    refresh: bool = False,
    db: Session = Depends(get_db_session),
):
    try:
        job = FilingProcessingService(db).create_job(filing_id, refresh=refresh)
    except FilingProcessingFilingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Filing not found") from exc

    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_filing_processing_job, job.id)
    return job
