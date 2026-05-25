from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """Small pgvector SQLAlchemy type used without requiring pgvector at import time."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dimensions})"

    def bind_processor(self, dialect: Any):
        def process(value: Sequence[float] | str | None) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return "[" + ",".join(str(float(item)) for item in value) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any):
        def process(value: Any) -> list[float] | None:
            if value is None:
                return None
            if isinstance(value, list):
                return [float(item) for item in value]
            text_value = str(value).strip()
            if text_value.startswith("[") and text_value.endswith("]"):
                text_value = text_value[1:-1]
            if not text_value:
                return []
            return [float(item) for item in text_value.split(",")]

        return process
