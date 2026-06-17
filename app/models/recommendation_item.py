from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from app.database.base import Base


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"

    id = Column(Integer, primary_key=True)
    theme_recommendation_id = Column(Integer, ForeignKey("theme_recommendations.id"), nullable=False, index=True)
    market_theme_id = Column(Integer, ForeignKey("market_themes.id"), nullable=False, index=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=True, index=True)
    candidate_id = Column(Integer, ForeignKey("theme_security_candidates.id"), nullable=True, index=True)
    rank = Column(Integer, nullable=False, default=0)
    asset_type = Column(String(16), nullable=False, index=True)
    country_code = Column(String(2), nullable=True, index=True)
    ticker = Column(String(32), nullable=True)
    security_name = Column(String(256), nullable=False)
    exchange_code = Column(String(16), nullable=True)
    final_score = Column(Float, nullable=False, default=0.0, index=True)
    candidate_score = Column(Float, nullable=False, default=0.0)
    theme_fit_score = Column(Float, nullable=False, default=0.0)
    evidence_score = Column(Float, nullable=False, default=0.0)
    diversification_score = Column(Float, nullable=False, default=0.0)
    risk_penalty_score = Column(Float, nullable=False, default=0.0)
    selection_reason = Column(Text, nullable=True)
    evidence_summary = Column(Text, nullable=True)
    risk_flags_json = Column(JSON, nullable=False, default=list)
    exclusion_flags_json = Column(JSON, nullable=False, default=list)
    is_selected = Column(Boolean, nullable=False, default=False, index=True)
    is_excluded = Column(Boolean, nullable=False, default=False, index=True)
    excluded_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["RecommendationItem"]
