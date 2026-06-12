from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from threading import Lock
from typing import Protocol

from app.core import Settings, get_settings
from app.services.openai_client import get_openai_client

QUERY_EMBEDDING_CACHE_MAX_ENTRIES = 512
QUERY_EMBEDDING_CACHE_MAX_TEXT_CHARS = 512

_query_embedding_cache: OrderedDict[tuple[str, int, str], list[float]] = OrderedDict()
_query_embedding_cache_lock = Lock()


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider cannot produce vectors."""


def _cache_key(model: str, dimensions: int, text: str) -> tuple[str, int, str]:
    return (model, dimensions, text)


def _cacheable(text: str) -> bool:
    return len(text) <= QUERY_EMBEDDING_CACHE_MAX_TEXT_CHARS


def _cache_get(key: tuple[str, int, str]) -> list[float] | None:
    with _query_embedding_cache_lock:
        vector = _query_embedding_cache.get(key)
        if vector is not None:
            _query_embedding_cache.move_to_end(key)
        return vector


def _cache_put(key: tuple[str, int, str], vector: list[float]) -> None:
    with _query_embedding_cache_lock:
        _query_embedding_cache[key] = vector
        _query_embedding_cache.move_to_end(key)
        while len(_query_embedding_cache) > QUERY_EMBEDDING_CACHE_MAX_ENTRIES:
            _query_embedding_cache.popitem(last=False)


def clear_query_embedding_cache() -> None:
    with _query_embedding_cache_lock:
        _query_embedding_cache.clear()


class EmbeddingProvider(Protocol):
    provider_name: str

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        model: str,
        dimensions: int,
    ) -> list[list[float]]:
        """Embed texts in the same order they are provided."""


class OpenAIEmbeddingProvider:
    provider_name = "openai"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        model: str,
        dimensions: int,
    ) -> list[list[float]]:
        if not texts:
            return []

        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise EmbeddingProviderError("OPENAI_API_KEY must be configured to generate embeddings.")

        try:
            client = get_openai_client(api_key.get_secret_value())
        except ImportError as exc:
            raise EmbeddingProviderError(
                "The openai package must be installed to generate embeddings."
            ) from exc

        results: dict[int, list[float]] = {}
        miss_indexes: list[int] = []
        for index, text in enumerate(texts):
            cached = (
                _cache_get(_cache_key(model, dimensions, text))
                if _cacheable(text)
                else None
            )
            if cached is not None:
                results[index] = cached
            else:
                miss_indexes.append(index)

        if miss_indexes:
            response = client.embeddings.create(
                model=model,
                input=[texts[index] for index in miss_indexes],
                dimensions=dimensions,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(miss_indexes):
                raise EmbeddingProviderError(
                    "Embedding provider returned an unexpected number of vectors."
                )
            for miss_position, item in zip(miss_indexes, ordered, strict=True):
                vector = list(item.embedding)
                results[miss_position] = vector
                text = texts[miss_position]
                if _cacheable(text):
                    _cache_put(_cache_key(model, dimensions, text), vector)

        return [results[index] for index in range(len(texts))]


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    active_settings = settings or get_settings()
    provider_name = active_settings.embedding_provider.strip().lower()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider(active_settings)
    raise EmbeddingProviderError(f"Unsupported embedding provider: {active_settings.embedding_provider}")
