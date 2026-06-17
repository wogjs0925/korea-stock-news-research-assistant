from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint

from app.database.base import Base


class NewsSearchTerm(Base):
    __tablename__ = "news_search_terms"
    __table_args__ = (UniqueConstraint("query", "provider", name="uq_search_term_query_provider"),)

    id = Column(Integer, primary_key=True, index=True)
    query = Column(String(256), nullable=False, index=True)
    provider = Column(String(64), nullable=True, index=True)
    source_type = Column(String(16), nullable=False, default="manual", server_default="manual", index=True)
    display = Column(Integer, nullable=False, default=50)
    sort = Column(String(16), nullable=False, default="date")
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
