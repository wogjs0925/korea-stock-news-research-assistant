from __future__ import annotations

import os
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.app_setting_service import get_secret_value
from app.utils.text import strip_html


class NewsProviderError(Exception):
    pass


class NewsProviderConfigurationError(NewsProviderError):
    pass


class NewsProviderHTTPError(NewsProviderError):
    def __init__(self, status_code: int, message: str | None = None):
        message = message or f"Naver HTTP error {status_code}"
        super().__init__(message)
        self.status_code = status_code


class NaverNewsProvider:
    name = "naver"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.settings = get_settings()
        if client_id is None:
            client_id = get_secret_value("NAVER_CLIENT_ID") or self.settings.naver_client_id or os.getenv("NAVER_CLIENT_ID")
        if client_secret is None:
            client_secret = get_secret_value("NAVER_CLIENT_SECRET") or self.settings.naver_client_secret or os.getenv("NAVER_CLIENT_SECRET")

        if not client_id or not client_secret:
            raise NewsProviderConfigurationError("Naver client id/secret not configured")

        self._client_id = client_id
        self._client_secret = client_secret
        self.base_url = "https://openapi.naver.com/v1/search/news.json"

    def _build_headers(self) -> dict[str, str]:
        return {
            "X-Naver-Client-Id": self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }

    def _format_http_error_message(self, status_code: int) -> str:
        if status_code in {401, 403}:
            return "Naver authentication failed"
        if status_code == 429:
            return "Naver rate limit exceeded"
        if 400 <= status_code < 500:
            return f"Naver client error {status_code}"
        return f"Naver service error {status_code}"

    def _parse_published_at(self, value: Any) -> str | None:
        if not value:
            return None
        if isinstance(value, str):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.isoformat()
            except Exception:
                return None
        return None

    async def search(self, query: str, display: int, sort: str) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "display": min(max(1, display), int(self.settings.news_max_display)),
            "start": 1,
        }
        if sort:
            params["sort"] = sort

        timeout = float(self.settings.news_api_timeout or 10.0)
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                resp = await client.get(self.base_url, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                raise NewsProviderHTTPError(408, "Naver request timed out") from exc
            except httpx.HTTPError as exc:
                raise NewsProviderError("Naver network request failed") from exc

        if resp.status_code >= 400:
            raise NewsProviderHTTPError(resp.status_code, self._format_http_error_message(resp.status_code))

        try:
            payload = resp.json()
        except ValueError as exc:
            raise NewsProviderHTTPError(resp.status_code, "invalid JSON response from Naver") from exc

        if not isinstance(payload, dict):
            raise NewsProviderError("invalid Naver response payload")

        items = payload.get("items")
        if items is None:
            return []
        if not isinstance(items, list):
            raise NewsProviderError("Naver response items is not a list")

        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            title = strip_html(str(item.get("title", "")))
            description = strip_html(str(item.get("description", ""))) if item.get("description") is not None else None
            published_at = self._parse_published_at(item.get("pubDate"))

            normalized_items.append(
                {
                    "title": title,
                    "description": description,
                    "link": item.get("link"),
                    "original_link": item.get("originallink"),
                    "published_at": published_at,
                    "publisher": None,
                    "raw_data": item,
                }
            )

        return normalized_items
