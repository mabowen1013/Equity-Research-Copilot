from __future__ import annotations

import hashlib
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import XMLParsedAsHTMLWarning
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, Filing, FilingDocument, FilingSection

SEC2MD_PARSER_VERSION = "sec2md_v1"
SEC2MD_EXTRACTION_METHOD = "sec2md"
SEC2MD_FULL_DOCUMENT_FALLBACK_METHOD = "sec2md_full_document_fallback"
SEC2MD_EXTRACTION_CONFIDENCE = 95
SEC2MD_FULL_DOCUMENT_FALLBACK_CONFIDENCE = 70
SEC2MD_CHUNK_SIZE = 800
SEC2MD_CHUNK_OVERLAP = 0
SEC2MD_MAX_TABLE_TOKENS = 2048
SEC2MD_SUPPORTED_SECTION_FORM_TYPES = {
    "10-K",
    "10-Q",
    "8-K",
    "20-F",
    "SC 13D",
    "SC 13G",
}

_SPACE_RE = re.compile(r"\s+")
_ITEM_PREFIX_RE = re.compile(r"^item\s+", re.IGNORECASE)
_PART_PREFIX_RE = re.compile(r"^part\s+", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Sec2MDFilingProcessingError(RuntimeError):
    """Raised when sec2md cannot parse a cached filing document."""


@dataclass(frozen=True)
class Sec2MDFilingProcessingMetrics:
    total_pages: int
    total_sections: int
    total_chunks: int
    total_elements: int
    chunks_with_tables: int
    chunks_with_xbrl_tags: int
    full_document_fallback_count: int

    def as_payload(self) -> dict[str, int]:
        return {
            "total_pages": self.total_pages,
            "total_sections": self.total_sections,
            "total_chunks": self.total_chunks,
            "total_elements": self.total_elements,
            "chunks_with_tables": self.chunks_with_tables,
            "chunks_with_xbrl_tags": self.chunks_with_xbrl_tags,
            "full_document_fallback_count": self.full_document_fallback_count,
        }


@dataclass(frozen=True)
class Sec2MDSectionSource:
    section_key: str
    section_title: str
    markdown: str
    pages: list[Any]
    raw_section: Any | None
    extraction_confidence: int
    extraction_method: str
    page_start: int | None
    page_end: int | None
    display_page_start: int | None
    display_page_end: int | None


@dataclass(frozen=True)
class ParsedSec2MDFiling:
    pages: list[Any]
    full_markdown: str
    sections: list[Sec2MDSectionSource]
    used_full_document_fallback: bool


@dataclass(frozen=True)
class Sec2MDFilingProcessingResult:
    sections: list[FilingSection]
    chunks: list[DocumentChunk]
    metrics: Sec2MDFilingProcessingMetrics


class Sec2MDFilingProcessingService:
    def __init__(
        self,
        db: Session,
        *,
        chunk_size: int = SEC2MD_CHUNK_SIZE,
        chunk_overlap: int = SEC2MD_CHUNK_OVERLAP,
        max_table_tokens: int = SEC2MD_MAX_TABLE_TOKENS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if chunk_size <= 0:
            raise Sec2MDFilingProcessingError("sec2md chunk size must be positive.")
        if chunk_overlap < 0:
            raise Sec2MDFilingProcessingError("sec2md chunk overlap cannot be negative.")

        self._db = db
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_table_tokens = max_table_tokens
        self._clock = clock
        self.last_processing_metrics: Sec2MDFilingProcessingMetrics | None = None

    def process_filing_document(
        self,
        filing: Filing,
        document: FilingDocument,
    ) -> Sec2MDFilingProcessingResult:
        self.last_processing_metrics = None
        html = self._read_cached_html(document)
        parsed = parse_sec2md_filing(html, filing.form_type)

        self.delete_chunks_for_filing(filing)
        sections = self._upsert_sections(filing, parsed)
        chunks = self._create_chunks_for_sections(filing, sections, parsed.sections)

        now = self._clock()
        document.parser_version = SEC2MD_PARSER_VERSION
        document.updated_at = now

        metrics = Sec2MDFilingProcessingMetrics(
            total_pages=len(parsed.pages),
            total_sections=len(sections),
            total_chunks=len(chunks),
            total_elements=sum(len(getattr(page, "elements", None) or []) for page in parsed.pages),
            chunks_with_tables=sum(1 for chunk in chunks if chunk.has_table),
            chunks_with_xbrl_tags=sum(1 for chunk in chunks if chunk.xbrl_tags),
            full_document_fallback_count=1 if parsed.used_full_document_fallback else 0,
        )
        self.last_processing_metrics = metrics
        self._db.flush()
        return Sec2MDFilingProcessingResult(sections=sections, chunks=chunks, metrics=metrics)

    def delete_chunks_for_filing(self, filing: Filing) -> int:
        existing_statement = select(DocumentChunk).where(DocumentChunk.filing_id == filing.id)
        existing_chunks = list(self._db.scalars(existing_statement).all())
        for chunk in existing_chunks:
            self._db.delete(chunk)
        self._db.flush()
        return len(existing_chunks)

    def _read_cached_html(self, document: FilingDocument) -> str:
        if document.status != "downloaded":
            raise Sec2MDFilingProcessingError(
                "Filing document must be downloaded before sec2md processing.",
            )

        if not document.cache_path:
            raise Sec2MDFilingProcessingError(
                "Downloaded filing document does not have a cache path.",
            )

        cache_path = Path(document.cache_path)
        if not cache_path.is_file():
            raise Sec2MDFilingProcessingError(f"Cached filing document was not found: {cache_path}")

        return cache_path.read_bytes().decode("utf-8", errors="replace")

    def _upsert_sections(
        self,
        filing: Filing,
        parsed: ParsedSec2MDFiling,
    ) -> list[FilingSection]:
        statement = select(FilingSection).where(FilingSection.filing_id == filing.id)
        existing_sections = list(self._db.scalars(statement).all())
        existing_by_key = {section.section_key: section for section in existing_sections}
        desired_keys = {section.section_key for section in parsed.sections}

        for index, section in enumerate(existing_sections):
            section.section_order = 10_000 + index
        self._db.flush()

        for section in existing_sections:
            if section.section_key not in desired_keys:
                self._db.delete(section)
        self._db.flush()

        spans = _find_section_spans(parsed.full_markdown, parsed.sections)
        now = self._clock()
        stored_sections: list[FilingSection] = []

        for order, (source, (start_offset, end_offset)) in enumerate(zip(parsed.sections, spans)):
            section = existing_by_key.get(source.section_key)
            if section is None:
                section = FilingSection(
                    filing_id=filing.id,
                    section_key=source.section_key,
                    section_title=source.section_title,
                    section_order=order,
                    normalized_text=source.markdown,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    display_page_start=source.display_page_start,
                    display_page_end=source.display_page_end,
                    extraction_confidence=source.extraction_confidence,
                    extraction_method=source.extraction_method,
                    created_at=now,
                    updated_at=now,
                )
                self._db.add(section)
            else:
                section.section_title = source.section_title
                section.section_order = order
                section.normalized_text = source.markdown
                section.start_offset = start_offset
                section.end_offset = end_offset
                section.page_start = source.page_start
                section.page_end = source.page_end
                section.display_page_start = source.display_page_start
                section.display_page_end = source.display_page_end
                section.extraction_confidence = source.extraction_confidence
                section.extraction_method = source.extraction_method
                section.updated_at = now

            stored_sections.append(section)

        self._db.flush()
        return stored_sections

    def _create_chunks_for_sections(
        self,
        filing: Filing,
        sections: list[FilingSection],
        sources: list[Sec2MDSectionSource],
    ) -> list[DocumentChunk]:
        import sec2md

        stored_chunks: list[DocumentChunk] = []
        for section, source in zip(sections, sources):
            if section.id is None:
                raise Sec2MDFilingProcessingError(
                    "Filing sections must be persisted before chunking.",
                )

            sec2md_chunks = sec2md.chunk_pages(
                _content_only_pages(source),
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
                max_table_tokens=self._max_table_tokens,
            )

            local_cursor = 0
            for chunk_index, sec2md_chunk in enumerate(sec2md_chunks):
                chunk_text = str(sec2md_chunk.content).strip()
                if not chunk_text:
                    continue

                local_start, local_end = _find_text_span(
                    section.normalized_text,
                    chunk_text,
                    local_cursor,
                )
                local_cursor = max(local_cursor, local_end)
                display_page_range = getattr(sec2md_chunk, "display_page_range", None)
                page_range = getattr(sec2md_chunk, "page_range", None)
                matched_elements = _match_elements_for_chunk(source.pages, chunk_text)
                xbrl_tags = sorted(
                    {
                        str(tag)
                        for element in matched_elements
                        for tag in (getattr(element, "tags", None) or [])
                    },
                )
                element_ids = [str(element.id) for element in matched_elements]
                now = self._clock()
                chunk = DocumentChunk(
                    filing_id=filing.id,
                    section_id=section.id,
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    token_count=max(1, int(getattr(sec2md_chunk, "num_tokens", 0) or 0)),
                    start_offset=section.start_offset + local_start,
                    end_offset=section.start_offset + local_end,
                    page_start=_range_start(page_range),
                    page_end=_range_end(page_range),
                    display_page_start=_range_start(display_page_range),
                    display_page_end=_range_end(display_page_range),
                    element_ids=element_ids or None,
                    xbrl_tags=xbrl_tags or None,
                    has_table=bool(getattr(sec2md_chunk, "has_table", False)),
                    has_image=bool(getattr(sec2md_chunk, "has_image", False)),
                    text_hash=hash_text(chunk_text),
                    accession_number=filing.accession_number,
                    form_type=filing.form_type,
                    filing_date=filing.filing_date,
                    section_key=section.section_key,
                    sec_url=filing.sec_primary_document_url or filing.sec_filing_url,
                    created_at=now,
                    updated_at=now,
                )
                self._db.add(chunk)
                stored_chunks.append(chunk)

        self._db.flush()
        return stored_chunks


def parse_sec2md_filing(html: str, form_type: str) -> ParsedSec2MDFiling:
    import sec2md

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        pages = sec2md.parse_filing(html, include_elements=True, embed_images=False)

    pages = [page for page in pages if str(getattr(page, "content", "")).strip()]
    full_markdown = "\n\n".join(str(page.content).strip() for page in pages).strip()
    if not full_markdown:
        raise Sec2MDFilingProcessingError("sec2md did not produce extractable markdown.")

    normalized_form_type = form_type.upper()
    raw_sections = []
    if normalized_form_type in SEC2MD_SUPPORTED_SECTION_FORM_TYPES:
        try:
            raw_sections = sec2md.extract_sections(pages, filing_type=normalized_form_type)
        except Exception as exc:
            raise Sec2MDFilingProcessingError(f"sec2md section extraction failed: {exc}") from exc

    section_sources = [
        _section_source_from_sec2md_section(section, normalized_form_type)
        for section in raw_sections
        if str(section.markdown()).strip()
    ]
    section_sources = _dedupe_section_sources(section_sources)
    if section_sources:
        return ParsedSec2MDFiling(
            pages=pages,
            full_markdown=full_markdown,
            sections=section_sources,
            used_full_document_fallback=False,
        )

    return ParsedSec2MDFiling(
        pages=pages,
        full_markdown=full_markdown,
        sections=[
            Sec2MDSectionSource(
                section_key="full_document",
                section_title="Full document",
                markdown=full_markdown,
                pages=pages,
                raw_section=None,
                extraction_confidence=SEC2MD_FULL_DOCUMENT_FALLBACK_CONFIDENCE,
                extraction_method=SEC2MD_FULL_DOCUMENT_FALLBACK_METHOD,
                page_start=pages[0].number if pages else None,
                page_end=pages[-1].number if pages else None,
                display_page_start=getattr(pages[0], "display_page", None) if pages else None,
                display_page_end=getattr(pages[-1], "display_page", None) if pages else None,
            ),
        ],
        used_full_document_fallback=True,
    )


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _section_source_from_sec2md_section(section: Any, form_type: str) -> Sec2MDSectionSource:
    pages = list(getattr(section, "pages", []) or [])
    page_start, page_end = _page_range_from_pages(pages)
    display_page_start = getattr(pages[0], "display_page", None) if pages else None
    display_page_end = getattr(pages[-1], "display_page", None) if pages else None

    return Sec2MDSectionSource(
        section_key=_build_section_key(
            form_type=form_type,
            part=getattr(section, "part", None),
            item=getattr(section, "item", None),
        ),
        section_title=_build_section_title(
            part=getattr(section, "part", None),
            item=getattr(section, "item", None),
            title=getattr(section, "item_title", None),
        ),
        markdown=str(section.markdown()).strip(),
        pages=pages,
        raw_section=section,
        extraction_confidence=SEC2MD_EXTRACTION_CONFIDENCE,
        extraction_method=SEC2MD_EXTRACTION_METHOD,
        page_start=page_start,
        page_end=page_end,
        display_page_start=display_page_start,
        display_page_end=display_page_end,
    )


def _build_section_key(*, form_type: str, part: str | None, item: str | None) -> str:
    part_key = _slug_part(part)
    item_key = _slug_item(item)
    if item_key is None:
        return part_key or "full_document"

    if form_type == "10-Q" and part_key is not None:
        return f"{part_key}_item_{item_key}"

    return f"item_{item_key}"


def _build_section_title(*, part: str | None, item: str | None, title: str | None) -> str:
    labels = []
    if part:
        labels.append(part.title())
    if item:
        labels.append(item.title())

    prefix = " ".join(labels).strip()
    clean_title = _SPACE_RE.sub(" ", str(title or "")).strip(" .-:")
    if prefix and clean_title:
        return f"{prefix}. {clean_title}"
    return prefix or clean_title or "Full document"


def _slug_part(part: str | None) -> str | None:
    if not part:
        return None
    value = _PART_PREFIX_RE.sub("", part.strip()).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return f"part_{value}" if value else None


def _slug_item(item: str | None) -> str | None:
    if not item:
        return None
    value = _ITEM_PREFIX_RE.sub("", item.strip()).lower()
    value = value.replace(".", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    return value or None


def _page_range_from_pages(pages: list[Any]) -> tuple[int | None, int | None]:
    if not pages:
        return None, None
    return int(pages[0].number), int(pages[-1].number)


def _content_only_pages(source: Sec2MDSectionSource) -> list[Any]:
    from sec2md.models import Page

    pages = []
    for page in source.pages:
        content = str(getattr(page, "content", "")).strip()
        if not content:
            continue

        pages.append(
            Page(
                number=page.number,
                content=content,
                elements=None,
                text_blocks=None,
                display_page=getattr(page, "display_page", None),
            ),
        )

    if pages:
        return pages

    return [
        Page(
            number=source.page_start or 1,
            content=source.markdown,
            elements=None,
            text_blocks=None,
            display_page=source.display_page_start,
        ),
    ]


def _match_elements_for_chunk(pages: list[Any], chunk_text: str) -> list[Any]:
    chunk_compact = _compact_for_match(chunk_text)
    if not chunk_compact:
        return []

    chunk_has_table = _looks_like_markdown_table(chunk_text)
    matched = []
    seen_ids: set[str] = set()
    for page in pages:
        for element in getattr(page, "elements", None) or []:
            element_id = str(getattr(element, "id", ""))
            if not element_id or element_id in seen_ids:
                continue

            element_text = str(getattr(element, "content", "")).strip()
            element_compact = _compact_for_match(element_text)
            if not element_compact:
                continue

            if element_compact in chunk_compact:
                matched.append(element)
                seen_ids.add(element_id)
                continue

            if (
                chunk_has_table
                and str(getattr(element, "kind", "")).lower() == "table"
                and _word_overlap_ratio(chunk_text, element_text) >= 0.75
            ):
                matched.append(element)
                seen_ids.add(element_id)
                continue

            if (
                chunk_compact in element_compact
                and (
                    len(element_compact) <= int(len(chunk_compact) * 1.2)
                    or len(element_compact) - len(chunk_compact) <= 50
                )
            ):
                matched.append(element)
                seen_ids.add(element_id)

    return matched


def _compact_for_match(text: str) -> str:
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", "", text).lower()


def _looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines()]
    table_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
    return len(table_lines) >= 2


def _word_overlap_ratio(left: str, right: str) -> float:
    left_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9]+", left.lower()))
    right_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9]+", right.lower()))
    denominator = min(len(left_words), len(right_words))
    if denominator == 0:
        return 0.0
    return len(left_words & right_words) / denominator


