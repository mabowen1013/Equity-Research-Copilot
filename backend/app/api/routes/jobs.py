from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import Job
from app.schemas import JobRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def list_jobs(
    status: str | None = None,
    company_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db_session),
) -> list[Job]:
    statement = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)

    if status is not None:
        statement = statement.where(Job.status == status)

    if company_id is not None:
        statement = statement.where(Job.company_id == company_id)

    return list(db.scalars(statement).all())


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db_session)) -> Job:
    job = db.get(Job, job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job
