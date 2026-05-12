from app.db.base import Base
from app.models import Company, Filing, SecResponseCache
from app.schemas import CompanyRead, CompanySearchResult, FilingRead, SecResponseCacheRead


def test_companies_table_contains_sec_metadata_columns() -> None:
    columns = Company.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "ticker",
        "cik",
        "name",
        "exchange",
        "sic",
        "sic_description",
        "created_at",
        "updated_at",
    }


def test_companies_table_defines_ticker_and_cik_uniqueness() -> None:
    constraint_names = {
        constraint.name
        for constraint in Company.__table__.constraints
        if constraint.name is not None
    }

    assert "uq_companies_ticker" in constraint_names
    assert "uq_companies_cik" in constraint_names


def test_filings_table_contains_sec_metadata_columns() -> None:
    columns = Filing.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "company_id",
        "accession_number",
        "form_type",
        "filing_date",
        "report_date",
        "primary_document",
        "sec_filing_url",
        "sec_primary_document_url",
        "created_at",
        "updated_at",
    }


def test_filings_table_links_to_companies_and_deduplicates_accessions() -> None:
    foreign_keys = {foreign_key.target_fullname for foreign_key in Filing.company_id.foreign_keys}
    constraint_names = {
        constraint.name
        for constraint in Filing.__table__.constraints
        if constraint.name is not None
    }

    assert foreign_keys == {"companies.id"}
    assert "uq_filings_accession_number" in constraint_names


def test_sec_response_cache_table_contains_cache_metadata_columns() -> None:
    columns = SecResponseCache.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "cache_key",
        "url",
        "response_json",
        "status_code",
        "fetched_at",
        "expires_at",
        "created_at",
        "updated_at",
    }


def test_sec_response_cache_table_defines_status_code_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in SecResponseCache.__table__.constraints
        if constraint.name is not None
    }

    assert "ck_sec_response_cache_status_code_range" in constraint_names


def test_sec_ingestion_tables_are_registered_for_migrations() -> None:
    assert "companies" in Base.metadata.tables
    assert "filings" in Base.metadata.tables
    assert "sec_response_cache" in Base.metadata.tables


def test_sec_ingestion_schemas_allow_orm_serialization() -> None:
    assert CompanyRead.model_config["from_attributes"] is True
    assert CompanySearchResult.model_config["from_attributes"] is True
    assert FilingRead.model_config["from_attributes"] is True
    assert SecResponseCacheRead.model_config["from_attributes"] is True
