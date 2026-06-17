from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorLogCreate(BaseModel):
    error_code: str
    severity: str
    component: str
    error_type: str
    message: str
    stack_trace: str | None = None
    run_id: str | None = None
    ticker: str | None = None
    fingerprint: str | None = None
    retry_count: int = 0
    context_json: dict[str, Any] = Field(default_factory=dict)
    app_version: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None


class ErrorLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    error_code: str
    severity: str
    component: str
    error_type: str
    message: str
    stack_trace: str | None
    run_id: str | None
    ticker: str | None
    status: str
    fingerprint: str | None
    retry_count: int
    context_json: dict[str, Any]
    app_version: str | None
    model_version: str | None
    prompt_version: str | None
    occurred_at: datetime
    created_at: datetime


class ErrorStatusUpdate(BaseModel):
    status: str


class ErrorSummary(BaseModel):
    total: int
    unresolved: int
    critical: int
    warning: int
    error: int
    info: int
