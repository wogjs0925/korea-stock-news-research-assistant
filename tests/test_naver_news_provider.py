import os
import asyncio

import httpx
import pytest

from app.providers.news.naver import (
    NaverNewsProvider,
    NewsProviderConfigurationError,
    NewsProviderError,
    NewsProviderHTTPError,
)


class DummyResponse:
    def __init__(self, status_code: int, json_data=None, reason_phrase: str | None = None):
        self.status_code = status_code
        self._json_data = json_data
        self.reason_phrase = reason_phrase or ""

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class DummyClient:
    def __init__(self, response: DummyResponse, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def test_naver_provider_requires_config(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    with pytest.raises(NewsProviderConfigurationError):
        NaverNewsProvider()


def test_naver_provider_search_strips_html_and_parses_date(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    fake_items = [
        {
            "title": "<b>뉴스</b>",
            "description": "<p>설명</p>",
            "link": "https://news.example.com/article",
            "originallink": "https://origin.example.com/article",
            "pubDate": "Tue, 02 May 2023 12:34:56 +0900",
        }
    ]
    response = DummyResponse(status_code=200, json_data={"items": fake_items})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(response))

    provider = NaverNewsProvider()
    items = asyncio.run(provider.search("query", 5, "date"))

    assert len(items) == 1
    assert items[0]["title"] == "뉴스"
    assert items[0]["description"] == "설명"
    assert items[0]["link"] == "https://news.example.com/article"
    assert items[0]["original_link"] == "https://origin.example.com/article"
    assert items[0]["published_at"].endswith("+09:00")
    assert items[0]["raw_data"] == fake_items[0]


@pytest.mark.parametrize("status_code,expected_message", [
    (401, "Naver authentication failed"),
    (403, "Naver authentication failed"),
    (429, "Naver rate limit exceeded"),
    (404, "Naver client error 404"),
    (500, "Naver service error 500"),
])
def test_naver_provider_http_error_messages(monkeypatch, status_code, expected_message):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    response = DummyResponse(status_code=status_code, json_data={})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(response))

    provider = NaverNewsProvider()
    with pytest.raises(NewsProviderHTTPError) as exc_info:
        asyncio.run(provider.search("query", 1, "date"))
    assert expected_message in str(exc_info.value)
    assert "secret" not in str(exc_info.value).lower()
    assert "id" not in str(exc_info.value).lower()


def test_naver_provider_timeout(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyClient(
            DummyResponse(200, json_data={}), raise_exc=httpx.TimeoutException("timeout")
        ),
    )

    provider = NaverNewsProvider()
    with pytest.raises(NewsProviderHTTPError) as exc_info:
        asyncio.run(provider.search("query", 1, "date"))
    assert "timed out" in str(exc_info.value).lower()


def test_naver_provider_invalid_json(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    response = DummyResponse(status_code=200, json_data=ValueError("Bad JSON"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(response))

    provider = NaverNewsProvider()
    with pytest.raises(NewsProviderHTTPError) as exc_info:
        asyncio.run(provider.search("query", 1, "date"))
    assert "invalid json" in str(exc_info.value).lower()


def test_naver_provider_items_type_error(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    response = DummyResponse(status_code=200, json_data={"items": "not-a-list"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(response))

    provider = NaverNewsProvider()
    with pytest.raises(NewsProviderError) as exc_info:
        asyncio.run(provider.search("query", 1, "date"))
    assert "items is not a list" in str(exc_info.value)


def test_naver_provider_does_not_expose_secrets_in_errors(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    response = DummyResponse(status_code=401, json_data={"message": "unauthorized"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient(response))

    provider = NaverNewsProvider()
    with pytest.raises(NewsProviderError) as exc_info:
        asyncio.run(provider.search("query", 1, "date"))
    assert "id" not in str(exc_info.value).lower()
    assert "secret" not in str(exc_info.value).lower()
