from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.market_theme import MarketTheme
from app.models.recommendation_item import RecommendationItem
from app.models.recommendation_run import RecommendationRun
from app.models.security import Security
from app.models.theme_recommendation import ThemeRecommendation
from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.repositories.recommendation_repository import (
    create_recommendation_item,
    create_recommendation_run,
    create_theme_recommendation,
    ensure_recommendation_tables_schema,
    get_latest_candidate_run_for_theme_run,
    get_latest_recommendation_run,
    get_latest_theme_recommendation,
    get_recommendation_run_by_run_id,
    list_candidate_rows_for_theme,
    list_recommendation_items,
    list_recommendation_runs,
    list_theme_recommendations,
    update_recommendation_run,
)
from app.repositories.theme_analysis_repository import get_latest_theme_run, get_market_theme, list_themes_for_run
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log
from app.services.theme_candidate_service import theme_candidate_diagnostics
from app.utils.display_labels import EXCLUSION_FLAG_LABELS, label_list
from app.utils.security_names import normalize_company_name

settings = get_settings()


@dataclass
class ScoredCandidate:
    candidate: ThemeSecurityCandidate
    security: Security
    score: float
    diversification_score: float
    risk_penalty: float
    flags: list[str]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:128] for item in value if item]


def _log_recommendation_error(db: Session, code: str, error_type: str, message: str, context: dict[str, Any]) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=code,
                severity="ERROR",
                component="recommendation_engine",
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except Exception:
        pass


def calculate_recommendation_score(
    candidate: ThemeSecurityCandidate,
    theme: MarketTheme,
    *,
    diversification_score: float = 0.0,
    extra_risk_penalty: float = 0.0,
) -> float:
    candidate_score = candidate.final_candidate_score or 0.0
    theme_score = ((theme.confidence_score or 0.0) + (theme.calculated_score or 0.0)) / 2
    evidence_strength = max(candidate.evidence_score or 0.0, min((candidate.evidence_count or 0) / 5, 1.0) * 0.5)
    risk_penalty = (candidate.risk_penalty_score or 0.0) + extra_risk_penalty
    return _clamp(candidate_score * 0.60 + theme_score * 0.15 + evidence_strength * 0.15 + diversification_score * 0.05 - risk_penalty * 0.05)


def _base_exclusion_flags(
    candidate: ThemeSecurityCandidate,
    security: Security | None,
    *,
    include_leveraged_inverse_etfs: bool,
    min_candidate_score: float,
    min_evidence_score: float,
    stock_country_scope: str,
) -> list[str]:
    flags: list[str] = []
    if candidate.match_status == "ambiguous":
        flags.append("ambiguous_match")
    elif candidate.match_status != "matched":
        flags.append(f"{candidate.match_status}_match")
    if security is None:
        flags.append("missing_security")
    else:
        if not security.is_active:
            flags.append("inactive_security")
        if not security.is_recommendation_eligible:
            flags.append("recommendation_excluded_security")
        if security.asset_type not in {"stock", "etf"}:
            flags.append("unsupported_asset_type")
        if security.country_code not in {"KR", "US"}:
            flags.append("unsupported_country")
        if security.asset_type == "stock":
            if stock_country_scope == "KR_ONLY" and security.country_code == "US":
                flags.append("overseas_reference_stock")
            elif stock_country_scope == "US_ONLY" and security.country_code == "KR":
                flags.append("outside_stock_country_scope")
        if security.asset_type == "etf" and not include_leveraged_inverse_etfs:
            if security.is_leveraged:
                flags.append("leveraged_etf_default_excluded")
            if security.is_inverse:
                flags.append("inverse_etf_default_excluded")
    if (candidate.final_candidate_score or 0.0) < min_candidate_score:
        flags.append("low_candidate_score")
    if (candidate.evidence_score or 0.0) < min_evidence_score and (candidate.evidence_count or 0) <= 0:
        flags.append("insufficient_evidence")
    if candidate.match_method == "weak_industry_keyword":
        flags.append("weak_industry_candidate")
    return flags


