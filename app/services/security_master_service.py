from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.security_sync_run import SecuritySyncRun
from app.providers.securities.base import SecurityMasterProvider
from app.providers.securities.krx_kr import KRXConfigurationError, KRXEmptyResponseError, KrxKRProvider, build_krx_provider_from_runtime_settings
from app.providers.securities.mock import MockSecurityMasterProvider
from app.providers.securities.nasdaq_trader_us import NasdaqTraderUSProvider
from app.providers.securities.nasdaq_trader_us import clean_file_creation_time
from app.providers.securities.sec_us import SecUSProvider
from app.repositories.security_repository import (
    count_by_exchange,
    count_existing_by_source,
    count_kr_etf_flag,
    count_kr_unknown_type,
    count_recommendation_eligible,
    count_securities,
    count_sec_aliases_for_us_stocks,
    count_us_recommendation_eligible,
    count_us_stock_with_cik,
    count_us_stock_with_issuer_name,
    count_us_stock_without_cik,
    create_sync_run,
    deactivate_missing,
    ensure_security_tables_schema,
    get_security_by_key,
    get_sync_run_by_run_id,
    last_kr_sync_run,
    last_sync_at,
    last_us_sync_run,
    list_active_us_stocks,
    list_sync_runs,
    save_alias_with_created,
    update_sync_run,
    upsert_security,
    upsert_securities_batch,
)
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log
from app.services.app_setting_service import get_runtime_setting, get_secret_value, runtime_sec_user_agent
from app.utils.security_names import generate_security_key, normalize_ticker
from app.providers.securities.sec_us import normalize_us_ticker_for_match, sec_enrichment_map

logger = logging.getLogger(__name__)
_SEC_ENRICHMENT_RUNNING = False
_KR_SYNC_RUNNING = False


class KRXSyncRunningError(RuntimeError):
    pass


def _log_security_error(
    db: Session,
    error_code: str,
    error_type: str,
    message: str,
    context: dict[str, Any],
    component: str = "security_master",
) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=error_code,
                severity="ERROR",
                component=component,
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except KRXEmptyResponseError as exc:
        status = "failed"
        failed = 1
        error_message = "KRX 기준정보 응답에 KOSPI/KOSDAQ 유효 기준일 데이터가 없습니다."
        try:
            db.rollback()
        except Exception:
            pass
        _log_security_error(
            db,
            "KRX_EMPTY_RESPONSE",
            type(exc).__name__,
            error_message,
            {**_safe_failure_context(run_id, current_stage, exc, repository_function=current_stage, db=db), "provider": provider.name},
            component="security_master_kr",
        )
        counts.update(progress_percent=100)
    except Exception as exc:
        logger.warning("failed to write security master error: %s", type(exc).__name__)


def _log_security_error(
    db: Session,
    error_code: str,
    error_type: str,
    message: str,
    context: dict[str, Any],
    component: str = "security_master",
) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=error_code,
                severity="ERROR",
                component=component,
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except Exception as exc:
        logger.warning("failed to write security master error: %s", type(exc).__name__)


def _session_state(db: Session) -> dict[str, Any]:
    try:
        transaction_active = db.in_transaction()
    except Exception:
        transaction_active = None
    return {
        "session_state": "active" if db.is_active else "inactive",
        "transaction_active": transaction_active,
    }


