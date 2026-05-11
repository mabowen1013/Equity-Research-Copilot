from collections.abc import Generator

import pytest
from sqlalchemy import Engine

import app.db.session as db_session_module
from app.db import get_engine, get_sessionmaker


def test_database_engine_uses_configured_postgresql_url() -> None:
    engine = get_engine()

    assert isinstance(engine, Engine)
    assert engine.url.drivername == "postgresql+psycopg"


def test_database_session_dependency_closes_session(monkeypatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(
        db_session_module,
        "get_sessionmaker",
        lambda: lambda: fake_session,
    )

    dependency = db_session_module.get_db_session()

    assert isinstance(dependency, Generator)
    assert next(dependency) is fake_session

    with pytest.raises(StopIteration):
        next(dependency)

    assert fake_session.closed


def test_database_sessionmaker_is_cached() -> None:
    assert get_sessionmaker() is get_sessionmaker()
