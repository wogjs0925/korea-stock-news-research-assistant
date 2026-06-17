from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint

from app.database.base import Base


class ThemeNewsLink(Base):
    __tablename__ = "theme_news_links"
    __table_args__ = (
        UniqueConstraint("market_theme_id", "news_analysis_id", name="uq_theme_news_link_theme_analysis"),
    )

    id = Column(Integer, primary_key=True)
    market_theme_id = Column(Integer, ForeignKey("market_themes.id"), nullable=False, index=True)
    news_analysis_id = Column(Integer, ForeignKey("news_analyses.id"), nullable=False, index=True)
    relevance_score = Column(Float, nullable=False)
    evidence_reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
