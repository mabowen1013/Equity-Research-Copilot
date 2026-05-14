from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from warnings import catch_warnings, simplefilter

from inscriptis import get_text
from sec_parser import Edgar10QParser, TreeBuilder
from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Filing, FilingDocument, FilingSection

FULL_DOCUMENT_SECTION_KEY = "full_document"
FULL_DOCUMENT_SECTION_TITLE = "Full document"
FILING_TEXT_PARSER_VERSION = "sec_parser_validated_regex_offsets_v3"
FULL_DOCUMENT_EXTRACTION_METHOD = "full_document_fallback"
SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD = "sec_parser_validated_regex_offsets"
REGEX_EXTRACTION_METHOD = "regex_fallback"
FULL_DOCUMENT_EXTRACTION_CONFIDENCE = 50
SEC_PARSER_VALIDATED_REGEX_OFFSETS_CONFIDENCE = 90
REGEX_EXTRACTION_CONFIDENCE = 80
SEC_PARSER_PRIMARY_FORM_TYPES = {"10-Q"}

_EXCESSIVE_BLANK_LINES = re.compile(r"\n{3,}")
_PART_HEADING_RE = re.compile(r"^part\s+(?P<part>i{1,3}|iv|v|vi{0,3}|ix|x)\b", re.IGNORECASE)
_ITEM_HEADING_RE = re.compile(
    r"^item\s+(?P<item>\d{1,2}(?:[a-z])?|\d{1,2}\.\d{2})\s*[\.\-–—:]?\s+(?P<title>.+)$",
    re.IGNORECASE,
)
_ITEM_ONLY_RE = re.compile(
    r"^item\s+(?P<item>\d{1,2}(?:[a-z])?|\d{1,2}\.\d{2})\s*[\.\-–—:]?\s*$",
    re.IGNORECASE,
)
_TRAILING_PAGE_NUMBER_RE = re.compile(r"\s+\.{0,}\s*\d+\s*$")
_STYLE_WHITESPACE_RE = re.compile(r"\s+")
_SPACE_RE = re.compile(r"\s+")


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingTextExtractionError(RuntimeError):
    """Raised when cached filing HTML cannot be converted into filing text."""


@dataclass(frozen=True)
class ExtractedFilingSection:
    section_key: str
    section_title: str
    normalized_text: str
    start_offset: int
    end_offset: int
    extraction_confidence: int
    extraction_method: str


@dataclass(frozen=True)
class FilingExtractionMetrics:
    total_sections: int = 0
    sec_parser_validated_regex_offsets_count: int = 0
    regex_fallback_count: int = 0
    full_document_fallback_count: int = 0

    def as_payload(self) -> dict[str, int]:
        return {
            "total_sections": self.total_sections,
            "sec_parser_validated_regex_offsets_count": (
                self.sec_parser_validated_regex_offsets_count
            ),
            "regex_fallback_count": self.regex_fallback_count,
            "full_document_fallback_count": self.full_document_fallback_count,
        }


@dataclass(frozen=True)
class SemanticFilingSection:
    section_key: str
    section_title: str
    section_text: str


@dataclass(frozen=True)
class SectionHeadingCandidate:
    section_key: str
    section_title: str
    start_offset: int
    heading_end_offset: int


@dataclass(frozen=True)
class NormalizedTextLine:
    text: str
    start_offset: int
    end_offset: int


