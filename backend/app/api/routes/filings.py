from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db_session, get_sessionmaker
from app.models import DocumentChunk, Filing, FilingSection
from app.schemas import DocumentChunkRead, FilingSectionRead, JobRead
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


@router.get("/{filing_id}/sections", response_model=list[FilingSectionRead])
def list_filing_sections(
    filing_id: int,
    db: Session = Depends(get_db_session),
) -> list[FilingSection]:
    get_filing_or_404(filing_id, db)
    statement = (
        select(FilingSection)
        .where(FilingSection.filing_id == filing_id)
        .order_by(FilingSection.section_order)
    )

    return list(db.scalars(statement).all())


@router.get("/{filing_id}/chunks", response_model=list[DocumentChunkRead])
def list_filing_chunks(
    filing_id: int,
    section_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db_session),
) -> list[DocumentChunk]:
    get_filing_or_404(filing_id, db)
    statement = (
        select(DocumentChunk)
        .join(FilingSection, DocumentChunk.section_id == FilingSection.id)
        .where(DocumentChunk.filing_id == filing_id)
        .order_by(FilingSection.section_order, DocumentChunk.chunk_index)
        .limit(limit)
    )

    if section_id is not None:
        section = db.get(FilingSection, section_id)
        if section is None or section.filing_id != filing_id:
            raise HTTPException(status_code=404, detail="Filing section not found")
        statement = statement.where(DocumentChunk.section_id == section_id)

    return list(db.scalars(statement).all())


def get_filing_or_404(filing_id: int, db: Session) -> Filing:
    filing = db.get(Filing, filing_id)
    if filing is None:
        raise HTTPException(status_code=404, detail="Filing not found")

    return filing
