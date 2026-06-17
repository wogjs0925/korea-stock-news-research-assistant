import hashlib
from typing import Any

from app.services.credential_service import mask_sensitive_text

from app.models.error_log import ErrorLog
from app.repositories.error_repository import (
    create_error_log as repository_create_error_log,
    get_error_log_by_id,
    update_error_status as repository_update_error_status,
)
from app.schemas.error import ErrorLogCreate


ALLOWED_SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
ALLOWED_STATUSES = {
    "new",
    "investigating",
    "planned",
    "resolved",
    "ignored",
    "reopened",
}
SENSITIVE_KEYS = {
    "api_key",
    "openai_api_key",
    "authorization",
    "password",
    "access_token",
    "refresh_token",
    "secret",
    "token",
}


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in SENSITIVE_KEYS or "secret" in lowered or "api_key" in lowered or "authorization" in lowered:
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return mask_sensitive_text(value)
    return value


def _generate_fingerprint(component: str, error_type: str, message: str) -> str:
    digest = hashlib.sha256()
    digest.update(component.encode("utf-8"))
    digest.update(error_type.encode("utf-8"))
    digest.update(message.encode("utf-8"))
    return digest.hexdigest()


def create_error_log(db: Any, error_data: ErrorLogCreate) -> ErrorLog:
    severity = error_data.severity.upper()
    if severity not in ALLOWED_SEVERITIES:
        raise ValueError(
            f"허용되지 않은 severity 값입니다. 지원 값: {', '.join(sorted(ALLOWED_SEVERITIES))}"
        )

    context_json = _sanitize_value(error_data.context_json)
    fingerprint = error_data.fingerprint
    if not fingerprint:
        fingerprint = _generate_fingerprint(
            error_data.component, error_data.error_type, error_data.message
        )

    error_log = ErrorLog(
        error_code=error_data.error_code,
        severity=severity,
        component=error_data.component,
        error_type=error_data.error_type,
        message=error_data.message,
        stack_trace=error_data.stack_trace,
        run_id=error_data.run_id,
        ticker=error_data.ticker,
        status="new",
        fingerprint=fingerprint,
        retry_count=error_data.retry_count,
        context_json=context_json,
        app_version=error_data.app_version,
        model_version=error_data.model_version,
        prompt_version=error_data.prompt_version,
    )
    return repository_create_error_log(db, error_log)


def update_error_status(db: Any, error_id: int, status: str) -> ErrorLog:
    normalized_status = status.lower()
    if normalized_status not in ALLOWED_STATUSES:
        raise ValueError(
            f"허용되지 않은 status 값입니다. 지원 값: {', '.join(sorted(ALLOWED_STATUSES))}"
        )

    error_log = get_error_log_by_id(db, error_id)
    if error_log is None:
        raise LookupError("오류를 찾을 수 없습니다.")

    return repository_update_error_status(db, error_log, normalized_status)