def _safe_failure_context(
    run_id: str,
    stage: str,
    exc: Exception,
    repository_function: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    context = {
        "run_id": run_id,
        "current_stage": stage,
        "original_exception_type": type(exc).__name__,
        "repository_function": repository_function,
    }
    if db is not None:
        context.update(_session_state(db))
    return context


def _update_run_stage(db: Session, run: SecuritySyncRun, stage: str, **counts: Any) -> None:
    run.current_stage = stage
    _set_run_counts(run, **counts)
    db.add(run)
    db.commit()


def _mark_us_sync_failed_new_session(
    run_id: str,
    error_message: str,
    duration_ms: int,
    stage: str,
    counts: dict[str, Any],
) -> None:
    db = SessionLocal()
    try:
        run = db.scalar(select(SecuritySyncRun).where(SecuritySyncRun.run_id == run_id))
        if run is None:
            return
        run.status = "failed"
        run.current_stage = stage
        run.error_message = error_message
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = duration_ms
        run.failed_count = max(run.failed_count or 0, 1)
        _set_run_counts(run, **counts)
        db.add(run)
        db.commit()
    except Exception as exc:
        logger.warning("failed to mark us sync failed for %s: %s", run_id, type(exc).__name__)
    finally:
        db.close()


def get_provider(provider: str) -> SecurityMasterProvider:
    if provider == "mock":
        return MockSecurityMasterProvider()
    if provider == "us":
        return NasdaqTraderUSProvider()
    if provider == "kr":
        return KrxKRProvider()
    raise ValueError("unknown security provider")


def sync_security_master(db: Session | None, provider_name: str) -> dict[str, Any]:
    if provider_name == "us":
        return sync_us_security_master(db)
    if provider_name == "kr":
        return sync_kr_security_master()
    if db is None:
        raise ValueError("db session is required for this provider")

    ensure_security_tables_schema(db)
    provider = get_provider(provider_name)
    run_id = f"SECURITY-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run = create_sync_run(
        db,
        SecuritySyncRun(
            run_id=run_id,
            country_code=provider.country_code,
            provider=provider.name,
            status="running",
            started_at=datetime.now(timezone.utc),
        ),
    )
    start = time.time()
    created = updated = failed = deactivated = 0
    error_message = None
    rows = []
    try:
        rows = asyncio.run(provider.fetch_securities())
        seen_keys: set[str] = set()
        for item in rows:
            try:
                key = generate_security_key(item.country_code, item.exchange_code, normalize_ticker(item.ticker))
                seen_keys.add(key)
                _, was_created = upsert_security(db, item)
                if was_created:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                failed += 1
                _log_security_error(
                    db,
                    "SECURITY_STORAGE_ERROR",
                    type(exc).__name__,
                    "종목 기준정보 저장 중 오류가 발생했습니다.",
                    {"run_id": run_id, "provider": provider.name, "country_code": item.country_code},
                )
        country_scope = None if provider.country_code == "ALL" else provider.country_code
        deactivated = deactivate_missing(db, provider.name, seen_keys, country_code=country_scope)
        status = "completed" if failed == 0 else ("partial" if created or updated else "failed")
    except Exception as exc:
        status = "failed"
        failed += 1
        error_message = "종목 기준정보 동기화 중 오류가 발생했습니다."
        _log_security_error(
            db,
            "SECURITY_PROVIDER_ERROR",
            type(exc).__name__,
            error_message,
            {"run_id": run_id, "provider": provider.name, "country_code": provider.country_code},
        )

    run.requested_count = len(rows)
    run.created_count = created
    run.updated_count = updated
    run.deactivated_count = deactivated
    run.failed_count = failed
    run.status = status
    run.error_message = error_message
    run.completed_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.time() - start) * 1000)
    update_sync_run(db, run)
    return {
        "run_id": run.run_id,
        "country_code": run.country_code,
        "provider": run.provider,
        "requested_count": run.requested_count,
        "received_count": run.received_count,
        "valid_count": run.valid_count,
        "created_count": run.created_count,
        "updated_count": run.updated_count,
        "skipped_count": run.skipped_count,
        "deactivated_count": run.deactivated_count,
        "failed_count": run.failed_count,
        "stock_count": run.stock_count,
        "etf_count": run.etf_count,
        "excluded_security_count": run.excluded_security_count,
        "cik_enriched_count": run.cik_enriched_count,
        "unknown_exchange_count": run.unknown_exchange_count,
        "kospi_stock_count": getattr(run, "kospi_stock_count", 0),
        "kosdaq_stock_count": getattr(run, "kosdaq_stock_count", 0),
        "konex_stock_count": getattr(run, "konex_stock_count", 0),
        "kospi_received_count": getattr(run, "kospi_received_count", 0),
        "kosdaq_received_count": getattr(run, "kosdaq_received_count", 0),
        "konex_received_count": getattr(run, "konex_received_count", 0),
        "etf_received_count": getattr(run, "etf_received_count", 0),
        "kospi_valid_count": getattr(run, "kospi_valid_count", 0),
        "kosdaq_valid_count": getattr(run, "kosdaq_valid_count", 0),
        "konex_valid_count": getattr(run, "konex_valid_count", 0),
        "etf_valid_count": getattr(run, "etf_valid_count", 0),
        "kospi_skipped_count": getattr(run, "kospi_skipped_count", 0),
        "kosdaq_skipped_count": getattr(run, "kosdaq_skipped_count", 0),
        "konex_skipped_count": getattr(run, "konex_skipped_count", 0),
        "etf_skipped_count": getattr(run, "etf_skipped_count", 0),
        "recommendation_eligible_count": getattr(run, "recommendation_eligible_count", 0),
        "recommendation_excluded_count": getattr(run, "recommendation_excluded_count", 0),
        "leveraged_etf_count": getattr(run, "leveraged_etf_count", 0),
        "inverse_etf_count": getattr(run, "inverse_etf_count", 0),
        "unknown_type_count": getattr(run, "unknown_type_count", 0),
        "duplicate_code_count": getattr(run, "duplicate_code_count", 0),
        "processed_count": getattr(run, "processed_count", 0),
        "total_count": getattr(run, "total_count", 0),
        "progress_percent": getattr(run, "progress_percent", 0),
        "snapshot_date": getattr(run, "snapshot_date", None),
        "status": run.status,
        "current_stage": run.current_stage,
        "duration_ms": run.duration_ms,
        "source_file_created_at": run.source_file_created_at,
        "skipped_reason_counts": getattr(run, "skipped_reason_counts", None),
        "krx_response_diagnostics": getattr(run, "krx_response_diagnostics", None),
        "error_message": run.error_message,
    }


