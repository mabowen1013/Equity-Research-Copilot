from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.models import SecResponseCache


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_sec_cache_key(url: str) -> str:
    digest = sha256(url.strip().encode("utf-8")).hexdigest()
    return f"sec:{digest}"


@dataclass(frozen=True)
class SecCacheResult:
    response_json: dict[str, Any]
    cache_hit: bool
    record: SecResponseCache


class SecResponseCacheService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        ttl_seconds: int | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        active_settings = settings or get_settings()
        self._db = db
        self._ttl_seconds = ttl_seconds or active_settings.sec_cache_ttl_seconds
        self._clock = clock

    def get_json(
        self,
        cache_key: str,
        *,
        refresh: bool = False,
    ) -> SecCacheResult | None:
        if refresh:
            return None

        record = self._get_record(cache_key)
        if record is None or self._is_expired(record):
            return None

        return SecCacheResult(
            response_json=record.response_json,
            cache_hit=True,
            record=record,
        )

    def store_json(
        self,
        *,
        cache_key: str,
        url: str,
        response_json: dict[str, Any],
        status_code: int = 200,
    ) -> SecResponseCache:
        now = self._clock()
        record = self._get_record(cache_key)

        if record is None:
            record = SecResponseCache(cache_key=cache_key)
            self._db.add(record)

        record.url = url
        record.response_json = response_json
        record.status_code = status_code
        record.fetched_at = now
        record.expires_at = now + timedelta(seconds=self._ttl_seconds)
        record.updated_at = now

        self._db.flush()
        return record

    def get_or_fetch_json(
        self,
        *,
        cache_key: str,
        url: str,
        fetch_json: Callable[[str], dict[str, Any]],
        refresh: bool = False,
    ) -> SecCacheResult:
        cached_result = self.get_json(cache_key, refresh=refresh)
        if cached_result is not None:
            return cached_result

        response_json = fetch_json(url)
        record = self.store_json(
            cache_key=cache_key,
            url=url,
            response_json=response_json,
        )

        return SecCacheResult(
            response_json=response_json,
            cache_hit=False,
            record=record,
        )

    def _get_record(self, cache_key: str) -> SecResponseCache | None:
        statement = select(SecResponseCache).where(SecResponseCache.cache_key == cache_key)
        return self._db.scalar(statement)

    def _is_expired(self, record: SecResponseCache) -> bool:
        now = self._clock()
        expires_at = record.expires_at

        if expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)

        return expires_at <= now
