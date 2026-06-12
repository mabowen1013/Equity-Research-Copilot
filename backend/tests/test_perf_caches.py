from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import Settings
from app.services import embedding_provider as embedding_provider_module
from app.services import query_planner as query_planner_module
from app.services.embedding_provider import (
    OpenAIEmbeddingProvider,
    clear_query_embedding_cache,
)
from app.services.openai_client import get_openai_client
from app.services.query_planner import (
    LLMQueryPlanner,
    clear_llm_response_cache,
)


@pytest.fixture(autouse=True)
def reset_caches():
    clear_query_embedding_cache()
    clear_llm_response_cache()
    get_openai_client.cache_clear()
    yield
    clear_query_embedding_cache()
    clear_llm_response_cache()
    get_openai_client.cache_clear()


def build_settings(**overrides) -> Settings:
    return Settings(_env_file=None, openai_api_key="sk-test", **overrides)


def test_get_openai_client_reuses_client_for_same_configuration() -> None:
    first = get_openai_client("sk-test", timeout=10.0, max_retries=0)
    second = get_openai_client("sk-test", timeout=10.0, max_retries=0)
    other_timeout = get_openai_client("sk-test", timeout=20.0, max_retries=0)

    assert first is second
    assert first is not other_timeout


class FakeEmbeddingsClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, input: list[str], dimensions: int):
        self.calls.append(list(input))
        data = [
            SimpleNamespace(index=index, embedding=[float(len(text))] * dimensions)
            for index, text in enumerate(input)
        ]
        return SimpleNamespace(data=data)


def test_embed_texts_caches_repeated_query_texts(monkeypatch) -> None:
    fake_client = FakeEmbeddingsClient()
    monkeypatch.setattr(
        embedding_provider_module,
        "get_openai_client",
        lambda api_key: fake_client,
    )
    provider = OpenAIEmbeddingProvider(build_settings())

    first = provider.embed_texts(["revenue latest quarter"], model="m", dimensions=4)
    second = provider.embed_texts(["revenue latest quarter"], model="m", dimensions=4)

    assert first == second
    assert len(fake_client.calls) == 1


def test_embed_texts_only_requests_cache_misses(monkeypatch) -> None:
    fake_client = FakeEmbeddingsClient()
    monkeypatch.setattr(
        embedding_provider_module,
        "get_openai_client",
        lambda api_key: fake_client,
    )
    provider = OpenAIEmbeddingProvider(build_settings())

    provider.embed_texts(["query a"], model="m", dimensions=4)
    result = provider.embed_texts(["query a", "query b"], model="m", dimensions=4)

    assert len(result) == 2
    assert fake_client.calls == [["query a"], ["query b"]]


def test_embed_texts_does_not_cache_long_document_texts(monkeypatch) -> None:
    fake_client = FakeEmbeddingsClient()
    monkeypatch.setattr(
        embedding_provider_module,
        "get_openai_client",
        lambda api_key: fake_client,
    )
    provider = OpenAIEmbeddingProvider(build_settings())
    long_text = "x" * 2000

    provider.embed_texts([long_text], model="m", dimensions=4)
    provider.embed_texts([long_text], model="m", dimensions=4)

    assert len(fake_client.calls) == 2


class FakeChatClient:
    def __init__(self, content: str) -> None:
        self.call_count = 0
        self._content = content
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.call_count += 1
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_llm_planner_caches_plan_candidates(monkeypatch) -> None:
    fake_client = FakeChatClient('{"question_type": "metric"}')
    monkeypatch.setattr(
        query_planner_module,
        "get_openai_client",
        lambda api_key, *, timeout=None, max_retries=0: fake_client,
    )
    planner = LLMQueryPlanner(build_settings())

    first = planner.plan_candidate("What was Apple's revenue?")
    second = planner.plan_candidate("What was Apple's revenue?")
    planner.plan_candidate("What was Apple's gross margin?")

    assert first == second == {"question_type": "metric"}
    assert fake_client.call_count == 2