def _sync_response(run: SecuritySyncRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "country_code": run.country_code,
        "provider": run.provider,
        "requested_count": run.requested_count,
        "received_count": run.received_count,
        "valid_count": run.valid_count,
        "created_count": run.created_count,
        "updated_count": run.updated_count,
        "skipped_count": run.skipped_count,
        "deactivated_count": run.deactivated_count,
        "failed_count": run.failed_count,
        "stock_count": run.stock_count,
        "etf_count": run.etf_count,
        "excluded_security_count": run.excluded_security_count,
        "cik_enriched_count": run.cik_enriched_count,
        "unknown_exchange_count": run.unknown_exchange_count,
        "kospi_stock_count": getattr(run, "kospi_stock_count", 0),
        "kosdaq_stock_count": getattr(run, "kosdaq_stock_count", 0),
        "konex_stock_count": getattr(run, "konex_stock_count", 0),
        "kospi_received_count": getattr(run, "kospi_received_count", 0),
        "kosdaq_received_count": getattr(run, "kosdaq_received_count", 0),
        "konex_received_count": getattr(run, "konex_received_count", 0),
        "etf_received_count": getattr(run, "etf_received_count", 0),
        "kospi_valid_count": getattr(run, "kospi_valid_count", 0),
        "kosdaq_valid_count": getattr(run, "kosdaq_valid_count", 0),
        "konex_valid_count": getattr(run, "konex_valid_count", 0),
        "etf_valid_count": getattr(run, "etf_valid_count", 0),
        "kospi_skipped_count": getattr(run, "kospi_skipped_count", 0),
        "kosdaq_skipped_count": getattr(run, "kosdaq_skipped_count", 0),
        "konex_skipped_count": getattr(run, "konex_skipped_count", 0),
        "etf_skipped_count": getattr(run, "etf_skipped_count", 0),
        "recommendation_eligible_count": getattr(run, "recommendation_eligible_count", 0),
        "recommendation_excluded_count": getattr(run, "recommendation_excluded_count", 0),
        "leveraged_etf_count": getattr(run, "leveraged_etf_count", 0),
        "inverse_etf_count": getattr(run, "inverse_etf_count", 0),
        "unknown_type_count": getattr(run, "unknown_type_count", 0),
        "duplicate_code_count": getattr(run, "duplicate_code_count", 0),
        "processed_count": getattr(run, "processed_count", 0),
        "total_count": getattr(run, "total_count", 0),
        "progress_percent": getattr(run, "progress_percent", 0),
        "snapshot_date": getattr(run, "snapshot_date", None),
        "status": run.status,
        "current_stage": run.current_stage,
        "duration_ms": run.duration_ms,
        "source_file_created_at": run.source_file_created_at,
        "skipped_reason_counts": getattr(run, "skipped_reason_counts", None),
        "krx_response_diagnostics": getattr(run, "krx_response_diagnostics", None),
        "error_message": run.error_message,
    }


def _set_run_counts(run: SecuritySyncRun, **counts: Any) -> None:
    for field, value in counts.items():
        if hasattr(run, field):
            setattr(run, field, value)


def _match_sec_record(
    sec_record: dict[str, str | None],
    exact_index: dict[tuple[str, str], list[Any]],
    ticker_index: dict[str, list[Any]],
) -> tuple[Any | None, str]:
    ticker = normalize_us_ticker_for_match(sec_record.get("ticker"))
    exchange_code = sec_record.get("exchange_code")
    if ticker and exchange_code:
        exact = exact_index.get((ticker, exchange_code), [])
        if len(exact) == 1:
            return exact[0], "exact_ticker_exchange"
    if ticker:
        candidates = ticker_index.get(ticker, [])
        if len(candidates) == 1:
            return candidates[0], "unique_ticker"
        if len(candidates) > 1:
            return None, "ambiguous"
    return None, "unmatched"


def _sec_persisted_counts() -> dict[str, int]:
    db = SessionLocal()
    try:
        ensure_security_tables_schema(db)
        return {
            "persisted_cik_count": count_us_stock_with_cik(db),
            "persisted_issuer_name_count": count_us_stock_with_issuer_name(db),
            "persisted_sec_alias_count": count_sec_aliases_for_us_stocks(db),
        }
    finally:
        db.close()


