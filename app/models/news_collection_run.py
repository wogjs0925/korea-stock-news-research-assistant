from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class NewsCollectionRun(Base):
    __tablename__ = "news_collection_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    provider = Column(String(64), nullable=False, index=True)
    query = Column(String(256), nullable=False, index=True)
    requested_count = Column(Integer, nullable=False, default=0)
    received_count = Column(Integer, nullable=False, default=0)
    saved_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="running", index=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
