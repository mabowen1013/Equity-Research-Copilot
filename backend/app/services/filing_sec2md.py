from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import sec2md
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, Filing, FilingDocument, FilingSection, Job
from app.services.sec_client import SecClient

FILING_PARSE_JOB_TYPE = "filing_parse"
SEC2MD_PARSER_NAME = "sec2md"
try:
    SEC2MD_PARSER_VERSION = version("sec2md")
except PackageNotFoundError:
    SEC2MD_PARSER_VERSION = getattr(sec2md, "__version__", "unknown")
SEC2MD_SUPPORTED_SECTION_FORMS = frozenset({"10-K", "10-Q", "8-K"})
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 128
DEFAULT_MAX_TABLE_TOKENS = 2048


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingSec2MdError(ValueError):
    """Base error for filing document parsing failures."""


class FilingNotFoundError(FilingSec2MdError):
    """Raised when a filing id cannot be found."""


class FilingParseJobNotFoundError(FilingSec2MdError):
    """Raised when a filing parse job cannot be found."""


class UnsupportedFilingDocumentError(FilingSec2MdError):
    """Raised when a filing document cannot be parsed as HTML."""


@dataclass(frozen=True)
class ParsedSection:
    section_key: str
    part: str | None
    item: str | None
    title: str | None
    pages: list[Any]
    markdown_text: str
    token_count: int
    start_page: int | None
    end_page: int | None
    start_display_page: int | None
    end_display_page: int | None


@dataclass(frozen=True)
class FilingParseResult:
    document: FilingDocument
    sections: list[FilingSection]
    chunks: list[DocumentChunk]


