from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal
from pydantic import ConfigDict


EventType = Literal[
    "earnings",
    "contract",
    "investment",
    "policy",
    "regulation",
    "product",
    "technology",
    "partnership",
    "merger_acquisition",
    "financing",
    "legal_risk",
    "macroeconomy",
    "market_trend",
    "other",
]

ImpactDirection = Literal["positive", "negative", "neutral", "mixed"]
TimeHorizon = Literal["intraday", "short_term", "medium_term", "long_term", "unknown"]


class CompanyMention(BaseModel):
    company_name: str
    relation: str | None = None
    relevance: float = Field(0.0, ge=0.0, le=1.0)
    impact_direction: ImpactDirection | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("relevance", "confidence", mode="before")
    def clamp_company_score(cls, value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class NewsAnalysisOutput(BaseModel):
    summary: str = ""
    event_type: EventType = "other"
    impact_direction: ImpactDirection = "neutral"
    sentiment_score: float = Field(0.0, ge=-1.0, le=1.0)
    importance_score: float = Field(0.0, ge=0.0, le=1.0)
    novelty_score: float = Field(0.0, ge=0.0, le=1.0)
    market_relevance_score: float = Field(0.0, ge=0.0, le=1.0)
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    time_horizon: TimeHorizon = "unknown"
    candidate_themes: List[str] = Field(default_factory=list)
    companies: List[CompanyMention] = Field(default_factory=list)
    evidence_points: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    is_investment_relevant: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_validator(
        "importance_score",
        "novelty_score",
        "market_relevance_score",
        "confidence_score",
        mode="before",
    )
    def clamp_unit_score(cls, value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("sentiment_score", mode="before")
    def clamp_sentiment_score(cls, value):
        try:
            return max(-1.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
