from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.base import Base


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_news_content_hash"),)

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(64), nullable=False, index=True)
    external_id = Column(String(256), nullable=True, index=True)
    query = Column(String(256), nullable=False, index=True)
    title = Column(String(1024), nullable=False)
    description = Column(Text, nullable=True)
    link = Column(Text, nullable=False)
    original_link = Column(Text, nullable=True)
    publisher = Column(String(256), nullable=True, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    collected_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    available_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    title_normalized = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, unique=True, index=True)
    canonical_url = Column(Text, nullable=True, index=True)
    normalized_title = Column(Text, nullable=True, index=True)
    content_fingerprint = Column(String(64), nullable=True, index=True)
    duplicate_group_id = Column(String(64), nullable=True, index=True)
    duplicate_of_id = Column(Integer, ForeignKey("news_articles.id"), nullable=True)
    duplicate_of_article_id = Column(Integer, nullable=True, index=True)
    duplicate_reason = Column(String(256), nullable=True)
    is_duplicate = Column(Boolean, nullable=False, default=False, index=True)
    market_relevance_score = Column(Float, nullable=False, default=1.0, index=True)
    is_market_relevant = Column(Boolean, nullable=False, default=True, index=True)
    is_analysis_candidate = Column(Boolean, nullable=False, default=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    raw_data = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    duplicate_of = relationship("NewsArticle", remote_side=[id], uselist=False)