def enrich_us_securities_from_sec() -> dict[str, Any]:
    global _SEC_ENRICHMENT_RUNNING
    if _SEC_ENRICHMENT_RUNNING:
        return {"status": "running"}
    settings = get_settings()
    if not runtime_sec_user_agent(default=settings.sec_user_agent):
        return {"status": "configuration_error", "error_code": "SEC_USER_AGENT_NOT_CONFIGURED"}

    _SEC_ENRICHMENT_RUNNING = True
    start = time.time()
    before_counts = _sec_persisted_counts()
    db = SessionLocal()
    try:
        ensure_security_tables_schema(db)
        sec_payload = asyncio.run(SecUSProvider().fetch_payload())
        sec_map = sec_enrichment_map(sec_payload)
        stocks = list_active_us_stocks(db)
        exact_index: dict[tuple[str, str], list[Any]] = {}
        ticker_index: dict[str, list[Any]] = {}
        for stock in stocks:
            key = normalize_us_ticker_for_match(stock.ticker)
            exact_index.setdefault((key, stock.exchange_code), []).append(stock)
            ticker_index.setdefault(key, []).append(stock)

        matched = attempted_cik = attempted_issuer = attempted_alias = ambiguous = unmatched = skipped = 0
        already_had_cik = duplicate_sec_match = 0
        matched_security_ids: set[int] = set()
        for record in sec_map.values():
            security, method = _match_sec_record(record, exact_index, ticker_index)
            if method == "ambiguous":
                ambiguous += 1
                continue
            if security is None:
                unmatched += 1
                continue
            matched += 1
            if security.id in matched_security_ids:
                duplicate_sec_match += 1
            matched_security_ids.add(security.id)
            cik = record.get("cik")
            name = record.get("name")
            if cik and security.cik == cik:
                already_had_cik += 1
            if cik and security.cik != cik:
                security.cik = cik
                attempted_cik += 1
            if name and security.issuer_name != name:
                security.issuer_name = name
                attempted_issuer += 1
            if name:
                _, created = save_alias_with_created(
                    db,
                    security.id,
                    alias=name,
                    alias_type="legal_name",
                    language="en",
                    source="sec_us",
                )
                if created:
                    attempted_alias += 1
            db.add(security)

        db.commit()
        after_counts = _sec_persisted_counts()
        verified_cik_delta = after_counts["persisted_cik_count"] - before_counts["persisted_cik_count"]
        verified_issuer_delta = after_counts["persisted_issuer_name_count"] - before_counts["persisted_issuer_name_count"]
        verified_alias_delta = after_counts["persisted_sec_alias_count"] - before_counts["persisted_sec_alias_count"]
        return {
            "status": "completed",
            "received_sec_records": len(sec_map),
            "candidate_stock_count": len(stocks),
            "matched_count": matched,
            "matched_sec_record_count": matched,
            "unique_matched_security_count": len(matched_security_ids),
            "duplicate_sec_match_count": duplicate_sec_match,
            "attempted_cik_update_count": attempted_cik,
            "attempted_issuer_name_update_count": attempted_issuer,
            "attempted_alias_create_count": attempted_alias,
            "persisted_cik_count_before": before_counts["persisted_cik_count"],
            "persisted_cik_count_after": after_counts["persisted_cik_count"],
            "verified_cik_delta": verified_cik_delta,
            "persisted_issuer_name_count_before": before_counts["persisted_issuer_name_count"],
            "persisted_issuer_name_count_after": after_counts["persisted_issuer_name_count"],
            "verified_issuer_name_delta": verified_issuer_delta,
            "persisted_sec_alias_count_before": before_counts["persisted_sec_alias_count"],
            "persisted_sec_alias_count_after": after_counts["persisted_sec_alias_count"],
            "verified_sec_alias_delta": verified_alias_delta,
            "cik_updated_count": verified_cik_delta,
            "issuer_name_updated_count": verified_issuer_delta,
            "alias_created_count": verified_alias_delta,
            "already_had_cik_count": already_had_cik,
            "ambiguous_count": ambiguous,
            "unmatched_count": unmatched,
            "skipped_count": skipped,
            "duration_ms": int((time.time() - start) * 1000),
        }
    except Exception as exc:
        db.rollback()
        _log_security_error(
            db,
            "SEC_ENRICHMENT_ERROR",
            type(exc).__name__,
            "SEC CIK 보강 중 오류가 발생했습니다.",
            {"current_stage": "enriching_sec"},
            component="security_master_us",
        )
        raise
    finally:
        db.close()
        _SEC_ENRICHMENT_RUNNING = False


def sync_us_security_master(db: Session | None = None) -> dict[str, Any]:
    if db is not None:
        return _sync_us_security_master(db)
    owned_db = SessionLocal()
    try:
        return _sync_us_security_master(owned_db)
    finally:
        owned_db.close()



