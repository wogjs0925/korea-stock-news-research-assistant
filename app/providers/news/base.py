from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    async def search(self, query: str, display: int, sort: str) -> list[dict]:
        ...
