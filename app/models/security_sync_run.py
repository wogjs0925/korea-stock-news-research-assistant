from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class SecuritySyncRun(Base):
    __tablename__ = "security_sync_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    country_code = Column(String(2), nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    requested_count = Column(Integer, nullable=False, default=0)
    received_count = Column(Integer, nullable=False, default=0)
    valid_count = Column(Integer, nullable=False, default=0)
    created_count = Column(Integer, nullable=False, default=0)
    updated_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    deactivated_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    stock_count = Column(Integer, nullable=False, default=0)
    etf_count = Column(Integer, nullable=False, default=0)
    excluded_security_count = Column(Integer, nullable=False, default=0)
    cik_enriched_count = Column(Integer, nullable=False, default=0)
    unknown_exchange_count = Column(Integer, nullable=False, default=0)
    kospi_stock_count = Column(Integer, nullable=False, default=0)
    kosdaq_stock_count = Column(Integer, nullable=False, default=0)
    konex_stock_count = Column(Integer, nullable=False, default=0)
    kospi_received_count = Column(Integer, nullable=False, default=0)
    kosdaq_received_count = Column(Integer, nullable=False, default=0)
    konex_received_count = Column(Integer, nullable=False, default=0)
    etf_received_count = Column(Integer, nullable=False, default=0)
    kospi_valid_count = Column(Integer, nullable=False, default=0)
    kosdaq_valid_count = Column(Integer, nullable=False, default=0)
    konex_valid_count = Column(Integer, nullable=False, default=0)
    etf_valid_count = Column(Integer, nullable=False, default=0)
    kospi_skipped_count = Column(Integer, nullable=False, default=0)
    kosdaq_skipped_count = Column(Integer, nullable=False, default=0)
    konex_skipped_count = Column(Integer, nullable=False, default=0)
    etf_skipped_count = Column(Integer, nullable=False, default=0)
    recommendation_eligible_count = Column(Integer, nullable=False, default=0)
    recommendation_excluded_count = Column(Integer, nullable=False, default=0)
    leveraged_etf_count = Column(Integer, nullable=False, default=0)
    inverse_etf_count = Column(Integer, nullable=False, default=0)
    unknown_type_count = Column(Integer, nullable=False, default=0)
    duplicate_code_count = Column(Integer, nullable=False, default=0)
    processed_count = Column(Integer, nullable=False, default=0)
    total_count = Column(Integer, nullable=False, default=0)
    progress_percent = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="running", index=True)
    current_stage = Column(String(64), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    skipped_reason_counts = Column(Text, nullable=True)
    krx_response_diagnostics = Column(Text, nullable=True)
    source_file_created_at = Column(String(128), nullable=True)
    snapshot_date = Column(String(32), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


__all__ = ["SecuritySyncRun"]
