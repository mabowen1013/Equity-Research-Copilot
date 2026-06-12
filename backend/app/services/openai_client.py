from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


@lru_cache(maxsize=8)
def get_openai_client(
    api_key: str,
    *,
    timeout: float | None = None,
    max_retries: int = 2,
) -> "OpenAI":
    """Return a shared OpenAI client so HTTP connections are reused across calls.

    Clients are cached per (api_key, timeout, max_retries) combination because the
    planner, dense rewriter, answer generator, and embedding provider use different
    timeout budgets.
    """
    from openai import OpenAI

    return OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
