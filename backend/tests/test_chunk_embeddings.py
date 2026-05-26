from datetime import UTC, datetime

import pytest

from app.core import Settings
from app.models import ChunkEmbedding, Company, DocumentChunk
from app.services import ChunkEmbeddingError, ChunkEmbeddingService, build_chunk_embedding_input

NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


class FakeProvider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts, *, model: str, dimensions: int):
        self.calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeScalarResult:
    def __init__(self, items: list) -> None:
        self.items = items

    def all(self) -> list:
        return self.items


class FakeSession:
    def __init__(self, *, chunks: list[DocumentChunk], embeddings: list[ChunkEmbedding]) -> None:
        self.chunks = chunks
        self.embeddings = embeddings
        self.added: list[ChunkEmbedding] = []
        self.flush_calls = 0

    def scalars(self, statement) -> FakeScalarResult:
        statement_text = str(statement)
        if "chunk_embeddings" in statement_text:
            return FakeScalarResult(self.embeddings)
        return FakeScalarResult(self.chunks)

    def add(self, instance) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        self.flush_calls += 1


def make_company() -> Company:
    return Company(id=1, ticker="AAPL", cik="0000320193", name="Apple Inc.")


def make_chunk(*, chunk_id: int = 10, text: str = "Revenue increased.") -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        filing_id=20,
        section_id=30,
        chunk_index=0,
        chunk_text=text,
        token_count=3,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        section_label="PART II - ITEM 7 - Management's Discussion and Analysis",
        sec_url="https://www.sec.gov/Archives/aapl.htm",
        start_page=1,
        end_page=1,
        start_display_page=None,
        end_display_page=None,
        element_ids=[],
        xbrl_tags=[],
        source_start_offset=None,
        source_end_offset=None,
        has_table=False,
        created_at=NOW,
        updated_at=NOW,
    )


def make_settings(*, input_version: str = "v1") -> Settings:
    return Settings(
        _env_file=None,
        embedding_provider="fake",
        embedding_model="test-embedding",
        embedding_dimensions=3,
        embedding_input_version=input_version,
    )


def test_build_chunk_embedding_input_includes_metadata() -> None:
    text = build_chunk_embedding_input(make_company(), make_chunk())

    assert "Company: AAPL - Apple Inc." in text
    assert "Form: 10-K" in text
    assert "Section: PART II - ITEM 7" in text
    assert "Revenue increased." in text


def test_embedding_generation_skips_unchanged_chunks() -> None:
    company = make_company()
    chunk = make_chunk()
    unchanged_hash = __import__("hashlib").sha256(
        build_chunk_embedding_input(company, chunk).encode("utf-8")
    ).hexdigest()
    existing = ChunkEmbedding(
        id=1,
        chunk_id=chunk.id,
        company_id=company.id,
        filing_id=chunk.filing_id,
        provider="fake",
        model="test-embedding",
        dimensions=3,
        embedding_input_version="v1",
        content_sha256=unchanged_hash,
        embedding=[0.1, 0.2, 0.3],
        created_at=NOW,
        updated_at=NOW,
    )
    provider = FakeProvider()
    service = ChunkEmbeddingService(
        FakeSession(chunks=[chunk], embeddings=[existing]),
        settings=make_settings(),
        provider=provider,
        clock=lambda: NOW,
    )

    result = service.generate_company_embeddings(company)

    assert result.skipped_count == 1
    assert result.embedded_count == 0
    assert result.stale_updated_count == 0
    assert provider.calls == []


def test_embedding_generation_requires_parsed_chunks() -> None:
    service = ChunkEmbeddingService(
        FakeSession(chunks=[], embeddings=[]),
        settings=make_settings(),
        provider=FakeProvider(),
        clock=lambda: NOW,
    )

    with pytest.raises(ChunkEmbeddingError, match="No parsed document chunks"):
        service.generate_company_embeddings(make_company())


def test_embedding_generation_updates_stale_embedding() -> None:
    company = make_company()
    chunk = make_chunk()
    existing = ChunkEmbedding(
        id=1,
        chunk_id=chunk.id,
        company_id=company.id,
        filing_id=chunk.filing_id,
        provider="fake",
        model="test-embedding",
        dimensions=3,
        embedding_input_version="v1",
        content_sha256="stale",
        embedding=[0.0, 0.0, 0.0],
        created_at=NOW,
        updated_at=NOW,
    )
    service = ChunkEmbeddingService(
        FakeSession(chunks=[chunk], embeddings=[existing]),
        settings=make_settings(),
        provider=FakeProvider(),
        clock=lambda: NOW,
    )

    result = service.generate_company_embeddings(company)

    assert result.stale_updated_count == 1
    assert existing.content_sha256 != "stale"
    assert existing.embedding == [0.1, 0.2, 0.3]


def test_embedding_generation_creates_new_row_for_new_input_version() -> None:
    company = make_company()
    chunk = make_chunk()
    session = FakeSession(chunks=[chunk], embeddings=[])
    service = ChunkEmbeddingService(
        session,
        settings=make_settings(input_version="v2"),
        provider=FakeProvider(),
        clock=lambda: NOW,
    )

    result = service.generate_company_embeddings(company)

    assert result.embedded_count == 1
    assert session.added[0].embedding_input_version == "v2"
