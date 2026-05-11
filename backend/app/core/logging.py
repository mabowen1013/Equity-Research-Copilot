import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEFAULT_LOG_LEVEL = logging.INFO

request_logger = logging.getLogger("app.http")


def configure_logging(level: int = DEFAULT_LOG_LEVEL) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger("app").setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)


def add_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            request_logger.exception(
                "Unhandled request error method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        request_logger.info(
            "HTTP request method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
