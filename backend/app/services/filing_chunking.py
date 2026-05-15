from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, Filing, FilingSection

try:
    import tiktoken
except ImportError:  # pragma: no cover - dependency is declared for normal runtime.
    tiktoken = None

DEFAULT_CHUNK_TARGET_TOKENS = 800
DEFAULT_CHUNK_MAX_TOKENS = 1000
DEFAULT_TOKEN_ENCODING = "cl100k_base"
FILING_CHUNKER_VERSION = "section_text_chunker_v1"

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+(?:[\"')\]]+)?|[^.!?]+$")
_WORD_RE = re.compile(r"\S+")


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingChunkingError(RuntimeError):
    """Raised when filing sections cannot be converted into document chunks."""


@dataclass(frozen=True)
class TextBlock:
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ChunkCandidate:
    chunk_text: str
    token_count: int
    start_offset: int
    end_offset: int


class FilingChunkingService:
    def __init__(
        self,
        db: Session,
        *,
        target_tokens: int = DEFAULT_CHUNK_TARGET_TOKENS,
        max_tokens: int = DEFAULT_CHUNK_MAX_TOKENS,
        token_counter: Callable[[str], int] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if target_tokens <= 0:
            raise FilingChunkingError("Chunk target token count must be positive.")
        if max_tokens < target_tokens:
            raise FilingChunkingError("Chunk max token count must be greater than or equal to target.")

        self._db = db
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._token_counter = token_counter or count_tokens
        self._clock = clock

    def create_chunks_for_filing(
        self,
        filing: Filing,
        sections: list[FilingSection],
        *,
        delete_existing: bool = True,
    ) -> list[DocumentChunk]:
        if delete_existing:
            self.delete_chunks_for_filing(filing)

        stored_chunks: list[DocumentChunk] = []
        for section in sorted(sections, key=lambda item: item.section_order):
            if section.id is None:
                raise FilingChunkingError("Filing sections must be persisted before chunking.")

            candidates = build_text_chunk_candidates(
                section.normalized_text,
                target_tokens=self._target_tokens,
                max_tokens=self._max_tokens,
                token_counter=self._token_counter,
            )
            for chunk_index, candidate in enumerate(candidates):
                now = self._clock()
                chunk = DocumentChunk(
                    filing_id=filing.id,
                    section_id=section.id,
                    chunk_index=chunk_index,
                    chunk_text=candidate.chunk_text,
                    token_count=candidate.token_count,
                    start_offset=section.start_offset + candidate.start_offset,
                    end_offset=section.start_offset + candidate.end_offset,
                    text_hash=hash_text(candidate.chunk_text),
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

    def delete_chunks_for_filing(self, filing: Filing) -> int:
        existing_statement = select(DocumentChunk).where(DocumentChunk.filing_id == filing.id)
        existing_chunks = list(self._db.scalars(existing_statement).all())
        for chunk in existing_chunks:
            self._db.delete(chunk)
        self._db.flush()
        return len(existing_chunks)


def build_text_chunk_candidates(
    text: str,
    *,
    target_tokens: int = DEFAULT_CHUNK_TARGET_TOKENS,
    max_tokens: int = DEFAULT_CHUNK_MAX_TOKENS,
    token_counter: Callable[[str], int] | None = None,
) -> list[ChunkCandidate]:
    token_counter = token_counter or count_tokens
    blocks = split_text_blocks(text)
    chunks: list[ChunkCandidate] = []
    current_start: int | None = None
    current_end: int | None = None
    current_tokens = 0

    for block in blocks:
        block_tokens = token_counter(block.text)
        if block_tokens > max_tokens:
            chunks.extend(
                _flush_chunk(
                    text,
                    current_start,
                    current_end,
                    token_counter=token_counter,
                ),
            )
            current_start = None
            current_end = None
            current_tokens = 0
            chunks.extend(
                split_oversized_block(
                    text,
                    block,
                    target_tokens=target_tokens,
                    max_tokens=max_tokens,
                    token_counter=token_counter,
                ),
            )
            continue

        if current_start is None:
            current_start = block.start_offset
            current_end = block.end_offset
            current_tokens = block_tokens
            continue

        candidate = _make_chunk_candidate(
            text,
            current_start,
            block.end_offset,
            token_counter=token_counter,
        )
        should_merge = (
            candidate.token_count <= target_tokens
            or (
                current_tokens < max(1, target_tokens // 2)
                and candidate.token_count <= max_tokens
            )
        )
        if should_merge:
            current_end = block.end_offset
            current_tokens = candidate.token_count
            continue

        chunks.extend(
            _flush_chunk(
                text,
                current_start,
                current_end,
                token_counter=token_counter,
            ),
        )
        current_start = block.start_offset
        current_end = block.end_offset
        current_tokens = block_tokens

    chunks.extend(
        _flush_chunk(
            text,
            current_start,
            current_end,
            token_counter=token_counter,
        ),
    )
    return chunks


def split_text_blocks(text: str) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    block_start: int | None = None
    block_end: int | None = None
    offset = 0

    for line in text.splitlines(keepends=True):
        line_without_newline = line.rstrip("\n")
        line_start = offset
        line_end = offset + len(line_without_newline)
        offset += len(line)

        if not line_without_newline.strip():
            if block_start is not None and block_end is not None:
                blocks.append(_make_text_block(text, block_start, block_end))
                block_start = None
                block_end = None
            continue

        if block_start is None:
            block_start = line_start + len(line_without_newline) - len(line_without_newline.lstrip())
        block_end = line_end - (len(line_without_newline) - len(line_without_newline.rstrip()))

    if block_start is not None and block_end is not None:
        blocks.append(_make_text_block(text, block_start, block_end))

    return [block for block in blocks if block.text]


def split_oversized_block(
    source_text: str,
    block: TextBlock,
    *,
    target_tokens: int,
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[ChunkCandidate]:
    line_blocks = _split_block_into_lines(block)
    if len(line_blocks) > 1:
        return _merge_blocks(
            source_text,
            _split_blocks_by_words_when_needed(
                source_text,
                line_blocks,
                max_tokens=max_tokens,
                token_counter=token_counter,
            ),
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            token_counter=token_counter,
        )

    sentence_blocks = _split_block_into_sentences(block)
    return _merge_blocks(
        source_text,
        _split_blocks_by_words_when_needed(
            source_text,
            sentence_blocks,
            max_tokens=max_tokens,
            token_counter=token_counter,
        ),
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )


def count_tokens(text: str, *, encoding_name: str = DEFAULT_TOKEN_ENCODING) -> int:
    if tiktoken is None:
        raise FilingChunkingError("tiktoken is required for filing chunk token counts.")

    return len(_get_token_encoding(encoding_name).encode(text))


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _get_token_encoding(encoding_name: str):
    if tiktoken is None:
        raise FilingChunkingError("tiktoken is required for filing chunk token counts.")

    return tiktoken.get_encoding(encoding_name)


def _merge_blocks(
    source_text: str,
    blocks: list[TextBlock],
    *,
    target_tokens: int,
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[ChunkCandidate]:
    chunks: list[ChunkCandidate] = []
    current_start: int | None = None
    current_end: int | None = None
    current_tokens = 0

    for block in blocks:
        if current_start is None:
            current_start = block.start_offset
            current_end = block.end_offset
            current_tokens = token_counter(block.text)
            continue

        candidate = _make_chunk_candidate(
            source_text,
            current_start,
            block.end_offset,
            token_counter=token_counter,
        )
        if candidate.token_count <= target_tokens or (
            current_tokens < max(1, target_tokens // 2)
            and candidate.token_count <= max_tokens
        ):
            current_end = block.end_offset
            current_tokens = candidate.token_count
            continue

        chunks.extend(
            _flush_chunk(
                source_text,
                current_start,
                current_end,
                token_counter=token_counter,
            ),
        )
        current_start = block.start_offset
        current_end = block.end_offset
        current_tokens = token_counter(block.text)

    chunks.extend(
        _flush_chunk(
            source_text,
            current_start,
            current_end,
            token_counter=token_counter,
        ),
    )
    return chunks


def _split_blocks_by_words_when_needed(
    source_text: str,
    blocks: list[TextBlock],
    *,
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[TextBlock]:
    split_blocks: list[TextBlock] = []
    for block in blocks:
        if token_counter(block.text) <= max_tokens:
            split_blocks.append(block)
            continue

        split_blocks.extend(
            _split_block_by_words(
                source_text,
                block,
                max_tokens=max_tokens,
                token_counter=token_counter,
            ),
        )

    return split_blocks


def _split_block_by_words(
    source_text: str,
    block: TextBlock,
    *,
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[TextBlock]:
    words = [
        TextBlock(
            text=match.group(0),
            start_offset=block.start_offset + match.start(),
            end_offset=block.start_offset + match.end(),
        )
        for match in _WORD_RE.finditer(block.text)
    ]
    if not words:
        return []

    chunks = _merge_blocks(
        source_text,
        words,
        target_tokens=max_tokens,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )
    return [
        TextBlock(
            text=chunk.chunk_text,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
        )
        for chunk in chunks
    ]


def _split_block_into_lines(block: TextBlock) -> list[TextBlock]:
    lines: list[TextBlock] = []
    offset = 0
    for line in block.text.splitlines(keepends=True):
        line_without_newline = line.rstrip("\n")
        if line_without_newline.strip():
            line_start = block.start_offset + offset
            line_end = line_start + len(line_without_newline)
            lines.append(
                TextBlock(
                    text=line_without_newline.strip(),
                    start_offset=line_start + len(line_without_newline) - len(line_without_newline.lstrip()),
                    end_offset=line_end - (len(line_without_newline) - len(line_without_newline.rstrip())),
                ),
            )
        offset += len(line)

    return lines or [block]


def _split_block_into_sentences(block: TextBlock) -> list[TextBlock]:
    sentences: list[TextBlock] = []
    for match in _SENTENCE_RE.finditer(block.text):
        sentence = match.group(0)
        if not sentence.strip():
            continue

        leading_spaces = len(sentence) - len(sentence.lstrip())
        trailing_spaces = len(sentence) - len(sentence.rstrip())
        start_offset = block.start_offset + match.start() + leading_spaces
        end_offset = block.start_offset + match.end() - trailing_spaces
        sentences.append(
            TextBlock(
                text=block.text[match.start() + leading_spaces : match.end() - trailing_spaces],
                start_offset=start_offset,
                end_offset=end_offset,
            ),
        )

    return sentences or [block]


def _flush_chunk(
    source_text: str,
    start_offset: int | None,
    end_offset: int | None,
    *,
    token_counter: Callable[[str], int],
) -> list[ChunkCandidate]:
    if start_offset is None or end_offset is None:
        return []

    candidate = _make_chunk_candidate(
        source_text,
        start_offset,
        end_offset,
        token_counter=token_counter,
    )
    if not candidate.chunk_text:
        return []

    return [candidate]


def _make_chunk_candidate(
    source_text: str,
    start_offset: int,
    end_offset: int,
    *,
    token_counter: Callable[[str], int],
) -> ChunkCandidate:
    raw_text = source_text[start_offset:end_offset]
    leading_spaces = len(raw_text) - len(raw_text.lstrip())
    trailing_spaces = len(raw_text) - len(raw_text.rstrip())
    normalized_start = start_offset + leading_spaces
    normalized_end = end_offset - trailing_spaces
    chunk_text = source_text[normalized_start:normalized_end]
    return ChunkCandidate(
        chunk_text=chunk_text,
        token_count=token_counter(chunk_text),
        start_offset=normalized_start,
        end_offset=normalized_end,
    )


def _make_text_block(source_text: str, start_offset: int, end_offset: int) -> TextBlock:
    raw_text = source_text[start_offset:end_offset]
    leading_spaces = len(raw_text) - len(raw_text.lstrip())
    trailing_spaces = len(raw_text) - len(raw_text.rstrip())
    normalized_start = start_offset + leading_spaces
    normalized_end = end_offset - trailing_spaces
    return TextBlock(
        text=source_text[normalized_start:normalized_end],
        start_offset=normalized_start,
        end_offset=normalized_end,
    )
