from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.core import Settings, get_settings


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider cannot produce vectors."""


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
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingProviderError(
                "The openai package must be installed to generate embeddings."
            ) from exc

        client = OpenAI(api_key=api_key.get_secret_value())
        response = client.embeddings.create(
            model=model,
            input=list(texts),
            dimensions=dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    active_settings = settings or get_settings()
    provider_name = active_settings.embedding_provider.strip().lower()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider(active_settings)
    raise EmbeddingProviderError(f"Unsupported embedding provider: {active_settings.embedding_provider}")
