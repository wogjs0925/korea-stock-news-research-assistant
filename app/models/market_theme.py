from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint

from app.database.base import Base


class MarketTheme(Base):
    __tablename__ = "market_themes"
    __table_args__ = (
        UniqueConstraint("theme_run_id", "rank", name="uq_market_theme_run_rank"),
        Index("ix_market_theme_theme_run_id", "theme_run_id"),
    )

    id = Column(Integer, primary_key=True)
    theme_run_id = Column(Integer, ForeignKey("theme_analysis_runs.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    theme_name = Column(String(256), nullable=False, index=True)
    normalized_theme_name = Column(String(256), nullable=False, index=True)
    theme_summary = Column(Text, nullable=False)
    why_now = Column(Text, nullable=False)
    impact_direction = Column(String(32), nullable=False)
    confidence_score = Column(Float, nullable=False)
    calculated_score = Column(Float, nullable=False, index=True)
    actionability_score = Column(Float, nullable=False, default=0.0, index=True)
    price_impact_score = Column(Float, nullable=False, default=0.0, index=True)
    investable_link_score = Column(Float, nullable=False, default=0.0, index=True)
    is_investable_theme = Column(Boolean, nullable=False, default=False, index=True)
    theme_bucket = Column(String(64), nullable=False, default="low_actionability", index=True)
    theme_bucket_reason = Column(String(512), nullable=True)
    time_horizon = Column(String(32), nullable=False)
    related_industries_json = Column(JSON, nullable=False, default=list)
    related_companies_json = Column(JSON, nullable=False, default=list)
    risk_factors_json = Column(JSON, nullable=False, default=list)
    issue_tags_json = Column(JSON, nullable=False, default=list)
    direct_impact_industries_json = Column(JSON, nullable=False, default=list)
    entity_business_industries_json = Column(JSON, nullable=False, default=list)
    market_theme_tags_json = Column(JSON, nullable=False, default=list)
    candidate_search_tags_json = Column(JSON, nullable=False, default=list)
    tag_confidence_json = Column(JSON, nullable=False, default=dict)
    evidence_count = Column(Integer, nullable=False, default=0)
    source_publisher_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