def _score_rows(
    rows: list[tuple[ThemeSecurityCandidate, Security | None]],
    theme: MarketTheme,
    *,
    include_leveraged_inverse_etfs: bool,
    min_candidate_score: float,
    min_evidence_score: float,
    diversify_country: bool,
    stock_country_scope: str,
) -> tuple[list[ScoredCandidate], list[tuple[ThemeSecurityCandidate, Security | None, list[str]]]]:
    selected_pool: list[ScoredCandidate] = []
    excluded: list[tuple[ThemeSecurityCandidate, Security | None, list[str]]] = []
    country_counts: Counter[str] = Counter()
    company_names: set[str] = set()
    for candidate, security in rows:
        flags = _base_exclusion_flags(
            candidate,
            security,
            include_leveraged_inverse_etfs=include_leveraged_inverse_etfs,
            min_candidate_score=min_candidate_score,
            min_evidence_score=min_evidence_score,
            stock_country_scope=stock_country_scope,
        )
        if security is not None:
            normalized = normalize_company_name(security.issuer_name or security.name)
            if normalized and normalized in company_names and security.asset_type == "stock":
                flags.append("duplicate_company")
            elif normalized and security.asset_type == "stock":
                company_names.add(normalized)
        if flags:
            excluded.append((candidate, security, flags))
            continue
        country = security.country_code if security else ""
        diversification_score = 0.1 if diversify_country and country and country_counts[country] == 0 else 0.0
        country_counts[country] += 1
        extra_risk = 0.25 if security and (security.is_leveraged or security.is_inverse) else 0.0
        risk_flags = _safe_list(candidate.risk_flags_json)
        if security and security.is_leveraged and "leveraged_etf" not in risk_flags:
            risk_flags.append("leveraged_etf")
        if security and security.is_inverse and "inverse_etf" not in risk_flags:
            risk_flags.append("inverse_etf")
        score = calculate_recommendation_score(candidate, theme, diversification_score=diversification_score, extra_risk_penalty=extra_risk)
        selected_pool.append(ScoredCandidate(candidate, security, score, diversification_score, extra_risk, risk_flags))
    selected_pool.sort(key=lambda item: (item.score, item.candidate.evidence_score, item.candidate.match_score, item.candidate.evidence_count), reverse=True)
    return selected_pool, excluded


def _item_from_scored(theme_recommendation: ThemeRecommendation, theme: MarketTheme, scored: ScoredCandidate, rank: int) -> RecommendationItem:
    candidate = scored.candidate
    security = scored.security
    return RecommendationItem(
        theme_recommendation_id=theme_recommendation.id,
        market_theme_id=theme.id,
        security_id=security.id,
        candidate_id=candidate.id,
        rank=rank,
        asset_type=security.asset_type,
        country_code=security.country_code,
        ticker=security.ticker,
        security_name=security.name,
        exchange_code=security.exchange_code,
        final_score=scored.score,
        candidate_score=candidate.final_candidate_score or 0.0,
        theme_fit_score=candidate.theme_fit_score or 0.0,
        evidence_score=candidate.evidence_score or 0.0,
        diversification_score=scored.diversification_score,
        risk_penalty_score=(candidate.risk_penalty_score or 0.0) + scored.risk_penalty,
        selection_reason=f"{theme.theme_name} 테마와 후보 생성 근거가 일치해 관심 후보로 선정했습니다.",
        evidence_summary=(candidate.reason_summary or "")[:1000],
        risk_flags_json=scored.flags,
        exclusion_flags_json=[],
        is_selected=True,
        is_excluded=False,
    )


def _excluded_item(
    theme_recommendation: ThemeRecommendation,
    theme: MarketTheme,
    candidate: ThemeSecurityCandidate,
    security: Security | None,
    flags: list[str],
) -> RecommendationItem:
    return RecommendationItem(
        theme_recommendation_id=theme_recommendation.id,
        market_theme_id=theme.id,
        security_id=security.id if security else None,
        candidate_id=candidate.id,
        rank=0,
        asset_type=candidate.asset_type or (security.asset_type if security else "unknown"),
        country_code=candidate.country_code or (security.country_code if security else None),
        ticker=security.ticker if security else None,
        security_name=security.name if security else candidate.source_company_name,
        exchange_code=security.exchange_code if security else None,
        final_score=0.0,
        candidate_score=candidate.final_candidate_score or 0.0,
        theme_fit_score=candidate.theme_fit_score or 0.0,
        evidence_score=candidate.evidence_score or 0.0,
        diversification_score=0.0,
        risk_penalty_score=candidate.risk_penalty_score or 0.0,
        selection_reason=None,
        evidence_summary=(candidate.reason_summary or "")[:1000],
        risk_flags_json=_safe_list(candidate.risk_flags_json),
        exclusion_flags_json=flags,
        is_selected=False,
        is_excluded=True,
        excluded_reason=", ".join(label_list(flags, EXCLUSION_FLAG_LABELS)),
    )


