from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.schemas.error import ErrorLogCreate, ErrorLogRead, ErrorStatusUpdate, ErrorSummary
from app.services.error_service import create_error_log, update_error_status
from app.repositories.error_repository import (
    get_error_log_by_id,
    get_error_summary,
    list_error_logs,
)


settings = get_settings()
router = APIRouter(prefix="/errors", tags=["Error Center"])


@router.post("/", response_model=ErrorLogRead, status_code=status.HTTP_201_CREATED)
def post_error_log(
    payload: ErrorLogCreate,
    db: Session = Depends(get_db),
) -> ErrorLogRead:
    try:
        error_log = create_error_log(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return error_log


@router.get("/", response_model=list[ErrorLogRead])
def get_error_logs(
    limit: int = Query(100, ge=1, le=500),
    severity: str | None = None,
    component: str | None = None,
    status: str | None = None,
    ticker: str | None = None,
    db: Session = Depends(get_db),
) -> list[ErrorLogRead]:
    if severity:
        severity = severity.upper()
    if status:
        status = status.lower()

    return list_error_logs(
        db,
        limit=limit,
        severity=severity,
        component=component,
        status=status,
        ticker=ticker,
    )


@router.get("/summary", response_model=ErrorSummary)
def get_error_summary_endpoint(db: Session = Depends(get_db)) -> dict[str, int]:
    return get_error_summary(db)


@router.get("/{error_id}", response_model=ErrorLogRead)
def get_error_log(error_id: int, db: Session = Depends(get_db)) -> ErrorLogRead:
    error_log = get_error_log_by_id(db, error_id)
    if error_log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="오류를 찾을 수 없습니다.")
    return error_log


@router.patch("/{error_id}/status", response_model=ErrorLogRead)
def patch_error_status(
    error_id: int,
    payload: ErrorStatusUpdate,
    db: Session = Depends(get_db),
) -> ErrorLogRead:
    try:
        return update_error_status(db, error_id, payload.status)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="오류를 찾을 수 없습니다.")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/demo", response_model=ErrorLogRead, status_code=status.HTTP_201_CREATED)
def post_demo_error(db: Session = Depends(get_db)) -> ErrorLogRead:
    if settings.app_env != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="demo 오류는 개발 환경에서만 생성할 수 있습니다.",
        )

    payload = ErrorLogCreate(
        error_code="DEMO_ERROR",
        severity="WARNING",
        component="error_center",
        error_type="DemoError",
        message="Error Center 동작 확인을 위한 테스트 오류입니다.",
        context_json={"source": "manual_demo"},
    )
    return create_error_log(db, payload)
