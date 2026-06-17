from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from app.database.base import Base


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    error_code = Column(String(128), nullable=False, index=True)
    severity = Column(String(32), nullable=False, index=True)
    component = Column(String(128), nullable=False, index=True)
    error_type = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    run_id = Column(String(128), nullable=True, index=True)
    ticker = Column(String(32), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="new", index=True)
    fingerprint = Column(String(64), nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    context_json = Column(JSON, nullable=False, default=dict)
    app_version = Column(String(64), nullable=True)
    model_version = Column(String(64), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