def run_recommendations(
    db: Session,
    *,
    theme_run_id: int | None = None,
    theme_id: int | None = None,
    max_stocks_per_theme: int = 3,
    max_etfs_per_theme: int = 2,
    include_leveraged_inverse_etfs: bool = False,
    min_candidate_score: float = 0.35,
    min_evidence_score: float = 0.1,
    diversify_country: bool = True,
    stock_country_scope: str | None = None,
) -> dict[str, Any]:
    ensure_recommendation_tables_schema(db)
    started = time.time()
    run_id = f"RECOMMEND-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    source_theme_run = None
    stock_country_scope = (stock_country_scope or settings.recommend_stock_country_scope).upper()
    if stock_country_scope not in {"KR_ONLY", "KR_AND_US", "US_ONLY"}:
        stock_country_scope = "KR_ONLY"
    if theme_id is None:
        source_theme_run = db.get(MarketTheme, theme_id).theme_run_id if theme_id else None
        latest = get_latest_theme_run(db) if theme_run_id is None else None
        source_theme_run = theme_run_id or (latest.id if latest else None)
        themes = list_themes_for_run(db, source_theme_run) if source_theme_run else []
    else:
        theme = get_market_theme(db, theme_id)
        themes = [theme] if theme else []
        source_theme_run = theme.theme_run_id if theme else theme_run_id
    candidate_run = get_latest_candidate_run_for_theme_run(db, source_theme_run)
    run = create_recommendation_run(
        db,
        RecommendationRun(
            run_id=run_id,
            source_theme_run_id=source_theme_run,
            source_candidate_run_id=candidate_run.id if candidate_run else None,
            status="running",
            theme_count=len(themes),
        ),
    )
    if not themes:
        run.status = "insufficient_data"
        run.error_code = "RECOMMENDATION_INSUFFICIENT_DATA"
        run.error_message = "추천 후보를 만들 테마가 없습니다."
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.time() - started) * 1000)
        update_recommendation_run(db, run)
        _log_recommendation_error(db, run.error_code, "InsufficientData", run.error_message, {"run_id": run_id, "theme_id": theme_id, "candidate_count": 0, "selected_count": 0})
        return _run_response(run)

    totals = Counter()
    try:
        for theme in themes:
            rows = list_candidate_rows_for_theme(db, theme.id)
            theme_row = create_theme_recommendation(
                db,
                ThemeRecommendation(
                    recommendation_run_id=run.id,
                    market_theme_id=theme.id,
                    theme_name=theme.theme_name,
                    theme_score=theme.calculated_score or 0.0,
                    confidence_score=theme.confidence_score or 0.0,
                    impact_direction=theme.impact_direction,
                    recommendation_summary=f"{theme.theme_name} 관련 분석 기반 관심 후보입니다.",
                    risk_summary=", ".join(_safe_list(theme.risk_factors_json)[:5]) or None,
                ),
            )
            scored, excluded = _score_rows(
                rows,
                theme,
                include_leveraged_inverse_etfs=include_leveraged_inverse_etfs,
                min_candidate_score=min_candidate_score,
                min_evidence_score=min_evidence_score,
                diversify_country=diversify_country,
                stock_country_scope=stock_country_scope,
            )
            stocks = [item for item in scored if item.security.asset_type == "stock"][:max_stocks_per_theme]
            etfs = [item for item in scored if item.security.asset_type == "etf"][:max_etfs_per_theme]
            selected_ids = {item.candidate.id for item in stocks + etfs}
            lower_ranked = [
                (item.candidate, item.security, ["lower_ranked_alternative"])
                for item in scored
                if item.candidate.id not in selected_ids
            ]
            for rank, item in enumerate(stocks, start=1):
                create_recommendation_item(db, _item_from_scored(theme_row, theme, item, rank))
            for rank, item in enumerate(etfs, start=1):
                create_recommendation_item(db, _item_from_scored(theme_row, theme, item, rank))
            for candidate, security, flags in (excluded + lower_ranked)[:10]:
                create_recommendation_item(db, _excluded_item(theme_row, theme, candidate, security, flags))
            theme_row.stock_count = len(stocks)
            theme_row.etf_count = len(etfs)
            db.add(theme_row)
            db.commit()
            totals["stock"] += len(stocks)
            totals["etf"] += len(etfs)
            totals["excluded"] += min(len(excluded + lower_ranked), 10)
        run.recommended_stock_count = int(totals["stock"])
        run.recommended_etf_count = int(totals["etf"])
        run.excluded_count = int(totals["excluded"])
        if run.recommended_stock_count + run.recommended_etf_count == 0:
            run.status = "insufficient_candidates"
            run.error_code = "RECOMMENDATION_INSUFFICIENT_CANDIDATES"
            run.error_message = "선정 기준을 통과한 관심 후보가 없습니다."
            _log_recommendation_error(db, run.error_code, "InsufficientCandidates", run.error_message, {"run_id": run_id, "theme_id": theme_id, "candidate_count": sum(len(list_candidate_rows_for_theme(db, t.id)) for t in themes), "selected_count": 0})
        else:
            run.status = "completed"
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.error_code = "RECOMMENDATION_ERROR"
        run.error_message = "추천 후보 선정 중 오류가 발생했습니다."
        _log_recommendation_error(db, run.error_code, type(exc).__name__, run.error_message, {"run_id": run_id, "theme_id": theme_id, "safe_error_type": type(exc).__name__})
    run.completed_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.time() - started) * 1000)
    update_recommendation_run(db, run)
    return _run_response(run)