class FilingTextExtractionService:
    def __init__(
        self,
        db: Session,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._clock = clock
        self.last_extraction_metrics: FilingExtractionMetrics | None = None

    def extract_filing_sections(
        self,
        filing: Filing,
        document: FilingDocument,
    ) -> list[FilingSection]:
        self.last_extraction_metrics = None
        html = self._read_cached_html(document)
        cleaned_html = clean_filing_html(html)
        normalized_text = normalize_extracted_text(get_text(cleaned_html))
        if not normalized_text:
            raise FilingTextExtractionError("Cached filing document did not contain extractable text.")

        extracted_sections = extract_structured_sections(
            normalized_text,
            filing.form_type,
            cleaned_html=cleaned_html,
        )
        if not extracted_sections:
            extracted_sections = [
                ExtractedFilingSection(
                    section_key=FULL_DOCUMENT_SECTION_KEY,
                    section_title=FULL_DOCUMENT_SECTION_TITLE,
                    normalized_text=normalized_text,
                    start_offset=0,
                    end_offset=len(normalized_text),
                    extraction_confidence=FULL_DOCUMENT_EXTRACTION_CONFIDENCE,
                    extraction_method=FULL_DOCUMENT_EXTRACTION_METHOD,
                ),
            ]

        self.last_extraction_metrics = build_extraction_metrics(extracted_sections)
        sections = self._upsert_sections(filing, extracted_sections)
        now = self._clock()
        document.parser_version = FILING_TEXT_PARSER_VERSION
        document.updated_at = now
        self._db.flush()
        return sections

    def _read_cached_html(self, document: FilingDocument) -> str:
        if document.status != "downloaded":
            raise FilingTextExtractionError("Filing document must be downloaded before text extraction.")

        if not document.cache_path:
            raise FilingTextExtractionError("Downloaded filing document does not have a cache path.")

        cache_path = Path(document.cache_path)
        if not cache_path.is_file():
            raise FilingTextExtractionError(f"Cached filing document was not found: {cache_path}")

        return cache_path.read_bytes().decode("utf-8", errors="replace")

    def _upsert_sections(
        self,
        filing: Filing,
        extracted_sections: list[ExtractedFilingSection],
    ) -> list[FilingSection]:
        statement = select(FilingSection).where(FilingSection.filing_id == filing.id)
        existing_sections = list(self._db.scalars(statement).all())
        existing_by_key = {section.section_key: section for section in existing_sections}
        desired_keys = {section.section_key for section in extracted_sections}

        for index, section in enumerate(existing_sections):
            section.section_order = 10_000 + index
        self._db.flush()

        for section in existing_sections:
            if section.section_key not in desired_keys:
                self._db.delete(section)
        self._db.flush()

        now = self._clock()
        stored_sections: list[FilingSection] = []
        for order, extracted in enumerate(extracted_sections):
            section = existing_by_key.get(extracted.section_key)
            if section is None:
                section = FilingSection(
                    filing_id=filing.id,
                    section_key=extracted.section_key,
                    section_title=extracted.section_title,
                    section_order=order,
                    normalized_text=extracted.normalized_text,
                    start_offset=extracted.start_offset,
                    end_offset=extracted.end_offset,
                    extraction_confidence=extracted.extraction_confidence,
                    extraction_method=extracted.extraction_method,
                )
                self._db.add(section)
            else:
                section.section_title = extracted.section_title
                section.section_order = order
                section.normalized_text = extracted.normalized_text
                section.start_offset = extracted.start_offset
                section.end_offset = extracted.end_offset
                section.extraction_confidence = extracted.extraction_confidence
                section.extraction_method = extracted.extraction_method
                section.updated_at = now

            stored_sections.append(section)

        self._db.flush()
        return stored_sections


def extract_normalized_text(html: str) -> str:
    cleaned_html = clean_filing_html(html)
    return normalize_extracted_text(get_text(cleaned_html))


def clean_filing_html(html: str) -> str:
    tree = HTMLParser(html)
    for selector in (
        "script",
        "style",
        "noscript",
        "template",
        "[hidden]",
        '[aria-hidden="true"]',
    ):
        for node in tree.css(selector):
            node.decompose()

    for node in list(tree.css("[style]")):
        style = str(node.attrs.get("style", ""))
        normalized_style = _STYLE_WHITESPACE_RE.sub("", style.lower())
        if "display:none" in normalized_style or "visibility:hidden" in normalized_style:
            node.decompose()

    return tree.html


def normalize_extracted_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = "\n".join(lines).strip()
    return _EXCESSIVE_BLANK_LINES.sub("\n\n", normalized)


def extract_structured_sections(
    normalized_text: str,
    form_type: str,
    *,
    cleaned_html: str | None = None,
) -> list[ExtractedFilingSection]:
    regex_sections = extract_regex_sections(normalized_text, form_type)
    if cleaned_html is None or not should_use_sec_parser_primary(form_type):
        return regex_sections

    semantic_sections = extract_sec_parser_sections(cleaned_html, form_type)
    if not semantic_sections:
        return regex_sections

    return validate_regex_sections_with_sec_parser(
        semantic_sections,
        regex_sections=regex_sections,
    )


def should_use_sec_parser_primary(form_type: str) -> bool:
    return form_type.upper() in SEC_PARSER_PRIMARY_FORM_TYPES


def build_extraction_metrics(sections: list[object]) -> FilingExtractionMetrics:
    method_counts = Counter(
        str(getattr(section, "extraction_method", ""))
        for section in sections
    )
    return FilingExtractionMetrics(
        total_sections=len(sections),
        sec_parser_validated_regex_offsets_count=method_counts[
            SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD
        ],
        regex_fallback_count=method_counts[REGEX_EXTRACTION_METHOD],
        full_document_fallback_count=method_counts[FULL_DOCUMENT_EXTRACTION_METHOD],
    )


def extract_sec_parser_sections(html: str, form_type: str) -> list[SemanticFilingSection]:
    if not should_use_sec_parser_primary(form_type):
        return []

    try:
        with catch_warnings():
            simplefilter("ignore")
            elements = Edgar10QParser().parse(html)
            tree = TreeBuilder().build(elements)
    except Exception:
        return []

    sections_by_key: dict[str, SemanticFilingSection] = {}
    normalized_form_type = form_type.upper()
    for node in tree.nodes:
        heading_text = _get_node_text(node)
        item_heading = _parse_item_heading(heading_text)
        if item_heading is None:
            continue

        descendants = list(node.get_descendants())
        if not descendants:
            continue

        section_text = _section_text_from_node(node)
        if not _has_section_body(section_text, heading_text):
            continue

        item, title = item_heading
        current_part = _find_part_context(node)
        section_key = _build_section_key(
            form_type=normalized_form_type,
            item=item,
            current_part=current_part,
        )
        section = SemanticFilingSection(
            section_key=section_key,
            section_title=_build_section_title(
                form_type=normalized_form_type,
                item=item,
                title=title,
                current_part=current_part,
            ),
            section_text=section_text,
        )
        existing = sections_by_key.get(section.section_key)
        if existing is None or len(section.section_text) > len(existing.section_text):
            sections_by_key[section.section_key] = section

    return list(sections_by_key.values())


def validate_regex_sections_with_sec_parser(
    semantic_sections: list[SemanticFilingSection],
    *,
    regex_sections: list[ExtractedFilingSection],
) -> list[ExtractedFilingSection]:
    semantic_by_key = {
        section.section_key: section
        for section in semantic_sections
    }
    validated_sections: list[ExtractedFilingSection] = []

    for regex_section in regex_sections:
        semantic_section = semantic_by_key.get(regex_section.section_key)
        if semantic_section is None:
            validated_sections.append(regex_section)
            continue

        validated_sections.append(
            ExtractedFilingSection(
                section_key=regex_section.section_key,
                section_title=semantic_section.section_title,
                normalized_text=regex_section.normalized_text,
                start_offset=regex_section.start_offset,
                end_offset=regex_section.end_offset,
                extraction_confidence=SEC_PARSER_VALIDATED_REGEX_OFFSETS_CONFIDENCE,
                extraction_method=SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD,
            ),
        )

    return sorted(
        _dedupe_sections_by_key(validated_sections),
        key=lambda section: section.start_offset,
    )


def extract_regex_sections(normalized_text: str, form_type: str) -> list[ExtractedFilingSection]:
    candidates = _find_heading_candidates(normalized_text, form_type)
    if not candidates:
        return []

    sections_by_key: dict[str, ExtractedFilingSection] = {}
    for index, candidate in enumerate(candidates):
        next_start = (
            candidates[index + 1].start_offset
            if index + 1 < len(candidates)
            else len(normalized_text)
        )
        section_text = normalized_text[candidate.start_offset:next_start].strip()
        body_text = normalized_text[candidate.heading_end_offset:next_start].strip()
        if not section_text or not body_text:
            continue

        extracted = ExtractedFilingSection(
            section_key=candidate.section_key,
            section_title=candidate.section_title,
            normalized_text=section_text,
            start_offset=candidate.start_offset,
            end_offset=next_start,
            extraction_confidence=REGEX_EXTRACTION_CONFIDENCE,
            extraction_method=REGEX_EXTRACTION_METHOD,
        )
        existing = sections_by_key.get(extracted.section_key)
        if existing is None or len(extracted.normalized_text) > len(existing.normalized_text):
            sections_by_key[extracted.section_key] = extracted

    return sorted(sections_by_key.values(), key=lambda section: section.start_offset)


def _find_heading_candidates(normalized_text: str, form_type: str) -> list[SectionHeadingCandidate]:
    normalized_form_type = form_type.upper()
    candidates: list[SectionHeadingCandidate] = []
    current_part: str | None = None
    lines = _split_normalized_text_lines(normalized_text)

    for index, line in enumerate(lines):
        if not line.text:
            continue

        part_match = _PART_HEADING_RE.match(line.text)
        if part_match:
            current_part = _normalize_part_token(part_match.group("part"))
            continue

        item_heading = _parse_item_heading(line.text)
        if item_heading is not None:
            item, title = item_heading
            candidates.append(
                SectionHeadingCandidate(
                    section_key=_build_section_key(
                        form_type=normalized_form_type,
                        item=item,
                        current_part=current_part,
                    ),
                    section_title=_build_section_title(
                        form_type=normalized_form_type,
                        item=item,
                        title=title,
                        current_part=current_part,
                    ),
                    start_offset=line.start_offset,
                    heading_end_offset=line.end_offset,
                ),
            )
            continue

        item = _parse_item_only_heading(line.text)
        if item is None:
            continue

        title_line = _find_split_heading_title_line(lines, start_index=index + 1)
        if title_line is None:
            continue

        title = _clean_heading_title(title_line.text)
        candidates.append(
            SectionHeadingCandidate(
                section_key=_build_section_key(
                    form_type=normalized_form_type,
                    item=item,
                    current_part=current_part,
                ),
                section_title=_build_section_title(
                    form_type=normalized_form_type,
                    item=item,
                    title=title,
                    current_part=current_part,
                ),
                start_offset=line.start_offset,
                heading_end_offset=title_line.end_offset,
            ),
        )

    return candidates


def _split_normalized_text_lines(normalized_text: str) -> list[NormalizedTextLine]:
    lines: list[NormalizedTextLine] = []
    offset = 0
    for raw_line in normalized_text.splitlines(keepends=True):
        leading_spaces = len(raw_line) - len(raw_line.lstrip())
        line_start = offset + leading_spaces
        line_end = offset + len(raw_line.rstrip("\n"))
        offset += len(raw_line)
        lines.append(
            NormalizedTextLine(
                text=raw_line.strip(),
                start_offset=line_start,
                end_offset=line_end,
            ),
        )

    return lines


def _find_split_heading_title_line(
    lines: list[NormalizedTextLine],
    *,
    start_index: int,
) -> NormalizedTextLine | None:
    for line in lines[start_index:]:
        if not line.text:
            continue

        if _is_heading_boundary_line(line.text):
            return None

        title = _clean_heading_title(line.text)
        if not title:
            return None

        if not any(character.isalpha() for character in title):
            return None

        return NormalizedTextLine(
            text=title,
            start_offset=line.start_offset,
            end_offset=line.end_offset,
        )

    return None


def _is_heading_boundary_line(text: str) -> bool:
    return (
        _PART_HEADING_RE.match(text) is not None
        or _parse_item_heading(text) is not None
        or _parse_item_only_heading(text) is not None
    )


def _section_text_from_node(node: object) -> str:
    source_codes = [_get_node_source_code(node)]
    source_codes.extend(_get_node_source_code(descendant) for descendant in node.get_descendants())
    return extract_normalized_text("".join(source_codes))


def _dedupe_sections_by_key(
    sections: list[ExtractedFilingSection],
) -> list[ExtractedFilingSection]:
    sections_by_key: dict[str, ExtractedFilingSection] = {}
    for section in sections:
        existing = sections_by_key.get(section.section_key)
        if existing is None or len(section.normalized_text) > len(existing.normalized_text):
            sections_by_key[section.section_key] = section

    return list(sections_by_key.values())


def _find_part_context(node: object) -> str | None:
    parent = getattr(node, "parent", None)
    while parent is not None:
        text = _get_node_text(parent)
        part_match = _PART_HEADING_RE.match(text)
        if part_match:
            return _normalize_part_token(part_match.group("part"))

        parent = getattr(parent, "parent", None)

    return None


def _has_section_body(section_text: str, heading_text: str) -> bool:
    section_key = _normalize_title_key(section_text)
    heading_key = _normalize_title_key(heading_text)
    return bool(section_key and heading_key and section_key != heading_key)


def _parse_item_heading(text: str) -> tuple[str, str] | None:
    item_match = _ITEM_HEADING_RE.match(text.strip())
    if item_match is None:
        return None

    title = _clean_heading_title(item_match.group("title"))
    if not title:
        return None

    return item_match.group("item").lower(), title


def _parse_item_only_heading(text: str) -> str | None:
    item_match = _ITEM_ONLY_RE.match(text.strip())
    if item_match is None:
        return None

    return item_match.group("item").lower()


def _get_node_text(node: object) -> str:
    return _get_element_text(getattr(node, "semantic_element", node))


def _get_node_source_code(node: object) -> str:
    get_source_code = getattr(node, "get_source_code", None)
    if callable(get_source_code):
        return str(get_source_code())

    return _get_node_text(node)


def _get_element_text(element: object) -> str:
    text = getattr(element, "text", None)
    if isinstance(text, str):
        return text.strip()

    get_text_method = getattr(element, "get_text", None)
    if callable(get_text_method):
        value = get_text_method()
        if isinstance(value, str):
            return value.strip()

    return ""


def _build_section_key(
    *,
    form_type: str,
    item: str,
    current_part: str | None,
) -> str:
    item_key = item.replace(".", "_")
    if form_type == "10-Q" and current_part is not None:
        return f"part_{current_part}_item_{item_key}"

    return f"item_{item_key}"


def _build_section_title(
    *,
    form_type: str,
    item: str,
    title: str,
    current_part: str | None,
) -> str:
    item_label = item.upper()
    if form_type == "10-Q" and current_part is not None:
        return f"Part {current_part.upper()} Item {item_label}. {title}"

    return f"Item {item_label}. {title}"


def _clean_heading_title(title: str) -> str:
    return _TRAILING_PAGE_NUMBER_RE.sub("", title).strip(" .-–—:")


def _normalize_part_token(part: str) -> str:
    return part.lower()


def _normalize_title_key(title: str) -> str:
    return _SPACE_RE.sub(" ", title.strip().lower())