def _sync_us_security_master(db: Session) -> dict[str, Any]:
    ensure_security_tables_schema(db)
    settings = get_settings()
    provider = NasdaqTraderUSProvider()
    run_id = f"USSECURITY-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run = create_sync_run(
        db,
        SecuritySyncRun(
            run_id=run_id,
            country_code="US",
            provider=provider.name,
            status="running",
            started_at=datetime.now(timezone.utc),
            current_stage="created",
        ),
    )
    start = time.time()
    created = updated = failed = deactivated = cik_enriched = 0
    status = "completed"
    error_message = None
    counts: dict[str, Any] = {}
    current_stage = "downloading_nasdaq"
    try:
        _update_run_stage(db, run, current_stage)
        snapshot = asyncio.run(provider.fetch_snapshot())
        current_stage = "parsing_nasdaq"
        counts.update(
            requested_count=snapshot.valid_count,
            received_count=snapshot.received_count,
            valid_count=snapshot.valid_count,
            skipped_count=snapshot.skipped_count,
            stock_count=snapshot.stock_count,
            etf_count=snapshot.etf_count,
            excluded_security_count=snapshot.excluded_security_count,
            unknown_exchange_count=snapshot.unknown_exchange_count,
            source_file_created_at=snapshot.source_file_created_at,
            total_count=snapshot.valid_count,
        )
        _update_run_stage(db, run, current_stage, **counts)
        if snapshot.valid_count < settings.us_security_minimum_expected_count:
            status = "failed"
            failed = 1
            error_message = "?? ?? ???? ??? ??? ?? ??? ???? ?????."
            _log_security_error(
                db,
                "US_SECURITY_DATA_QUALITY_ERROR",
                "USDataQualityError",
                error_message,
                {"run_id": run_id, "provider": provider.name, "received_count": snapshot.received_count, "valid_count": snapshot.valid_count},
                component="security_master_us",
            )
        else:
            current_stage = "saving_securities"
            _update_run_stage(db, run, current_stage, **counts)
            created, updated, seen_keys = upsert_securities_batch(db, snapshot.securities)
            counts.update(created_count=created, updated_count=updated, processed_count=snapshot.valid_count, progress_percent=80)
            current_stage = "enriching_sec"
            _update_run_stage(db, run, current_stage, **counts)
            user_agent = runtime_sec_user_agent(default=settings.sec_user_agent)
            if user_agent:
                try:
                    sec_payload = asyncio.run(SecUSProvider().fetch_payload())
                    sec_map = sec_enrichment_map(sec_payload)
                    stocks = list_active_us_stocks(db)
                    exact_index: dict[tuple[str, str], list[Any]] = {}
                    ticker_index: dict[str, list[Any]] = {}
                    for stock in stocks:
                        key = normalize_us_ticker_for_match(stock.ticker)
                        exact_index.setdefault((key, stock.exchange_code), []).append(stock)
                        ticker_index.setdefault(key, []).append(stock)
                    for record in sec_map.values():
                        security, _method = _match_sec_record(record, exact_index, ticker_index)
                        if security is None:
                            continue
                        changed = False
                        cik = record.get("cik")
                        name = record.get("name")
                        if cik and security.cik != cik:
                            security.cik = cik
                            changed = True
                        if name and security.issuer_name != name:
                            security.issuer_name = name
                            changed = True
                        if changed:
                            cik_enriched += 1
                            db.add(security)
                    db.commit()
                except Exception as exc:
                    status = "partial"
                    error_message = "SEC CIK ?? ? ??? ??????."
                    _log_security_error(
                        db,
                        "SEC_ENRICHMENT_ERROR",
                        type(exc).__name__,
                        error_message,
                        {"run_id": run_id, "current_stage": current_stage},
                        component="security_master_us",
                    )
            else:
                status = "partial"
                error_message = "SEC_USER_AGENT? ???? ?? SEC CIK ??? ???????."
            counts.update(cik_enriched_count=cik_enriched, progress_percent=90)
            current_stage = "finalizing"
            _update_run_stage(db, run, current_stage, **counts)
            existing = count_existing_by_source(db, provider.name, "US")
            missing_count = max(existing - len(seen_keys), 0)
            missing_ratio = missing_count / existing if existing else 0.0
            if existing and missing_ratio > settings.us_security_deactivation_max_ratio:
                status = "partial"
                error_message = "???? ?? ??? ?? ??? ??? ?? ?? ??? ??????."
            else:
                deactivated = deactivate_missing(db, provider.name, seen_keys, country_code="US")
            counts.update(deactivated_count=deactivated, progress_percent=100)
    except Exception as exc:
        status = "failed"
        failed = 1
        error_message = "?? ?? ???? ??? ? ??? ??????."
        try:
            db.rollback()
        except Exception:
            pass
        _log_security_error(
            db,
            "US_SECURITY_SYNC_ERROR",
            type(exc).__name__,
            error_message,
            {**_safe_failure_context(run_id, current_stage, exc, repository_function=current_stage, db=db), "provider": provider.name},
            component="security_master_us",
        )
        _mark_us_sync_failed_new_session(run_id, error_message, int((time.time() - start) * 1000), current_stage, counts)
        raise

    run.failed_count = failed
    run.status = status
    run.current_stage = "finalizing"
    run.error_message = error_message
    run.completed_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.time() - start) * 1000)
    _set_run_counts(run, **counts)
    try:
        update_sync_run(db, run)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        error_message = "미국 종목 기준정보 최종 상태 저장 중 오류가 발생했습니다."
        _mark_us_sync_failed_new_session(run_id, error_message, int((time.time() - start) * 1000), "finalizing", counts)
        _log_security_error(
            db,
            "US_SECURITY_SYNC_ERROR",
            type(exc).__name__,
            error_message,
            {**_safe_failure_context(run_id, "finalizing", exc, repository_function="update_sync_run", db=db), "provider": provider.name},
            component="security_master_us",
        )
        raise
    return _sync_response(run)


def _missing_kr_settings() -> list[str]:
    settings = get_settings()
    checks = {
        "KRX_API_KEY": get_secret_value("KRX_API_KEY") or settings.krx_api_key,
        "KRX_API_BASE_URL": get_runtime_setting("KRX_API_BASE_URL", settings.krx_api_base_url),
        "KRX_KOSPI_BASIC_API_ID": get_runtime_setting("KRX_KOSPI_BASIC_API_ID", settings.krx_kospi_basic_api_id),
        "KRX_KOSDAQ_BASIC_API_ID": get_runtime_setting("KRX_KOSDAQ_BASIC_API_ID", settings.krx_kosdaq_basic_api_id),
        "KRX_KONEX_BASIC_API_ID": get_runtime_setting("KRX_KONEX_BASIC_API_ID", settings.krx_konex_basic_api_id),
        "KRX_ETF_DAILY_API_ID": get_runtime_setting("KRX_ETF_DAILY_API_ID", settings.krx_etf_daily_api_id),
    }
    return [name for name, value in checks.items() if not value]


