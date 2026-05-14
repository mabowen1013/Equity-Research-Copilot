from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Company, Filing, FilingDocument
from app.services import (
    FilingDocumentDownloadError,
    FilingDocumentService,
    SecContentResponse,
)


class FakeSecClient:
    def __init__(self, responses: list[SecContentResponse | Exception]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get_content(self, url: str) -> SecContentResponse:
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response

        return response


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_now_function(dbapi_connection, connection_record) -> None:
        dbapi_connection.create_function("now", 0, lambda: "2026-05-14 00:00:00")

    Company.__table__.create(engine)
    Filing.__table__.create(engine)
    FilingDocument.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_filing(
    db_session: Session,
    *,
    primary_document_url: str | None = "https://www.sec.gov/Archives/aapl-20240928.htm",
) -> Filing:
    company = Company(
        ticker="AAPL",
        cik="0000320193",
        name="Apple Inc.",
    )
    db_session.add(company)
    db_session.flush()

    filing = Filing(
        company_id=company.id,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_document="aapl-20240928.htm",
        sec_filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123",
        sec_primary_document_url=primary_document_url,
    )
    db_session.add(filing)
    db_session.flush()
    return filing


def make_response(content: bytes, *, url: str | None = None) -> SecContentResponse:
    return SecContentResponse(
        content=content,
        content_type="text/html; charset=utf-8",
        url=url or "https://www.sec.gov/Archives/aapl-20240928.htm",
    )


def test_filing_document_service_downloads_and_stores_metadata(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    content = b"<html><body>Apple 10-K</body></html>"
    sec_client = FakeSecClient([make_response(content)])
    now = datetime(2026, 5, 14, tzinfo=UTC)

    result = FilingDocumentService(
        db_session,
        sec_client=sec_client,
        cache_dir=tmp_path,
        clock=lambda: now,
    ).get_or_download_primary_document(filing)

    assert result.cache_hit is False
    assert result.document.status == "downloaded"
    assert result.document.source_url == "https://www.sec.gov/Archives/aapl-20240928.htm"
    assert result.document.content_sha256 == sha256(content).hexdigest()
    assert result.document.content_type == "text/html; charset=utf-8"
    assert result.document.byte_size == len(content)
    assert result.document.downloaded_at == now
    assert result.document.error_message is None
    assert sec_client.urls == ["https://www.sec.gov/Archives/aapl-20240928.htm"]
    assert result.document.cache_path is not None
    assert (tmp_path / "0000320193-24-000123" / "aapl-20240928.htm").read_bytes() == content


def test_filing_document_service_uses_existing_cache(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    content = b"<html><body>cached filing</body></html>"
    sec_client = FakeSecClient([make_response(content)])
    service = FilingDocumentService(
        db_session,
        sec_client=sec_client,
        cache_dir=tmp_path,
    )

    first_result = service.get_or_download_primary_document(filing)
    second_result = service.get_or_download_primary_document(filing)

    assert first_result.cache_hit is False
    assert second_result.cache_hit is True
    assert second_result.document.id == first_result.document.id
    assert sec_client.urls == ["https://www.sec.gov/Archives/aapl-20240928.htm"]


def test_filing_document_service_refresh_redownloads_cache(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    first_content = b"<html><body>old filing</body></html>"
    second_content = b"<html><body>new filing</body></html>"
    sec_client = FakeSecClient(
        [
            make_response(first_content),
            make_response(second_content),
        ],
    )
    service = FilingDocumentService(
        db_session,
        sec_client=sec_client,
        cache_dir=tmp_path,
    )

    service.get_or_download_primary_document(filing)
    result = service.get_or_download_primary_document(filing, refresh=True)

    assert result.cache_hit is False
    assert result.document.content_sha256 == sha256(second_content).hexdigest()
    assert result.document.cache_path is not None
    assert sec_client.urls == [
        "https://www.sec.gov/Archives/aapl-20240928.htm",
        "https://www.sec.gov/Archives/aapl-20240928.htm",
    ]
    assert (tmp_path / "0000320193-24-000123" / "aapl-20240928.htm").read_bytes() == second_content


def test_filing_document_service_marks_missing_primary_url_failed(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session, primary_document_url=None)
    sec_client = FakeSecClient([])

    with pytest.raises(
        FilingDocumentDownloadError,
        match="primary document URL",
    ):
        FilingDocumentService(
            db_session,
            sec_client=sec_client,
            cache_dir=tmp_path,
        ).get_or_download_primary_document(filing)

    document = db_session.query(FilingDocument).one()

    assert document.status == "failed"
    assert document.source_url == filing.sec_filing_url
    assert document.error_message == "Filing does not have a SEC primary document URL."
    assert sec_client.urls == []


def test_filing_document_service_marks_download_failures_failed(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    sec_client = FakeSecClient([RuntimeError("network down")])

    with pytest.raises(FilingDocumentDownloadError, match="network down"):
        FilingDocumentService(
            db_session,
            sec_client=sec_client,
            cache_dir=tmp_path,
        ).get_or_download_primary_document(filing)

    document = db_session.query(FilingDocument).one()

    assert document.status == "failed"
    assert document.source_url == "https://www.sec.gov/Archives/aapl-20240928.htm"
    assert "network down" in str(document.error_message)
    assert document.cache_path is None
