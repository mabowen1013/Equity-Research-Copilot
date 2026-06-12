from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.models import ResearchRunRecord
from app.schemas import RetrievalRequest, RetrievalResponse
from app.schemas.research_run import ResearchRunRead
from app.services.answer_generation import AnswerGenerator, ResearchAnswerService
from app.services.research_trace import (
    build_research_run_diagnostics,
    build_research_run_evidence,
    build_research_run_steps,
)
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)


class ResearchRunService:
    def __init__(
        self,
        db: Session | None,
        *,
        settings: Settings | None = None,
        retriever=None,
        answer_generator: AnswerGenerator | None = None,
        validator=None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._retriever = retriever or RetrievalService(db, settings=self._settings)
        self._answer_service = ResearchAnswerService(
            db,
            settings=self._settings,
            retriever=self._retriever,
            answer_generator=answer_generator,
            validator=validator,
        )

    def run(self, request: RetrievalRequest) -> ResearchRunRead:
        run_started = perf_counter()
        started_at = datetime.now(UTC)
        run_id = f"run_{uuid4().hex}"

        retrieval_response = RetrievalResponse.model_validate(
            self._retriever.retrieve(request)
        )
        answer_response = self._answer_service.answer_from_retrieval_response(
            request,
            retrieval_response,
        )
        finished_at = datetime.now(UTC)
        duration_ms = (perf_counter() - run_started) * 1000

        run = ResearchRunRead(
            run_id=run_id,
            status=_run_status(answer_response.validation_status),
            ticker=request.ticker.strip().upper(),
            question=request.question,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=duration_ms,
            answer=answer_response.answer,
            citations=answer_response.citations,
            validation_status=answer_response.validation_status,
            validation=answer_response.validation,
            limitations=answer_response.limitations,
            plan=answer_response.retrieval_plan.model_dump(mode="json"),
            steps=build_research_run_steps(retrieval_response, answer_response),
            evidence=build_research_run_evidence(retrieval_response, answer_response),
            diagnostics=build_research_run_diagnostics(retrieval_response),
        )
        self._persist_run(run)
        return run

    def get_run(self, run_id: str) -> ResearchRunRead | None:
        if self._db is None:
            return None
        record = self._db.scalar(
            select(ResearchRunRecord).where(ResearchRunRecord.run_id == run_id)
        )
        if record is None:
            return None
        return ResearchRunRead.model_validate(record.payload)

    def list_runs(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[ResearchRunRecord]:
        if self._db is None:
            return []
        statement = select(ResearchRunRecord).order_by(ResearchRunRecord.id.desc())
        if ticker is not None and ticker.strip():
            statement = statement.where(
                ResearchRunRecord.ticker == ticker.strip().upper()
            )
        statement = statement.limit(limit)
        return list(self._db.scalars(statement).all())

    def _persist_run(self, run: ResearchRunRead) -> None:
        if self._db is None:
            return
        record = ResearchRunRecord(
            run_id=run.run_id,
            ticker=run.ticker,
            question=run.question,
            status=run.status,
            validation_status=run.validation_status,
            duration_ms=run.duration_ms,
            payload=run.model_dump(mode="json"),
        )
        try:
            self._db.add(record)
            self._db.commit()
        except Exception:
            # An audit-write failure must not turn a completed run into a 500;
            # the run is still returned to the caller, only retrieval-by-id is lost.
            logger.exception("Failed to persist research run %s", run.run_id)
            self._db.rollback()


def _run_status(validation_status: str) -> str:
    if validation_status == "insufficient_evidence":
        return "insufficient_evidence"
    if validation_status == "failed":
        return "failed"
    return "completed"
