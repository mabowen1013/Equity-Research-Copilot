'''
为某个 company 的所有 document chunks 生成 embedding，
并把结果写入 chunk_embeddings 表；
同时用 Job 表记录这个 embedding 任务的状态。
'''

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.models import ChunkEmbedding, Company, DocumentChunk, Filing, Job
from app.services.company_lookup import CompanyLookupError, normalize_ticker
from app.services.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    build_embedding_provider,
)

CHUNK_EMBEDDING_JOB_TYPE = "chunk_embedding"
DEFAULT_EMBEDDING_BATCH_SIZE = 96


def utc_now() -> datetime:
    return datetime.now(UTC)


class ChunkEmbeddingError(ValueError):
    """Base error for chunk embedding generation."""


class ChunkEmbeddingCompanyNotFoundError(ChunkEmbeddingError):
    """Raised when embeddings are requested for an unknown company."""


class ChunkEmbeddingJobNotFoundError(ChunkEmbeddingError):
    """Raised when an embedding job cannot be found."""


@dataclass(frozen=True)
class ChunkEmbeddingInput:
    chunk: DocumentChunk
    text: str
    content_sha256: str
    existing_embedding: ChunkEmbedding | None


@dataclass(frozen=True)
class ChunkEmbeddingRunResult:
    total_chunks: int
    embedded_count: int
    skipped_count: int
    stale_updated_count: int
    failed_count: int


