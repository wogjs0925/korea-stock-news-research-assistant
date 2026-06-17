from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.base import Base


class ThemeCandidateRun(Base):
    __tablename__ = "theme_candidate_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    theme_run_id = Column(Integer, ForeignKey("theme_analysis_runs.id"), nullable=True, index=True)
    status = Column(String(32), nullable=False, index=True)
    theme_count = Column(Integer, nullable=False, default=0)
    stock_candidate_count = Column(Integer, nullable=False, default=0)
    etf_candidate_count = Column(Integer, nullable=False, default=0)
    ambiguous_count = Column(Integer, nullable=False, default=0)
    unmatched_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


__all__ = ["ThemeCandidateRun"]
