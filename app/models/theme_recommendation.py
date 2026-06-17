from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint

from app.database.base import Base


class ThemeRecommendation(Base):
    __tablename__ = "theme_recommendations"
    __table_args__ = (UniqueConstraint("recommendation_run_id", "market_theme_id", name="uq_theme_recommendation_run_theme"),)

    id = Column(Integer, primary_key=True)
    recommendation_run_id = Column(Integer, ForeignKey("recommendation_runs.id"), nullable=False, index=True)
    market_theme_id = Column(Integer, ForeignKey("market_themes.id"), nullable=False, index=True)
    theme_name = Column(String(256), nullable=False)
    theme_score = Column(Float, nullable=False, default=0.0)
    confidence_score = Column(Float, nullable=False, default=0.0)
    impact_direction = Column(String(32), nullable=False)
    recommendation_summary = Column(Text, nullable=True)
    risk_summary = Column(Text, nullable=True)
    stock_count = Column(Integer, nullable=False, default=0)
    etf_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["ThemeRecommendation"]
