from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.core.config import BACKEND_ROOT
from app.models import Filing, FilingDocument
from app.services.sec_client import SecClient, SecContentResponse

_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingDocumentDownloadError(RuntimeError):
    """Raised when a raw filing document cannot be downloaded or cached."""


@dataclass(frozen=True)
class FilingDocumentDownloadResult:
    document: FilingDocument
    cache_hit: bool


class FilingDocumentService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        sec_client: SecClient | None = None,
        cache_dir: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        active_settings = settings or get_settings()
        self._db = db
        self._sec_client = sec_client or SecClient(settings=active_settings)
        self._cache_dir = self._resolve_cache_dir(
            cache_dir or active_settings.sec_filing_cache_dir,
        )
        self._clock = clock

    def get_or_download_primary_document(
        self,
        filing: Filing,
        *,
        refresh: bool = False,
    ) -> FilingDocumentDownloadResult:
        record = self._get_record(filing)
        if not refresh and self._is_cache_hit(record):
            return FilingDocumentDownloadResult(document=record, cache_hit=True)

        source_url = filing.sec_primary_document_url
        if source_url is None or not source_url.strip():
            fallback_url = filing.sec_filing_url or ""
            record = self._get_or_create_record(filing, fallback_url)
            message = "Filing does not have a SEC primary document URL."
            self._mark_failed(record, message)
            raise FilingDocumentDownloadError(message)

        record = self._get_or_create_record(filing, source_url)
        self._mark_pending(record, source_url)

        try:
            response = self._sec_client.get_content(source_url)
            cache_path = self._build_cache_path(filing, source_url)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(response.content)
        except Exception as exc:
            message = f"Failed to download filing document: {exc}"
            self._mark_failed(record, message)
            raise FilingDocumentDownloadError(message) from exc

        self._mark_downloaded(record, response, cache_path)
        return FilingDocumentDownloadResult(document=record, cache_hit=False)

    def _get_record(self, filing: Filing) -> FilingDocument | None:
        statement = select(FilingDocument).where(FilingDocument.filing_id == filing.id)
        return self._db.scalar(statement)

    def _get_or_create_record(self, filing: Filing, source_url: str) -> FilingDocument:
        record = self._get_record(filing)
        if record is None:
            record = FilingDocument(
                filing_id=filing.id,
                source_url=source_url,
            )
            self._db.add(record)

        return record

    def _mark_pending(self, record: FilingDocument, source_url: str) -> None:
        now = self._clock()
        record.source_url = source_url
        record.status = "pending"
        record.error_message = None
        record.updated_at = now
        self._db.flush()

    def _mark_failed(self, record: FilingDocument, message: str) -> None:
        now = self._clock()
        record.status = "failed"
        record.error_message = message
        record.updated_at = now
        self._db.flush()

    def _mark_downloaded(
        self,
        record: FilingDocument,
        response: SecContentResponse,
        cache_path: Path,
    ) -> None:
        now = self._clock()
        record.source_url = response.url
        record.cache_path = str(cache_path)
        record.content_sha256 = sha256(response.content).hexdigest()
        record.content_type = response.content_type
        record.byte_size = len(response.content)
        record.status = "downloaded"
        record.error_message = None
        record.downloaded_at = now
        record.updated_at = now
        self._db.flush()

    def _is_cache_hit(self, record: FilingDocument | None) -> bool:
        if record is None or record.status != "downloaded" or not record.cache_path:
            return False

        return Path(record.cache_path).is_file()

    def _build_cache_path(self, filing: Filing, source_url: str) -> Path:
        accession = _safe_path_part(filing.accession_number, fallback=f"filing-{filing.id}")
        filename = _safe_path_part(
            filing.primary_document or Path(urlparse(source_url).path).name,
            fallback="primary-document.html",
        )
        return self._cache_dir / accession / filename

    def _resolve_cache_dir(self, cache_dir: Path) -> Path:
        if cache_dir.is_absolute():
            return cache_dir

        return BACKEND_ROOT / cache_dir


def _safe_path_part(value: str | None, *, fallback: str) -> str:
    if value is None:
        return fallback

    cleaned = _UNSAFE_PATH_CHARS.sub("_", value).strip("._")
    return cleaned or fallback
