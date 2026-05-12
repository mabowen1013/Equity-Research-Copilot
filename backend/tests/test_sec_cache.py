from datetime import UTC, datetime, timedelta
from typing import Any

from app.models import SecResponseCache
from app.services import SecResponseCacheService, build_sec_cache_key

NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self, record: SecResponseCache | None = None) -> None:
        self.record = record
        self.added: list[SecResponseCache] = []
        self.flush_calls = 0
        self.scalar_calls = 0

    def scalar(self, statement) -> SecResponseCache | None:
        self.scalar_calls += 1
        return self.record

    def add(self, record: SecResponseCache) -> None:
        self.added.append(record)
        self.record = record

    def flush(self) -> None:
        self.flush_calls += 1


def make_record(
    *,
    cache_key: str = "sec:test",
    url: str = "https://data.sec.gov/example.json",
    response_json: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    status_code: int = 200,
) -> SecResponseCache:
    return SecResponseCache(
        cache_key=cache_key,
        url=url,
        response_json=response_json or {"cached": True},
        status_code=status_code,
        fetched_at=NOW - timedelta(minutes=5),
        expires_at=expires_at or NOW + timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=5),
    )


def make_service(session: FakeSession, *, ttl_seconds: int = 60) -> SecResponseCacheService:
    return SecResponseCacheService(
        session,
        ttl_seconds=ttl_seconds,
        clock=lambda: NOW,
    )


def test_build_sec_cache_key_is_stable_and_bounded() -> None:
    url = "https://data.sec.gov/submissions/CIK0000320193.json"

    assert build_sec_cache_key(url) == build_sec_cache_key(url)
    assert build_sec_cache_key(url).startswith("sec:")
    assert len(build_sec_cache_key(url)) == 68


def test_get_json_returns_cached_payload_when_unexpired() -> None:
    record = make_record()
    service = make_service(FakeSession(record))

    result = service.get_json("sec:test")

    assert result is not None
    assert result.cache_hit is True
    assert result.response_json == {"cached": True}
    assert result.record is record


def test_get_json_returns_none_for_cache_miss() -> None:
    service = make_service(FakeSession())

    assert service.get_json("sec:missing") is None


def test_get_json_returns_none_when_record_is_expired() -> None:
    record = make_record(expires_at=NOW - timedelta(seconds=1))
    service = make_service(FakeSession(record))

    assert service.get_json("sec:test") is None


def test_get_json_returns_none_when_refresh_is_requested() -> None:
    record = make_record()
    session = FakeSession(record)
    service = make_service(session)

    assert service.get_json("sec:test", refresh=True) is None
    assert session.scalar_calls == 0


def test_store_json_inserts_new_cache_record() -> None:
    session = FakeSession()
    service = make_service(session, ttl_seconds=120)

    record = service.store_json(
        cache_key="sec:new",
        url="https://data.sec.gov/new.json",
        response_json={"fresh": True},
    )

    assert session.added == [record]
    assert session.flush_calls == 1
    assert record.cache_key == "sec:new"
    assert record.url == "https://data.sec.gov/new.json"
    assert record.response_json == {"fresh": True}
    assert record.status_code == 200
    assert record.fetched_at == NOW
    assert record.expires_at == NOW + timedelta(seconds=120)
    assert record.updated_at == NOW


def test_store_json_updates_existing_cache_record() -> None:
    record = make_record(response_json={"old": True}, expires_at=NOW - timedelta(hours=1))
    session = FakeSession(record)
    service = make_service(session, ttl_seconds=30)

    updated = service.store_json(
        cache_key="sec:test",
        url="https://data.sec.gov/updated.json",
        response_json={"updated": True},
        status_code=203,
    )

    assert updated is record
    assert session.added == []
    assert session.flush_calls == 1
    assert record.url == "https://data.sec.gov/updated.json"
    assert record.response_json == {"updated": True}
    assert record.status_code == 203
    assert record.fetched_at == NOW
    assert record.expires_at == NOW + timedelta(seconds=30)


def test_get_or_fetch_json_uses_cache_without_fetching() -> None:
    record = make_record()
    session = FakeSession(record)
    service = make_service(session)
    fetch_calls: list[str] = []

    result = service.get_or_fetch_json(
        cache_key="sec:test",
        url=record.url,
        fetch_json=lambda url: fetch_calls.append(url) or {"fresh": True},
    )

    assert result.cache_hit is True
    assert result.response_json == {"cached": True}
    assert fetch_calls == []
    assert session.flush_calls == 0


def test_get_or_fetch_json_fetches_and_stores_on_miss() -> None:
    session = FakeSession()
    service = make_service(session)

    result = service.get_or_fetch_json(
        cache_key="sec:new",
        url="https://data.sec.gov/new.json",
        fetch_json=lambda url: {"url": url},
    )

    assert result.cache_hit is False
    assert result.response_json == {"url": "https://data.sec.gov/new.json"}
    assert result.record is session.record
    assert session.flush_calls == 1


def test_get_or_fetch_json_refresh_bypasses_existing_cache() -> None:
    record = make_record(response_json={"old": True})
    session = FakeSession(record)
    service = make_service(session)

    result = service.get_or_fetch_json(
        cache_key="sec:test",
        url="https://data.sec.gov/refreshed.json",
        fetch_json=lambda url: {"refreshed": url},
        refresh=True,
    )

    assert result.cache_hit is False
    assert result.response_json == {"refreshed": "https://data.sec.gov/refreshed.json"}
    assert record.response_json == {"refreshed": "https://data.sec.gov/refreshed.json"}
    assert record.url == "https://data.sec.gov/refreshed.json"
    assert session.flush_calls == 1
