from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MarketAnalysisRunRequest(BaseModel):
    analysis_window_hours: int = Field(24, ge=1, le=720)
    max_news_analysis_count: int = Field(20, ge=1, le=200)
    max_theme_source_count: int = Field(50, ge=3, le=300)
    force_reanalyze: bool = False
    run_candidate_generation: bool = True
    include_weak_industry_candidates: bool = False
    include_watchlist_themes: bool = False
    include_leveraged_inverse_etfs: bool = True
    max_stock_candidates_per_theme: int = Field(15, ge=1, le=100)
    max_etf_candidates_per_theme: int = Field(20, ge=1, le=100)
    run_recommendations: bool = True
    max_stocks_per_theme: int = Field(3, ge=1, le=10)
    max_etfs_per_theme: int = Field(2, ge=1, le=10)
    diversify_country: bool = True


class MarketAnalysisRunResponse(BaseModel):
    run_id: str
    status: str
    failed_stage: str | None = None
    news_selection: dict[str, Any] | None = None
    news_analysis: dict[str, Any] | None = None
    theme_analysis: dict[str, Any] | None = None
    candidate_generation: dict[str, Any] | None = None
    recommendations: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
