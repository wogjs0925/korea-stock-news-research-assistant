from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

from app.schemas.news_analysis import NewsAnalysisOutput


class NewsAnalyzerProvider(ABC):
    @abstractmethod
    async def analyze(self, article_input: dict[str, Any]) -> tuple[NewsAnalysisOutput, dict]:
        ...
