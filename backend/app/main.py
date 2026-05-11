from fastapi import FastAPI

from app.api import api_router
from app.core import add_request_logging, configure_logging, get_settings
from app.db import get_engine


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Equity Research Copilot API")
    app.state.settings = get_settings()
    app.state.db_engine = get_engine()
    add_request_logging(app)
    app.include_router(api_router)
    return app


app = create_app()
