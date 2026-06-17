from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RecommendationRunRequest(BaseModel):
    theme_run_id: int | None = None
    max_stocks_per_theme: int = Field(3, ge=1, le=10)
    max_etfs_per_theme: int = Field(2, ge=1, le=10)
    include_leveraged_inverse_etfs: bool = False
    min_candidate_score: float = Field(0.35, ge=0.0, le=1.0)
    min_evidence_score: float = Field(0.1, ge=0.0, le=1.0)
    diversify_country: bool = True
    stock_country_scope: str | None = None


class RecommendationRunResponse(BaseModel):
    run_id: str
    status: str
    theme_count: int
    recommended_stock_count: int
    recommended_etf_count: int
    excluded_count: int
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class RecommendationDetailResponse(BaseModel):
    run: dict[str, Any] | None = None
    themes: list[dict[str, Any]] = Field(default_factory=list)
