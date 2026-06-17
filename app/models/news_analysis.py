from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, JSON, Text, UniqueConstraint, Index, ForeignKey
from app.database.base import Base


class NewsAnalysis(Base):
    __tablename__ = "news_analyses"
    __table_args__ = (
        UniqueConstraint("news_article_id", "model_name", "prompt_version", name="uq_news_analysis_unique"),
        Index("ix_news_analysis_news_article_id", "news_article_id"),
    )

    id = Column(Integer, primary_key=True)
    news_article_id = Column(Integer, ForeignKey("news_articles.id"), nullable=False, index=True)
    analysis_run_id = Column(String(128), nullable=False, index=True)
    model_name = Column(String(128), nullable=False, index=True)
    prompt_version = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="completed", index=True)
    summary = Column(Text, nullable=True)
    event_type = Column(String(64), nullable=True, index=True)
    impact_direction = Column(String(32), nullable=True, index=True)
    sentiment_score = Column(Float, nullable=True)
    importance_score = Column(Float, nullable=True, index=True)
    novelty_score = Column(Float, nullable=True)
    market_relevance_score = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    time_horizon = Column(String(32), nullable=True)
    candidate_themes_json = Column(JSON, nullable=False, default=list)
    companies_json = Column(JSON, nullable=False, default=list)
    evidence_points_json = Column(JSON, nullable=False, default=list)
    risk_factors_json = Column(JSON, nullable=False, default=list)
    is_investment_relevant = Column(Boolean, nullable=False, default=False, index=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    openai_request_id = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    analyzed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