def _range_start(value: Any) -> int | None:
    if isinstance(value, (tuple, list)) and value:
        return int(value[0]) if value[0] is not None else None
    return None


def _range_end(value: Any) -> int | None:
    if isinstance(value, (tuple, list)) and len(value) > 1:
        return int(value[1]) if value[1] is not None else None
    return None


def _find_section_spans(
    full_markdown: str,
    sections: list[Sec2MDSectionSource],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for section in sections:
        start, end = _find_text_span(full_markdown, section.markdown, cursor)
        spans.append((start, end))
        cursor = max(cursor, end)
    return spans


def _find_text_span(source_text: str, text: str, cursor: int = 0) -> tuple[int, int]:
    if not text:
        return cursor, cursor

    idx = source_text.find(text, cursor)
    if idx < 0:
        idx = source_text.find(text)
    if idx >= 0:
        return idx, idx + len(text)

    normalized_source = _SPACE_RE.sub(" ", source_text)
    normalized_text = _SPACE_RE.sub(" ", text)
    normalized_idx = normalized_source.find(normalized_text)
    if normalized_idx >= 0:
        return normalized_idx, min(len(source_text), normalized_idx + len(text))

    start = min(max(cursor, 0), len(source_text))
    end = min(len(source_text), start + len(text))
    return start, end


def _dedupe_section_sources(
    sections: list[Sec2MDSectionSource],
) -> list[Sec2MDSectionSource]:
    seen: dict[str, Sec2MDSectionSource] = {}
    order: list[str] = []
    for section in sections:
        existing = seen.get(section.section_key)
        if existing is None:
            seen[section.section_key] = section
            order.append(section.section_key)
            continue

        if len(section.markdown) > len(existing.markdown):
            seen[section.section_key] = section

    return [seen[key] for key in order]