class ChunkEmbeddingService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        provider: EmbeddingProvider | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._provider = provider
        self._clock = clock

    def create_job(self, ticker: str, *, refresh: bool = False) -> Job:
        company = self._get_company_by_ticker(ticker)
        now = self._clock()
        job = Job(
            job_type=CHUNK_EMBEDDING_JOB_TYPE,
            company_id=company.id,
            status="pending",
            progress=0,
            retry_count=0,
            payload={
                "ticker": company.ticker,
                "company_id": company.id,
                "refresh": refresh,
                "provider": self._settings.embedding_provider,
                "model": self._settings.embedding_model,
                "dimensions": self._settings.embedding_dimensions,
                "embedding_input_version": self._settings.embedding_input_version,
                "stage": "queued",
            },
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        self._db.add(job)
        self._db.flush()
        return job

    def run_job(self, job_id: int) -> Job:
        job = self._db.get(Job, job_id)
        if job is None:
            raise ChunkEmbeddingJobNotFoundError(f"Chunk embedding job {job_id} was not found.")

        company_id = int((job.payload or {}).get("company_id", 0))
        refresh = bool((job.payload or {}).get("refresh", False))

        try:
            self._mark_stage(job, stage="loading_chunks", progress=10, started=True)
            company = self._get_company_by_id(company_id)
            result = self.generate_company_embeddings(company, refresh=refresh)
            self._mark_succeeded(job, result)
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(Job, job_id) or job
            self._mark_failed(failed_job, exc)
            return failed_job

        return job

    # 给某个company的所有chunks生成embedding
    def generate_company_embeddings(
        self,
        company: Company,
        *,
        refresh: bool = False,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> ChunkEmbeddingRunResult:
        chunks = self._load_chunks(company.id)
        existing_by_chunk_id = self._load_existing_embeddings([chunk.id for chunk in chunks])

        work_items: list[ChunkEmbeddingInput] = []
        skipped_count = 0
        for chunk in chunks:
            text = build_chunk_embedding_input(company, chunk)
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            existing = existing_by_chunk_id.get(chunk.id)
            if (
                not refresh
                and existing is not None
                and existing.content_sha256 == content_hash
            ):
                skipped_count += 1
                continue
            work_items.append(
                ChunkEmbeddingInput(
                    chunk=chunk,
                    text=text,
                    content_sha256=content_hash,
                    existing_embedding=existing,
                )
            )

        embedded_count = 0
        stale_updated_count = 0
        failed_count = 0
        provider = self._get_provider()
        now = self._clock()

        for start in range(0, len(work_items), batch_size):
            batch = work_items[start : start + batch_size]
            try:
                vectors = provider.embed_texts(
                    [item.text for item in batch],
                    model=self._settings.embedding_model,
                    dimensions=self._settings.embedding_dimensions,
                )
            except EmbeddingProviderError:
                failed_count += len(batch)
                raise

            if len(vectors) != len(batch):
                failed_count += len(batch)
                raise EmbeddingProviderError("Embedding provider returned the wrong number of vectors.")

            for item, vector in zip(batch, vectors, strict=True):
                existing = item.existing_embedding
                if existing is None:
                    self._db.add(
                        ChunkEmbedding(
                            chunk_id=item.chunk.id,
                            company_id=company.id,
                            filing_id=item.chunk.filing_id,
                            provider=self._settings.embedding_provider,
                            model=self._settings.embedding_model,
                            dimensions=self._settings.embedding_dimensions,
                            embedding_input_version=self._settings.embedding_input_version,
                            content_sha256=item.content_sha256,
                            embedding=vector,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    embedded_count += 1
                else:
                    existing.content_sha256 = item.content_sha256
                    existing.embedding = vector
                    existing.updated_at = now
                    stale_updated_count += 1

        self._db.flush()
        return ChunkEmbeddingRunResult(
            total_chunks=len(chunks),
            embedded_count=embedded_count,
            skipped_count=skipped_count,
            stale_updated_count=stale_updated_count,
            failed_count=failed_count,
        )

    def _get_provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = build_embedding_provider(self._settings)
        return self._provider

    def _load_chunks(self, company_id: int) -> list[DocumentChunk]:
        statement = (
            select(DocumentChunk)
            .join(Filing, DocumentChunk.filing_id == Filing.id)
            .where(Filing.company_id == company_id)
            .order_by(DocumentChunk.filing_date.desc(), DocumentChunk.chunk_index, DocumentChunk.id)
        )
        return list(self._db.scalars(statement).all())

    def _load_existing_embeddings(self, chunk_ids: list[int]) -> dict[int, ChunkEmbedding]:
        if not chunk_ids:
            return {}
        statement = select(ChunkEmbedding).where(
            ChunkEmbedding.chunk_id.in_(chunk_ids),
            ChunkEmbedding.provider == self._settings.embedding_provider,
            ChunkEmbedding.model == self._settings.embedding_model,
            ChunkEmbedding.dimensions == self._settings.embedding_dimensions,
            ChunkEmbedding.embedding_input_version == self._settings.embedding_input_version,
        )
        return {embedding.chunk_id: embedding for embedding in self._db.scalars(statement).all()}

    def _get_company_by_ticker(self, ticker: str) -> Company:
        try:
            normalized_ticker = normalize_ticker(ticker)
        except CompanyLookupError as exc:
            raise ChunkEmbeddingError(str(exc)) from exc

        statement = select(Company).where(Company.ticker == normalized_ticker)
        company = self._db.scalar(statement)
        if company is None:
            raise ChunkEmbeddingCompanyNotFoundError(
                f"Company {normalized_ticker} was not found. Ingest it before embedding chunks."
            )
        return company

    def _get_company_by_id(self, company_id: int) -> Company:
        company = self._db.get(Company, company_id)
        if company is None:
            raise ChunkEmbeddingCompanyNotFoundError(f"Company id {company_id} was not found.")
        return company

    def _mark_stage(
        self,
        job: Job,
        *,
        stage: str,
        progress: int,
        started: bool = False,
        **payload_updates: Any,
    ) -> None:
        now = self._clock()
        job.status = "running"
        job.progress = progress
        job.updated_at = now
        if started:
            job.started_at = now
            job.error_message = None
        self._merge_payload(job, stage=stage, **payload_updates)
        self._db.commit()

    def _mark_succeeded(self, job: Job, result: ChunkEmbeddingRunResult) -> None:
        now = self._clock()
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = now
        job.updated_at = now
        self._merge_payload(
            job,
            stage="completed",
            total_chunks=result.total_chunks,
            embedded_count=result.embedded_count,
            skipped_count=result.skipped_count,
            stale_updated_count=result.stale_updated_count,
            failed_count=result.failed_count,
        )
        self._db.commit()

    def _mark_failed(self, job: Job, exc: Exception) -> None:
        now = self._clock()
        job.status = "failed"
        job.finished_at = now
        job.updated_at = now
        job.error_message = str(exc)
        self._merge_payload(job, stage="failed", error_type=type(exc).__name__)
        self._db.commit()

    def _merge_payload(self, job: Job, **updates: Any) -> None:
        job.payload = {**(job.payload or {}), **updates}


def build_chunk_embedding_input(company: Company, chunk: DocumentChunk) -> str:
    return "\n".join(
        [
            f"Company: {company.ticker} - {company.name}",
            f"Form: {chunk.form_type}",
            f"Filed: {chunk.filing_date.isoformat()}",
            f"Section: {chunk.section_label}",
            "",
            chunk.chunk_text,
        ]
    )
