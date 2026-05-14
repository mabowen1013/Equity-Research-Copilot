from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from inscriptis import get_text
from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Filing, FilingDocument, FilingSection

FULL_DOCUMENT_SECTION_KEY = "full_document"
FULL_DOCUMENT_SECTION_TITLE = "Full document"
FILING_TEXT_PARSER_VERSION = "full_document_v1"
FULL_DOCUMENT_EXTRACTION_METHOD = "full_document_fallback"
FULL_DOCUMENT_EXTRACTION_CONFIDENCE = 50

_EXCESSIVE_BLANK_LINES = re.compile(r"\n{3,}")


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingTextExtractionError(RuntimeError):
    """Raised when cached filing HTML cannot be converted into filing text."""


class FilingTextExtractionService:
    def __init__(
        self,
        db: Session,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._clock = clock

    def extract_full_document_section(
        self,
        filing: Filing,
        document: FilingDocument,
    ) -> FilingSection:
        html = self._read_cached_html(document)
        normalized_text = extract_normalized_text(html)
        if not normalized_text:
            raise FilingTextExtractionError("Cached filing document did not contain extractable text.")

        section = self._get_or_create_full_document_section(filing)
        now = self._clock()
        section.section_title = FULL_DOCUMENT_SECTION_TITLE
        section.section_order = 0
        section.normalized_text = normalized_text
        section.start_offset = 0
        section.end_offset = len(normalized_text)
        section.extraction_confidence = FULL_DOCUMENT_EXTRACTION_CONFIDENCE
        section.extraction_method = FULL_DOCUMENT_EXTRACTION_METHOD
        section.updated_at = now
        document.parser_version = FILING_TEXT_PARSER_VERSION
        document.updated_at = now
        self._db.flush()
        return section

    def _read_cached_html(self, document: FilingDocument) -> str:
        if document.status != "downloaded":
            raise FilingTextExtractionError("Filing document must be downloaded before text extraction.")

        if not document.cache_path:
            raise FilingTextExtractionError("Downloaded filing document does not have a cache path.")

        cache_path = Path(document.cache_path)
        if not cache_path.is_file():
            raise FilingTextExtractionError(f"Cached filing document was not found: {cache_path}")

        return cache_path.read_bytes().decode("utf-8", errors="replace")

    def _get_or_create_full_document_section(self, filing: Filing) -> FilingSection:
        statement = select(FilingSection).where(
            FilingSection.filing_id == filing.id,
            FilingSection.section_key == FULL_DOCUMENT_SECTION_KEY,
        )
        section = self._db.scalar(statement)
        if section is None:
            section = FilingSection(
                filing_id=filing.id,
                section_key=FULL_DOCUMENT_SECTION_KEY,
                section_title=FULL_DOCUMENT_SECTION_TITLE,
                section_order=0,
                normalized_text="",
                start_offset=0,
                end_offset=0,
                extraction_confidence=FULL_DOCUMENT_EXTRACTION_CONFIDENCE,
                extraction_method=FULL_DOCUMENT_EXTRACTION_METHOD,
            )
            self._db.add(section)

        return section


def extract_normalized_text(html: str) -> str:
    cleaned_html = clean_filing_html(html)
    return normalize_extracted_text(get_text(cleaned_html))


def clean_filing_html(html: str) -> str:
    tree = HTMLParser(html)
    for selector in (
        "script",
        "style",
        "noscript",
        "[hidden]",
        '[aria-hidden="true"]',
        '[style*="display:none"]',
        '[style*="display: none"]',
        '[style*="visibility:hidden"]',
        '[style*="visibility: hidden"]',
    ):
        for node in tree.css(selector):
            node.decompose()

    return tree.html


def normalize_extracted_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = "\n".join(lines).strip()
    return _EXCESSIVE_BLANK_LINES.sub("\n\n", normalized)