def _mark_kr_sync_failed_new_session(run_id: str, error_message: str, duration_ms: int, stage: str, counts: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        ensure_security_tables_schema(db)
        run = db.scalar(select(SecuritySyncRun).where(SecuritySyncRun.run_id == run_id))
        if run is None:
            return
        run.status = "failed"
        run.current_stage = stage
        run.error_message = error_message
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = duration_ms
        run.failed_count = max(run.failed_count or 0, 1)
        _set_run_counts(run, **counts)
        db.add(run)
        db.commit()
    except Exception as exc:
        logger.warning("failed to mark kr sync failed for %s: %s", run_id, type(exc).__name__)
    finally:
        db.close()


def sync_kr_security_master() -> dict[str, Any]:
    global _KR_SYNC_RUNNING
    if _KR_SYNC_RUNNING:
        raise KRXSyncRunningError("Korean security sync is already running")
    validation_db = SessionLocal()
    try:
        missing_settings = _missing_kr_settings()
        try:
            build_krx_provider_from_runtime_settings(validation_db)
            configuration_error = False
        except KRXConfigurationError:
            configuration_error = True
    finally:
        validation_db.close()
    if missing_settings or configuration_error:
        return {
            "run_id": "",
            "country_code": "KR",
            "provider": "krx_open_api",
            "requested_count": 0,
            "received_count": 0,
            "valid_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "deactivated_count": 0,
            "failed_count": 0,
            "stock_count": 0,
            "etf_count": 0,
            "excluded_security_count": 0,
            "cik_enriched_count": 0,
            "unknown_exchange_count": 0,
            "status": "configuration_error",
            "current_stage": "configuration",
            "duration_ms": 0,
            "source_file_created_at": None,
            "snapshot_date": None,
            "configuration_missing": missing_settings,
            "error_message": "KRX API ?? ?? ??? ??? ??? ????.",
        }
    _KR_SYNC_RUNNING = True
    db = SessionLocal()
    try:
        return _sync_kr_security_master(db)
    finally:
        db.close()
        _KR_SYNC_RUNNING = False


def _sync_kr_security_master(db: Session) -> dict[str, Any]:
    ensure_security_tables_schema(db)
    settings = get_settings()
    provider = build_krx_provider_from_runtime_settings(db)
    run_id = f"KRSECURITY-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run = create_sync_run(
        db,
        SecuritySyncRun(
            run_id=run_id,
            country_code="KR",
            provider=provider.name,
            status="running",
            started_at=datetime.now(timezone.utc),
            current_stage="created",
        ),
    )
    start = time.time()
    created = updated = failed = deactivated = 0
    status = "completed"
    error_message = None
    current_stage = "finding_krx_snapshot"
    counts: dict[str, Any] = {}
    try:
        _update_run_stage(db, run, current_stage)
        snapshot = asyncio.run(provider.fetch_snapshot())
        diagnostics = dict(snapshot.diagnostics)
        skipped_reason_counts = dict(diagnostics.get("skipped_reason_counts") or {})
        securities = snapshot.securities
        keys = [generate_security_key(item.country_code, item.exchange_code, normalize_ticker(item.ticker)) for item in securities]
        duplicate_code_count = len(keys) - len(set(keys))
        counts.update(
            requested_count=len(securities),
            received_count=snapshot.received_count,
            valid_count=snapshot.valid_count,
            skipped_count=snapshot.skipped_count,
            kospi_received_count=int(diagnostics.get("kospi", {}).get("row_count", 0)),
            kosdaq_received_count=int(diagnostics.get("kosdaq", {}).get("row_count", 0)),
            konex_received_count=int(diagnostics.get("konex", {}).get("row_count", 0)),
            etf_received_count=int(diagnostics.get("etf", {}).get("row_count", 0)),
            kospi_valid_count=int(diagnostics.get("kospi", {}).get("converted_count", 0)),
            kosdaq_valid_count=int(diagnostics.get("kosdaq", {}).get("converted_count", 0)),
            konex_valid_count=int(diagnostics.get("konex", {}).get("converted_count", 0)),
            etf_valid_count=int(diagnostics.get("etf", {}).get("converted_count", 0)),
            kospi_skipped_count=sum((diagnostics.get("kospi", {}).get("skipped_reason_counts") or {}).values()),
            kosdaq_skipped_count=sum((diagnostics.get("kosdaq", {}).get("skipped_reason_counts") or {}).values()),
            konex_skipped_count=sum((diagnostics.get("konex", {}).get("skipped_reason_counts") or {}).values()),
            etf_skipped_count=sum((diagnostics.get("etf", {}).get("skipped_reason_counts") or {}).values()),
            stock_count=snapshot.stock_count,
            etf_count=snapshot.etf_count,
            kospi_stock_count=snapshot.kospi_stock_count,
            kosdaq_stock_count=snapshot.kosdaq_stock_count,
            konex_stock_count=snapshot.konex_stock_count,
            recommendation_eligible_count=snapshot.recommendation_eligible_count,
            recommendation_excluded_count=snapshot.recommendation_excluded_count,
            leveraged_etf_count=snapshot.leveraged_etf_count,
            inverse_etf_count=snapshot.inverse_etf_count,
            unknown_type_count=snapshot.unknown_type_count,
            duplicate_code_count=duplicate_code_count,
            snapshot_date=snapshot.snapshot_date,
            total_count=len(securities),
            skipped_reason_counts=json.dumps(skipped_reason_counts, ensure_ascii=False, sort_keys=True),
            krx_response_diagnostics=json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
        )
        current_stage = "validating_krx"
        _update_run_stage(db, run, current_stage, **counts)
        if counts["kospi_valid_count"] <= 0 or counts["kosdaq_valid_count"] <= 0 or snapshot.valid_count < settings.kr_security_minimum_expected_count or duplicate_code_count > max(10, snapshot.valid_count // 20):
            status = "failed"
            failed = 1
            error_message = "KRX ???? ??? ??? ?? ??? ???? ?????."
            _log_security_error(
                db,
                "KRX_DATA_QUALITY_ERROR",
                "KRXDataQualityError",
                error_message,
                {
                    "run_id": run_id,
                    "current_stage": current_stage,
                    "received_count": snapshot.received_count,
                    "valid_count": snapshot.valid_count,
                    "duplicate_code_count": duplicate_code_count,
                    "market_counts": {
                        "kospi": {"received_count": counts["kospi_received_count"], "valid_count": counts["kospi_valid_count"], "skipped_count": counts["kospi_skipped_count"]},
                        "kosdaq": {"received_count": counts["kosdaq_received_count"], "valid_count": counts["kosdaq_valid_count"], "skipped_count": counts["kosdaq_skipped_count"]},
                        "konex": {"received_count": counts["konex_received_count"], "valid_count": counts["konex_valid_count"], "skipped_count": counts["konex_skipped_count"]},
                        "etf": {"received_count": counts["etf_received_count"], "valid_count": counts["etf_valid_count"], "skipped_count": counts["etf_skipped_count"]},
                    },
                    "skipped_reason_counts": skipped_reason_counts,
                    "krx_response_diagnostics": diagnostics,
                },
                component="security_master_kr",
            )
        else:
            current_stage = "saving_securities"
            _update_run_stage(db, run, current_stage, **counts)
            created, updated, seen_keys = upsert_securities_batch(db, securities)
            counts.update(created_count=created, updated_count=updated, processed_count=len(securities), progress_percent=95)
            current_stage = "finalizing"
            _update_run_stage(db, run, current_stage, **counts)
            existing = count_existing_by_source(db, provider.name, "KR")
            missing_count = max(existing - len(seen_keys), 0)
            missing_ratio = missing_count / existing if existing else 0.0
            if existing and missing_ratio > settings.kr_security_deactivation_max_ratio:
                status = "partial"
                error_message = "???? ?? ??? ?? ??? ??? ?? ?? ??? ??????."
            else:
                deactivated = deactivate_missing(db, provider.name, seen_keys, country_code="KR")
            if counts.get("konex_received_count", 0) == 0 or counts.get("etf_received_count", 0) == 0:
                status = "partial"
                error_message = error_message or "KONEX ?? ETF ?? ??? ?? ?? ?? ???? ??????."
            counts.update(deactivated_count=deactivated, progress_percent=100)
            _set_run_counts(run, **counts)
    except KRXEmptyResponseError as exc:
        status = "failed"
        failed = 1
        error_message = "KRX 기준정보 응답에 KOSPI/KOSDAQ 유효 기준일 데이터가 없습니다."
        try:
            db.rollback()
        except Exception:
            pass
        counts.update(progress_percent=100)
        _log_security_error(
            db,
            "KRX_EMPTY_RESPONSE",
            type(exc).__name__,
            error_message,
            {
                **_safe_failure_context(run_id, current_stage, exc, repository_function=current_stage, db=db),
                "provider": provider.name,
                "krx_response_diagnostics": getattr(exc, "diagnostics", None),
            },
            component="security_master_kr",
        )
        _mark_kr_sync_failed_new_session(run_id, error_message, int((time.time() - start) * 1000), current_stage, counts)
    except Exception as exc:
        status = "failed"
        failed = 1
        error_message = "?? ?? ???? ??? ? ??? ??????."
        try:
            db.rollback()
        except Exception:
            pass
        try:
            _log_security_error(
                db,
                "KRX_SYNC_ERROR",
                type(exc).__name__,
                error_message,
                {**_safe_failure_context(run_id, current_stage, exc, repository_function=current_stage, db=db), "provider": provider.name},
                component="security_master_kr",
            )
        finally:
            _mark_kr_sync_failed_new_session(run_id, error_message, int((time.time() - start) * 1000), current_stage, counts)
        raise

    run.failed_count = failed
    run.status = status
    run.current_stage = "finalizing"
    run.error_message = error_message
    run.completed_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.time() - start) * 1000)
    _set_run_counts(run, **counts)
    update_sync_run(db, run)
    return _sync_response(run)

