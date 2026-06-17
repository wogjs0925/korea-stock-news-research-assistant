from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from app.schemas.theme_analysis import ThemeSelectionOutput


class ThemeAnalyzerProvider(ABC):
    @abstractmethod
    async def analyze(
        self,
        sources: list[dict[str, Any]],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[ThemeSelectionOutput, dict[str, Any]]:
        ...