class FilingSec2MdService:
    def __init__(
        self,
        db: Session,
        *,
        sec_client: SecClient | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._sec_client = sec_client or SecClient()
        self._clock = clock

    def create_job(self, filing_id: int, *, refresh: bool = False) -> Job:
        filing = self._get_filing(filing_id)
        now = self._clock()
        job = Job(
            job_type=FILING_PARSE_JOB_TYPE,
            company_id=filing.company_id,
            status="pending",
            progress=0,
            retry_count=0,
            payload={
                "filing_id": filing.id,
                "accession_number": filing.accession_number,
                "form_type": filing.form_type,
                "refresh": refresh,
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
            raise FilingParseJobNotFoundError(f"Filing parse job {job_id} was not found.")

        filing_id = int((job.payload or {}).get("filing_id", 0))
        refresh = bool((job.payload or {}).get("refresh", False))

        try:
            self._mark_stage(job, stage="downloading_document", progress=10, started=True)
            filing = self._get_filing(filing_id)
            document = self.get_or_fetch_document(filing, refresh=refresh)
            self._mark_stage(
                job,
                stage="parsing_sec2md",
                progress=45,
                document_id=document.id,
            )

            result = self.parse_and_store_document(filing, document)
            self._mark_succeeded(
                job,
                document_id=result.document.id,
                sections_count=len(result.sections),
                chunks_count=len(result.chunks),
                section_keys=[section.section_key for section in result.sections],
            )
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(Job, job_id) or job
            self._mark_failed(failed_job, exc)
            return failed_job

        return job

    def parse_filing(self, filing_id: int, *, refresh: bool = False) -> FilingParseResult:
        filing = self._get_filing(filing_id)
        document = self.get_or_fetch_document(filing, refresh=refresh)
        return self.parse_and_store_document(filing, document)

    def get_or_fetch_document(
        self,
        filing: Filing,
        *,
        refresh: bool = False,
    ) -> FilingDocument:
        if not filing.sec_primary_document_url:
            raise UnsupportedFilingDocumentError(
                f"Filing {filing.id} does not have a primary document URL."
            )

        existing = self._get_document(filing.id)
        if existing is not None and not refresh:
            return existing

        raw_html = self._sec_client.get_text(filing.sec_primary_document_url)
        self._validate_html(raw_html, filing.id)

        now = self._clock()
        document = existing
        if document is None:
            document = FilingDocument(filing_id=filing.id)
            self._db.add(document)

        document.raw_html = raw_html
        document.annotated_html = None
        document.source_url = filing.sec_primary_document_url
        document.content_sha256 = sha256(raw_html.encode("utf-8")).hexdigest()
        document.parser_name = SEC2MD_PARSER_NAME
        document.parser_version = SEC2MD_PARSER_VERSION
        document.fetched_at = now
        document.parsed_at = None
        document.updated_at = now
        self._db.flush()
        return document

    def parse_and_store_document(
        self,
        filing: Filing,
        document: FilingDocument,
    ) -> FilingParseResult:
        self._validate_html(document.raw_html, filing.id)

        parser = sec2md.Parser(document.raw_html)
        pages = parser.get_pages(include_elements=True)
        if not pages:
            raise UnsupportedFilingDocumentError(f"Filing {filing.id} did not produce parseable pages.")

        parsed_sections = self._extract_sections(pages, filing.form_type)

        self._db.execute(delete(DocumentChunk).where(DocumentChunk.filing_id == filing.id))
        self._db.execute(delete(FilingSection).where(FilingSection.filing_id == filing.id))

        now = self._clock()
        document.annotated_html = parser.html()
        document.parser_name = SEC2MD_PARSER_NAME
        document.parser_version = SEC2MD_PARSER_VERSION
        document.parsed_at = now
        document.updated_at = now

        stored_sections: list[FilingSection] = []
        stored_chunks: list[DocumentChunk] = []
        chunk_index = 0

        for section_order, parsed_section in enumerate(parsed_sections):
            filing_section = FilingSection(
                filing_id=filing.id,
                section_key=parsed_section.section_key,
                part=parsed_section.part,
                item=parsed_section.item,
                title=parsed_section.title,
                section_order=section_order,
                start_page=parsed_section.start_page,
                end_page=parsed_section.end_page,
                start_display_page=parsed_section.start_display_page,
                end_display_page=parsed_section.end_display_page,
                markdown_text=parsed_section.markdown_text,
                token_count=parsed_section.token_count,
                created_at=now,
                updated_at=now,
            )
            self._db.add(filing_section)
            self._db.flush()
            stored_sections.append(filing_section)

            chunks = sec2md.chunk_pages(
                parsed_section.pages,
                chunk_size=DEFAULT_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                max_table_tokens=DEFAULT_MAX_TABLE_TOKENS,
                header=self._build_chunk_header(filing, parsed_section),
            )

            for chunk in chunks:
                document_chunk = self._build_document_chunk(
                    filing=filing,
                    filing_section=filing_section,
                    parsed_section=parsed_section,
                    chunk=chunk,
                    chunk_index=chunk_index,
                    now=now,
                )
                self._db.add(document_chunk)
                stored_chunks.append(document_chunk)
                chunk_index += 1

        self._db.flush()
        return FilingParseResult(
            document=document,
            sections=stored_sections,
            chunks=stored_chunks,
        )

    def _get_filing(self, filing_id: int) -> Filing:
        filing = self._db.get(Filing, filing_id)
        if filing is None:
            raise FilingNotFoundError(f"Filing {filing_id} was not found.")
        return filing

    def _get_document(self, filing_id: int) -> FilingDocument | None:
        statement = select(FilingDocument).where(FilingDocument.filing_id == filing_id)
        return self._db.scalar(statement)

    def _extract_sections(self, pages: list[Any], form_type: str) -> list[ParsedSection]:
        sections = []
        if form_type in SEC2MD_SUPPORTED_SECTION_FORMS:
            sections = sec2md.extract_sections(pages, filing_type=form_type)

        if not sections:
            return [
                ParsedSection(
                    section_key="FULL_DOCUMENT",
                    part=None,
                    item=None,
                    title="Full Document",
                    pages=pages,
                    markdown_text="\n\n".join(page.content for page in pages),
                    token_count=sum(page.tokens for page in pages),
                    start_page=pages[0].number,
                    end_page=pages[-1].number,
                    start_display_page=pages[0].display_page,
                    end_display_page=pages[-1].display_page,
                )
            ]

        seen_keys: set[str] = set()
        parsed_sections: list[ParsedSection] = []
        for section in sections:
            base_key = self._build_section_key(section.part, section.item, len(parsed_sections))
            section_key = base_key
            if section_key in seen_keys:
                section_key = f"{base_key}:{len(parsed_sections)}"
            seen_keys.add(section_key)

            parsed_sections.append(
                ParsedSection(
                    section_key=section_key,
                    part=section.part,
                    item=section.item,
                    title=section.item_title,
                    pages=section.pages,
                    markdown_text=section.markdown(),
                    token_count=section.tokens,
                    start_page=section.page_range[0],
                    end_page=section.page_range[1],
                    start_display_page=section.pages[0].display_page if section.pages else None,
                    end_display_page=section.pages[-1].display_page if section.pages else None,
                )
            )
        return parsed_sections

    def _build_document_chunk(
        self,
        *,
        filing: Filing,
        filing_section: FilingSection,
        parsed_section: ParsedSection,
        chunk: Any,
        chunk_index: int,
        now: datetime,
    ) -> DocumentChunk:
        source_offsets = [
            (element.content_start_offset, element.content_end_offset)
            for element in chunk.elements
            if element.content_start_offset is not None and element.content_end_offset is not None
        ]
        start_offset = min((start for start, _ in source_offsets), default=None)
        end_offset = max((end for _, end in source_offsets), default=None)

        return DocumentChunk(
            filing_id=filing.id,
            section_id=filing_section.id,
            chunk_index=chunk_index,
            chunk_text=chunk.content,
            token_count=chunk.num_tokens,
            accession_number=filing.accession_number,
            form_type=filing.form_type,
            filing_date=filing.filing_date,
            section_label=self._build_section_label(parsed_section),
            sec_url=filing.sec_primary_document_url or filing.sec_filing_url,
            start_page=chunk.start_page,
            end_page=chunk.end_page,
            start_display_page=chunk.start_display_page,
            end_display_page=chunk.end_display_page,
            element_ids=chunk.element_ids,
            xbrl_tags=sorted(chunk.tags),
            source_start_offset=start_offset,
            source_end_offset=end_offset,
            has_table=chunk.has_table,
            created_at=now,
            updated_at=now,
        )

    def _build_chunk_header(self, filing: Filing, parsed_section: ParsedSection) -> str:
        return (
            f"Form {filing.form_type} | Filed {filing.filing_date.isoformat()} | "
            f"Accession {filing.accession_number}\n"
            f"Section: {self._build_section_label(parsed_section)}"
        )

    def _build_section_label(self, parsed_section: ParsedSection) -> str:
        parts = [
            value
            for value in [parsed_section.part, parsed_section.item, parsed_section.title]
            if value
        ]
        return " - ".join(parts) if parts else parsed_section.section_key

    def _build_section_key(self, part: str | None, item: str | None, order: int) -> str:
        if part and item:
            return f"{part}|{item}"
        if item:
            return item
        if part:
            return part
        return f"SECTION_{order}"

    def _validate_html(self, raw_html: str, filing_id: int) -> None:
        if not raw_html or not raw_html.strip():
            raise UnsupportedFilingDocumentError(f"Filing {filing_id} returned an empty document.")
        if raw_html.lstrip().startswith("%PDF"):
            raise UnsupportedFilingDocumentError(
                f"Filing {filing_id} primary document is a PDF; only HTML is supported."
            )

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

    def _mark_succeeded(
        self,
        job: Job,
        *,
        document_id: int,
        sections_count: int,
        chunks_count: int,
        section_keys: list[str],
    ) -> None:
        now = self._clock()
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = now
        job.updated_at = now
        self._merge_payload(
            job,
            stage="completed",
            document_id=document_id,
            sections_count=sections_count,
            chunks_count=chunks_count,
            section_keys=section_keys,
        )
        self._db.commit()

    def _mark_failed(self, job: Job, exc: Exception) -> None:
        now = self._clock()
        job.status = "failed"
        job.finished_at = now
        job.updated_at = now
        job.error_message = str(exc)
        self._merge_payload(
            job,
            stage="failed",
            error_type=type(exc).__name__,
        )
        self._db.commit()

    def _merge_payload(self, job: Job, **updates: Any) -> None:
        job.payload = {
            **(job.payload or {}),
            **updates,
        }