def security_summary(db: Session) -> dict[str, Any]:
    ensure_security_tables_schema(db)
    return {
        "total": count_securities(db),
        "kr_stock": count_securities(db, "KR", "stock"),
        "kr_etf": count_securities(db, "KR", "etf"),
        "us_stock": count_securities(db, "US", "stock"),
        "us_etf": count_securities(db, "US", "etf"),
        "last_sync_at": last_sync_at(db),
    }


def security_data_quality(db: Session) -> dict[str, Any]:
    ensure_security_tables_schema(db)
    last = last_us_sync_run(db)
    last_kr = last_kr_sync_run(db)
    by_exchange = count_by_exchange(db, "US")
    return {
        "us_total": count_securities(db, "US"),
        "us_stock": count_securities(db, "US", "stock"),
        "us_etf": count_securities(db, "US", "etf"),
        "recommendation_eligible_count": count_us_recommendation_eligible(db, True),
        "recommendation_excluded_count": count_us_recommendation_eligible(db, False),
        "us_stock_without_cik_count": count_us_stock_without_cik(db),
        "by_exchange": by_exchange,
        "unknown_exchange_count": sum(count for exchange, count in by_exchange.items() if exchange.startswith("UNKNOWN_")),
        "last_successful_sync_at": last.completed_at if last else None,
        "last_source_file_created_at": last.source_file_created_at if last else None,
        "last_cik_enriched_count": last.cik_enriched_count if last else 0,
        "kr_total": count_securities(db, "KR"),
        "kr_stock": count_securities(db, "KR", "stock"),
        "kr_etf": count_securities(db, "KR", "etf"),
        "kospi_stock_count": last_kr.kospi_stock_count if last_kr else 0,
        "kosdaq_stock_count": last_kr.kosdaq_stock_count if last_kr else 0,
        "konex_stock_count": last_kr.konex_stock_count if last_kr else 0,
        "kr_recommendation_eligible_count": count_recommendation_eligible(db, "KR", True),
        "kr_recommendation_excluded_count": count_recommendation_eligible(db, "KR", False),
        "kr_leveraged_etf_count": count_kr_etf_flag(db, "is_leveraged"),
        "kr_inverse_etf_count": count_kr_etf_flag(db, "is_inverse"),
        "kr_unknown_type_count": count_kr_unknown_type(db),
        "last_kr_successful_sync_at": last_kr.completed_at if last_kr else None,
        "last_kr_snapshot_date": last_kr.snapshot_date if last_kr else None,
    }