def _run_response(run: RecommendationRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "theme_count": run.theme_count,
        "recommended_stock_count": run.recommended_stock_count,
        "recommended_etf_count": run.recommended_etf_count,
        "excluded_count": run.excluded_count,
        "duration_ms": run.duration_ms,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def recommendation_run_response(db: Session, run: RecommendationRun | None) -> dict[str, Any]:
    if run is None:
        return {"run": None, "themes": []}
    themes: list[dict[str, Any]] = []
    for theme_row in list_theme_recommendations(db, run.id):
        items = list_recommendation_items(db, theme_row.id)
        selected = [item for item in items if item.is_selected]
        excluded = [item for item in items if item.is_excluded]
        selected_dicts = [_item_to_dict(item) for item in selected]
        excluded_dicts = [_item_to_dict(item) for item in excluded]
        theme = get_market_theme(db, theme_row.market_theme_id)
        diagnostics = theme_candidate_diagnostics(db, theme, selected_items=selected_dicts) if theme is not None else {}
        overseas_reference = [
            item
            for item in excluded_dicts + selected_dicts
            if "overseas_reference_stock" in (item.get("exclusion_flags") or [])
            or (item.get("country_code") == "US" and item.get("asset_type") in {"stock", "etf"} and item.get("rank", 0))
        ]
        themes.append(
            {
                "theme_id": theme_row.market_theme_id,
                "theme_name": theme_row.theme_name,
                "recommendation_summary": theme_row.recommendation_summary,
                "risk_summary": theme_row.risk_summary,
                "candidate_diagnostics": diagnostics,
                "domestic_stocks": [item for item in selected_dicts if item.get("asset_type") == "stock" and item.get("country_code") == "KR"],
                "domestic_etfs": [item for item in selected_dicts if item.get("asset_type") == "etf" and item.get("country_code") == "KR"],
                "overseas_reference": overseas_reference,
                "excluded": [item for item in excluded_dicts if item not in overseas_reference],
                "stocks": [item for item in selected_dicts if item.get("asset_type") == "stock"],
                "etfs": [item for item in selected_dicts if item.get("asset_type") == "etf"],
            }
        )
    return {"run": _run_response(run), "themes": themes}


def _item_to_dict(item: RecommendationItem) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "asset_type": item.asset_type,
        "country_code": item.country_code,
        "ticker": item.ticker,
        "security_name": item.security_name,
        "exchange_code": item.exchange_code,
        "final_score": item.final_score,
        "candidate_score": item.candidate_score,
        "evidence_score": item.evidence_score,
        "risk_penalty_score": item.risk_penalty_score,
        "selection_reason": item.selection_reason,
        "evidence_summary": item.evidence_summary,
        "risk_flags": item.risk_flags_json or [],
        "exclusion_flags": item.exclusion_flags_json or [],
        "excluded_reason": item.excluded_reason,
    }


def latest_recommendations(db: Session) -> dict[str, Any]:
    ensure_recommendation_tables_schema(db)
    return recommendation_run_response(db, get_latest_recommendation_run(db))


def recommendation_run_detail(db: Session, run_id: str) -> dict[str, Any] | None:
    ensure_recommendation_tables_schema(db)
    run = get_recommendation_run_by_run_id(db, run_id)
    return None if run is None else recommendation_run_response(db, run)


def recommendation_runs(db: Session, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    ensure_recommendation_tables_schema(db)
    return [_run_response(run) for run in list_recommendation_runs(db, limit=limit, offset=offset)]


def theme_recommendations(db: Session, theme_id: int) -> dict[str, Any] | None:
    ensure_recommendation_tables_schema(db)
    theme_row = get_latest_theme_recommendation(db, theme_id)
    if theme_row is None:
        return None
    run = db.get(RecommendationRun, theme_row.recommendation_run_id)
    return recommendation_run_response(db, run)
