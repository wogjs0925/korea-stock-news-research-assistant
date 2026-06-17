from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.repositories.security_repository import get_security, list_securities
from app.schemas.security import SecurityOut, SecuritySummaryResponse, SecuritySyncResponse
from app.services.security_master_service import (
    KRXSyncRunningError,
    enrich_us_securities_from_sec,
    security_data_quality,
    security_summary,
    security_sync_run_detail,
    security_sync_runs,
    sync_security_master,
)
from app.services.security_alias_service import backfill_security_aliases
from app.services.security_match_service import match_security_dict
from app.services.theme_security_matching_service import match_theme_securities, theme_security_candidates

router = APIRouter(prefix="/securities", tags=["Securities"])


@router.get("/summary", response_model=SecuritySummaryResponse)
def summary(db: Session = Depends(get_db)):
    return security_summary(db)


@router.get("/sync-runs")
def sync_runs(
    db: Session = Depends(get_db),
    country_code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    return security_sync_runs(db, country_code=country_code, provider=provider, status=status, limit=limit)


@router.get("/sync-runs/{run_id}")
def sync_run_detail(run_id: str, db: Session = Depends(get_db)):
    row = security_sync_run_detail(db, run_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sync run not found")
    return row


@router.get("/data-quality")
def data_quality(db: Session = Depends(get_db)):
    return security_data_quality(db)


@router.post("/sync/mock", response_model=SecuritySyncResponse)
def sync_mock(db: Session = Depends(get_db)):
    if get_settings().app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mock sync is development only")
    return sync_security_master(db, "mock")


@router.post("/sync/us", response_model=SecuritySyncResponse)
def sync_us():
    try:
        return sync_security_master(None, "us")
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="미국 종목 기준정보 동기화에 실패했습니다.") from None


@router.post("/enrich/us/sec")
def enrich_us_sec():
    if get_settings().app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SEC enrichment is development only")
    result = enrich_us_securities_from_sec()
    if result.get("status") == "configuration_error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SEC_USER_AGENT가 설정되지 않아 SEC CIK 보강을 실행할 수 없습니다.",
        )
    if result.get("status") == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SEC CIK 보강 작업이 이미 실행 중입니다.")
    return result


@router.post("/aliases/backfill")
def backfill_aliases(db: Session = Depends(get_db)):
    if get_settings().app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="alias backfill is development only")
    return backfill_security_aliases(db)


@router.post("/sync/kr", response_model=SecuritySyncResponse)
def sync_kr():
    try:
        result = sync_security_master(None, "kr")
        if result.get("status") == "configuration_error":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="KRX API 인증키 또는 승인된 서비스 설정이 없습니다.",
            )
        return result
    except KRXSyncRunningError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="한국 종목 동기화가 이미 실행 중입니다.") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="한국 종목 기준정보 동기화에 실패했습니다.") from None


@router.get("", response_model=list[SecurityOut])
def securities(
    db: Session = Depends(get_db),
    country_code: str | None = None,
    asset_type: str | None = None,
    exchange_code: str | None = None,
    ticker: str | None = None,
    keyword: str | None = None,
    is_active: bool | None = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return list_securities(
        db,
        country_code=country_code,
        asset_type=asset_type,
        exchange_code=exchange_code,
        ticker=ticker,
        keyword=keyword,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.get("/search")
def search(
    query: str,
    db: Session = Depends(get_db),
    country_code: str | None = None,
    asset_type: str | None = None,
    limit: int = Query(10, ge=1, le=50),
):
    return match_security_dict(db, query, country_code=country_code, asset_type=asset_type, limit=limit)


@router.post("/themes/{theme_id}/match-securities")
def match_theme(theme_id: int, db: Session = Depends(get_db)):
    return match_theme_securities(db, theme_id)


@router.get("/themes/{theme_id}/security-candidates")
def theme_candidates(theme_id: int, db: Session = Depends(get_db)):
    return theme_security_candidates(db, theme_id)


@router.get("/{security_id}", response_model=SecurityOut)
def security_detail(security_id: int, db: Session = Depends(get_db)):
    security_row = get_security(db, security_id)
    if security_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="security not found")
    return security_row