def security_sync_runs(
    db: Session,
    country_code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[SecuritySyncRun]:
    ensure_security_tables_schema(db)
    return list_sync_runs(db, country_code=country_code, provider=provider, status=status, limit=limit)


def security_sync_run_detail(db: Session, run_id: str) -> dict[str, Any] | None:
    ensure_security_tables_schema(db)
    run = get_sync_run_by_run_id(db, run_id)
    if run is None:
        return None
    return _sync_response(run)


def cleanup_orphan_kr_sync_run(db: Session, run_id: str, message: str = "서버 종료로 중단된 한국 동기화 실행입니다.") -> bool:
    ensure_security_tables_schema(db)
    run = db.scalar(select(SecuritySyncRun).where(SecuritySyncRun.run_id == run_id, SecuritySyncRun.status == "running"))
    if run is None:
        return False
    run.status = "failed"
    run.current_stage = run.current_stage or "unknown"
    run.error_message = message
    run.failed_count = max(run.failed_count or 0, 1)
    run.completed_at = datetime.now(timezone.utc)
    if run.started_at:
        started_at = run.started_at
        completed_at = run.completed_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        run.duration_ms = int((completed_at - started_at).total_seconds() * 1000)
    db.add(run)
    db.commit()
    return True


def cleanup_orphan_us_sync_run(db: Session, run_id: str, message: str = "서버 종료로 중단된 실행입니다.") -> bool:
    ensure_security_tables_schema(db)
    run = db.scalar(select(SecuritySyncRun).where(SecuritySyncRun.run_id == run_id, SecuritySyncRun.status == "running"))
    if run is None:
        return False
    run.status = "failed"
    run.current_stage = run.current_stage or "unknown"
    run.error_message = message
    run.failed_count = max(run.failed_count or 0, 1)
    run.completed_at = datetime.now(timezone.utc)
    if run.started_at:
        started_at = run.started_at
        completed_at = run.completed_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        run.duration_ms = int((completed_at - started_at).total_seconds() * 1000)
    db.add(run)
    db.commit()
    return True


def cleanup_latest_us_source_file_created_at(db: Session) -> bool:
    ensure_security_tables_schema(db)
    run = last_us_sync_run(db)
    if run is None or not run.source_file_created_at:
        return False
    value = run.source_file_created_at
    if "||||" not in value and "File Creation Time" not in value:
        return False
    parts = [part.strip() for part in value.split(";")]
    labels = ("nasdaqlisted", "otherlisted")
    cleaned = []
    for label, part in zip(labels, parts):
        clean = clean_file_creation_time(part)
        if clean:
            cleaned.append(f"{label}={clean}")
    if not cleaned:
        return False
    run.source_file_created_at = "; ".join(cleaned)
    db.add(run)
    db.commit()
    return True
