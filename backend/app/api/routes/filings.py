from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sec2md.visualize import highlight_html
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db_session, get_sessionmaker
from app.models import DocumentChunk, Filing, FilingDocument, FilingSection, Job
from app.schemas import DocumentChunkRead, FilingSectionRead, FilingSectionSummary, JobRead
from app.services import FilingNotFoundError, FilingSec2MdService

router = APIRouter(prefix="/filings", tags=["filings"])


def run_filing_parse_job(job_id: int) -> None:
    session = get_sessionmaker()()
    try:
        FilingSec2MdService(session).run_job(job_id)
    finally:
        session.close()


@router.post(
    "/{filing_id}/parse",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def parse_filing(
    filing_id: int,
    background_tasks: BackgroundTasks,
    refresh: bool = False,
    db: Session = Depends(get_db_session),
) -> Job:
    try:
        job = FilingSec2MdService(db).create_job(filing_id, refresh=refresh)
    except FilingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_filing_parse_job, job.id)
    return job


@router.get("/{filing_id}/sections", response_model=list[FilingSectionSummary])
def list_filing_sections(
    filing_id: int,
    db: Session = Depends(get_db_session),
) -> list[FilingSection]:
    get_filing_or_404(filing_id, db)
    statement = (
        select(FilingSection)
        .where(FilingSection.filing_id == filing_id)
        .order_by(FilingSection.section_order, FilingSection.id)
    )
    return list(db.scalars(statement).all())


@router.get("/{filing_id}/sections/{section_id}", response_model=FilingSectionRead)
def get_filing_section(
    filing_id: int,
    section_id: int,
    db: Session = Depends(get_db_session),
) -> FilingSection:
    get_filing_or_404(filing_id, db)
    statement = select(FilingSection).where(
        FilingSection.id == section_id,
        FilingSection.filing_id == filing_id,
    )
    section = db.scalar(statement)
    if section is None:
        raise HTTPException(status_code=404, detail="Filing section not found")
    return section


@router.get("/{filing_id}/chunks", response_model=list[DocumentChunkRead])
def list_filing_chunks(
    filing_id: int,
    section_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db_session),
) -> list[DocumentChunk]:
    get_filing_or_404(filing_id, db)
    statement = (
        select(DocumentChunk)
        .where(DocumentChunk.filing_id == filing_id)
        .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
        .limit(limit)
    )
    if section_id is not None:
        statement = statement.where(DocumentChunk.section_id == section_id)
    return list(db.scalars(statement).all())


@router.get("/{filing_id}/chunks/{chunk_id}/source", response_class=HTMLResponse)
def get_chunk_highlighted_source(
    filing_id: int,
    chunk_id: int,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    get_filing_or_404(filing_id, db)
    statement = select(DocumentChunk).where(
        DocumentChunk.id == chunk_id,
        DocumentChunk.filing_id == filing_id,
    )
    chunk = db.scalar(statement)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Document chunk not found")

    document_statement = select(FilingDocument).where(FilingDocument.filing_id == filing_id)
    document = db.scalar(document_statement)
    if document is None or not document.annotated_html:
        raise HTTPException(status_code=404, detail="Annotated filing document not found")

    highlighted = highlight_html(document.annotated_html, chunk.element_ids)
    return HTMLResponse(content=highlighted)


def get_filing_or_404(filing_id: int, db: Session) -> Filing:
    filing = db.get(Filing, filing_id)
    if filing is None:
        raise HTTPException(status_code=404, detail="Filing not found")
    return filing
