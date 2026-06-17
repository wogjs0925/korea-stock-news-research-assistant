from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class ThemeAnalysisRun(Base):
    __tablename__ = "theme_analysis_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    model_name = Column(String(128), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    requested_source_count = Column(Integer, nullable=False, default=0)
    selected_source_count = Column(Integer, nullable=False, default=0)
    selected_theme_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, index=True)
    market_overview = Column(Text, nullable=True)
    insufficient_data_reason = Column(Text, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_code = Column(String(128), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
