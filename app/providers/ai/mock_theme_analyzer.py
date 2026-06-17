from __future__ import annotations

from datetime import datetime
from typing import Any

from app.providers.ai.theme_analyzer_base import ThemeAnalyzerProvider
from app.schemas.theme_analysis import SelectedThemeCandidate, ThemeEvidence, ThemeSelectionOutput


class MockThemeAnalyzer(ThemeAnalyzerProvider):
    async def analyze(
        self,
        sources: list[dict[str, Any]],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[ThemeSelectionOutput, dict[str, Any]]:
        themes: list[SelectedThemeCandidate] = []
        if len(sources) >= 2:
            evidence = [
                ThemeEvidence(
                    news_analysis_id=int(source["news_analysis_id"]),
                    relevance_score=0.8,
                    reason="최근 뉴스 분석 결과와 테마 관련성이 확인되었습니다.",
                )
                for source in sources[: min(3, len(sources))]
            ]
            themes.append(
                SelectedThemeCandidate(
                    theme_name="개발 검증용 시장 테마",
                    theme_summary="최근 AI 뉴스 분석 결과가 공통된 시장 테마를 가리킵니다.",
                    why_now="여러 뉴스 분석에서 관련 투자 관심도가 함께 확인되었습니다.",
                    impact_direction="mixed",
                    confidence_score=0.6,
                    time_horizon="short_term",
                    related_industries=["기술"],
                    related_companies=[
                        company
                        for source in sources
                        for company in source.get("companies", [])
                    ][:15],
                    evidence=evidence,
                    risk_factors=["개발 검증용 결과이므로 실제 판단 전 추가 확인이 필요합니다."],
                )
            )

        return (
            ThemeSelectionOutput(
                market_overview="최근 분석된 시장 뉴스에 대한 개발 검증용 개요입니다.",
                themes=themes[:3],
                insufficient_data_reason=None if themes else "테마를 선정할 만큼 분석된 뉴스가 부족합니다.",
            ),
            {"tokens": {"input": 0, "output": 0, "total": 0}, "latency_ms": 0, "response_id": "mock"},
        )
