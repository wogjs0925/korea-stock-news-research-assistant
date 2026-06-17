from __future__ import annotations
from typing import Any
from app.providers.ai.base import NewsAnalyzerProvider
from app.schemas.news_analysis import NewsAnalysisOutput, CompanyMention


class MockNewsAnalyzer(NewsAnalyzerProvider):
    async def analyze(self, article_input: dict[str, Any]) -> tuple[NewsAnalysisOutput, dict]:
        title = article_input.get("title", "")
        summary = f"Mock analysis for: {title[:80]}"
        out = NewsAnalysisOutput(
            summary=summary,
            event_type="other",
            impact_direction="neutral",
            sentiment_score=0.0,
            importance_score=0.1,
            novelty_score=0.0,
            market_relevance_score=0.0,
            confidence_score=0.5,
            time_horizon="unknown",
            candidate_themes=[],
            companies=[],
            evidence_points=[],
            risk_factors=[],
            is_investment_relevant=False,
        )
        meta = {"model": "mock", "tokens": {"input": 0, "output": 0, "total": 0}}
        return out, meta
