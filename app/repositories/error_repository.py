from typing import Any, Iterable

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.error_log import ErrorLog


def create_error_log(db: Session, error_log: ErrorLog) -> ErrorLog:
    db.add(error_log)
    db.commit()
    db.refresh(error_log)
    return error_log


def list_error_logs(
    db: Session,
    limit: int = 100,
    severity: str | None = None,
    component: str | None = None,
    status: str | None = None,
    ticker: str | None = None,
) -> list[ErrorLog]:
    query = select(ErrorLog)

    if severity:
        query = query.where(ErrorLog.severity == severity)
    if component:
        query = query.where(ErrorLog.component == component)
    if status:
        query = query.where(ErrorLog.status == status)
    if ticker:
        query = query.where(ErrorLog.ticker == ticker)

    query = query.order_by(desc(ErrorLog.occurred_at), desc(ErrorLog.id)).limit(limit)
    return db.scalars(query).all()


def get_error_log_by_id(db: Session, error_id: int) -> ErrorLog | None:
    return db.get(ErrorLog, error_id)


def update_error_status(db: Session, error_log: ErrorLog, status: str) -> ErrorLog:
    error_log.status = status
    db.add(error_log)
    db.commit()
    db.refresh(error_log)
    return error_log


def get_error_summary(db: Session) -> dict[str, int]:
    total = db.scalar(select(func.count()).select_from(ErrorLog)) or 0
    unresolved = db.scalar(
        select(func.count()).select_from(ErrorLog).where(
            ErrorLog.status.notin_(["resolved", "ignored"])
        )
    ) or 0
    critical = db.scalar(
        select(func.count()).select_from(ErrorLog).where(ErrorLog.severity == "CRITICAL")
    ) or 0
    warning = db.scalar(
        select(func.count()).select_from(ErrorLog).where(ErrorLog.severity == "WARNING")
    ) or 0
    error = db.scalar(
        select(func.count()).select_from(ErrorLog).where(ErrorLog.severity == "ERROR")
    ) or 0
    info = db.scalar(
        select(func.count()).select_from(ErrorLog).where(ErrorLog.severity == "INFO")
    ) or 0

    return {
        "total": int(total),
        "unresolved": int(unresolved),
        "critical": int(critical),
        "warning": int(warning),
        "error": int(error),
        "info": int(info),
    }
