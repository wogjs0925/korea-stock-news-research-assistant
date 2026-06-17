from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Float, Text
from app.database.base import Base


class NewsAnalysisRun(Base):
    __tablename__ = "news_analysis_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(128), nullable=False, unique=True)
    model_name = Column(String(128), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    requested_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
