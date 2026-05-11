from fastapi import FastAPI

from app.main import app


def test_app_is_fastapi_instance() -> None:
    assert isinstance(app, FastAPI)
