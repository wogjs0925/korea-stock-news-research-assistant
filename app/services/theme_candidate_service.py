from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.models.market_theme import MarketTheme
from app.models.news_analysis import NewsAnalysis
from app.models.security import Security
from app.models.security_alias import SecurityAlias
from app.models.etf_holding import ETFHolding
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_candidate_run import ThemeCandidateRun
from app.models.theme_news_link import ThemeNewsLink
from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.repositories.security_repository import (
    create_theme_candidate_run,
    ensure_security_tables_schema,
    list_theme_candidates,
    save_theme_candidate,
    update_theme_candidate_run,
)
from app.repositories.theme_analysis_repository import get_latest_theme_run, get_market_theme, list_themes_for_run
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log
from app.services.security_match_service import match_security
from app.utils.security_names import normalize_company_name, normalize_ticker

logger = logging.getLogger(__name__)


@dataclass
class CandidateScores:
    theme_fit_score: float
    match_score: float
    evidence_score: float
    relevance_score: float
    liquidity_proxy_score: float
    risk_penalty_score: float
    final_candidate_score: float


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def calculate_candidate_scores(
    *,
    theme: MarketTheme,
    match_score: float,
    evidence_count: int,
    relevance_score: float,
    risk_penalty_score: float,
) -> CandidateScores:
    theme_score = _clamp(((theme.calculated_score or 0.0) + (theme.confidence_score or 0.0)) / 2)
    evidence_score = _clamp(min(evidence_count, 5) / 5)
    liquidity_proxy_score = 0.5
    final_score = (
        theme_score * 0.20
        + _clamp(match_score) * 0.25
        + evidence_score * 0.25
        + _clamp(relevance_score) * 0.20
        + liquidity_proxy_score * 0.10
        - _clamp(risk_penalty_score) * 0.10
    )
    return CandidateScores(
        theme_fit_score=theme_score,
        match_score=_clamp(match_score),
        evidence_score=evidence_score,
        relevance_score=_clamp(relevance_score),
        liquidity_proxy_score=liquidity_proxy_score,
        risk_penalty_score=_clamp(risk_penalty_score),
        final_candidate_score=_clamp(final_score),
    )


def _safe_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _keywords_from_text(*parts: str | None) -> list[str]:
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for token in re.split(r"[^0-9A-Za-z가-힣]+", part.lower()):
            if len(token) >= 2:
                tokens.append(token)
    seen: set[str] = set()
    return [token for token in tokens if not (token in seen or seen.add(token))][:20]


def _theme_search_tags(theme: MarketTheme) -> list[str]:
    tags: list[str] = []
    for field_name in [
        "candidate_search_tags_json",
        "market_theme_tags_json",
        "direct_impact_industries_json",
        "issue_tags_json",
        "related_industries_json",
    ]:
        tags.extend(str(item) for item in _safe_json(getattr(theme, field_name, [])) if isinstance(item, str))
    for row in _safe_json(getattr(theme, "entity_business_industries_json", [])):
        if isinstance(row, dict):
            tags.extend(str(item) for item in row.get("industries", []) if isinstance(item, str))
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = normalize_company_name(tag)
        if key and key not in seen:
            seen.add(key)
            result.append(tag)
    return result[:40]


def _theme_evidence_rows(db: Session, theme_id: int) -> list[NewsAnalysis]:
    query = (
        select(NewsAnalysis)
        .join(ThemeNewsLink, ThemeNewsLink.news_analysis_id == NewsAnalysis.id)
        .where(ThemeNewsLink.market_theme_id == theme_id)
    )
    return list(db.scalars(query).all())


def _collect_company_names(theme: MarketTheme, analyses: list[NewsAnalysis]) -> list[str]:
    companies: list[str] = []
    companies.extend(str(item) for item in _safe_json(theme.related_companies_json) if isinstance(item, str))
    for analysis in analyses:
        for item in _safe_json(analysis.companies_json):
            if isinstance(item, dict) and item.get("company_name"):
                companies.append(str(item["company_name"]))
    seen: set[str] = set()
    result: list[str] = []
    for company in companies:
        key = normalize_company_name(company)
        if key and key not in seen:
            seen.add(key)
            result.append(company.strip())
    return result


def _risk_flags(security: Security | None, match_status: str, evidence_count: int) -> list[str]:
    flags: list[str] = []
    if match_status == "ambiguous":
        flags.append("ambiguous_match")
    if evidence_count <= 1:
        flags.append("limited_evidence")
    if security is not None:
        if security.is_leveraged:
            flags.append("leveraged_etf")
        if security.is_inverse:
            flags.append("inverse_etf")
        if not security.is_recommendation_eligible:
            flags.append("not_recommendation_eligible")
    return flags


def _risk_penalty(flags: list[str], theme: MarketTheme) -> float:
    penalty = 0.0
    if "ambiguous_match" in flags:
        penalty += 0.20
    if "limited_evidence" in flags:
        penalty += 0.10
    if "leveraged_etf" in flags:
        penalty += 0.30
    if "inverse_etf" in flags:
        penalty += 0.30
    if theme.impact_direction in {"negative", "mixed"}:
        penalty += 0.10
    return _clamp(penalty)


def _log_candidate_error(db: Session, code: str, error_type: str, message: str, context: dict[str, Any]) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=code,
                severity="ERROR",
                component="theme_candidate_engine",
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except Exception as exc:
        logger.warning("failed to write theme candidate error: %s", type(exc).__name__)


def _eligible_security(db: Session, security_id: int | None) -> Security | None:
    if security_id is None:
        return None
    return db.scalar(
        select(Security).where(
            Security.id == security_id,
            Security.is_active == True,
            Security.is_recommendation_eligible == True,
            Security.country_code.in_(["KR", "US"]),
            Security.asset_type.in_(["stock", "etf"]),
        )
    )


def _theme_candidate_eligible(theme: MarketTheme, *, include_watchlist_themes: bool) -> bool:
    bucket = getattr(theme, "theme_bucket", None) or "low_actionability"
    if bool(getattr(theme, "is_investable_theme", False)) or bucket == "investable_opportunity":
        return theme.impact_direction != "negative"
    if include_watchlist_themes and bucket == "watchlist":
        return theme.impact_direction != "negative"
    return False


def _save_candidate(
    db: Session,
    *,
    theme: MarketTheme,
    security: Security | None,
    source_name: str,
    source_keyword: str | None,
    source_type: str,
    match_score: float,
    match_method: str,
    match_status: str,
    evidence_count: int,
    relevance_score: float,
    reason_summary: str,
) -> ThemeSecurityCandidate:
    flags = _risk_flags(security, match_status, evidence_count)
    scores = calculate_candidate_scores(
        theme=theme,
        match_score=match_score,
        evidence_count=evidence_count,
        relevance_score=relevance_score,
        risk_penalty_score=_risk_penalty(flags, theme),
    )
    return save_theme_candidate(
        db,
        ThemeSecurityCandidate(
            market_theme_id=theme.id,
            security_id=security.id if security is not None else None,
            source_company_name=source_name[:256],
            source_keyword=(source_keyword or "")[:256] or None,
            source_type=source_type,
            match_score=scores.match_score,
            relevance_score=scores.relevance_score,
            theme_fit_score=scores.theme_fit_score,
            evidence_score=scores.evidence_score,
            liquidity_proxy_score=scores.liquidity_proxy_score,
            risk_penalty_score=scores.risk_penalty_score,
            final_candidate_score=scores.final_candidate_score,
            match_method=match_method,
            match_status=match_status,
            country_code=security.country_code if security is not None else None,
            asset_type=security.asset_type if security is not None else None,
            evidence_count=evidence_count,
            reason_summary=reason_summary[:1000],
            matched_evidence_json=[{"source": source_type, "keyword": source_keyword, "evidence_count": evidence_count}],
            risk_flags_json=flags,
            updated_at=datetime.now(timezone.utc),
        ),
    )


def _generate_company_candidates(
    db: Session,
    theme: MarketTheme,
    analyses: list[NewsAnalysis],
    max_candidates: int = 15,
) -> Counter:
    counts: Counter = Counter()
    evidence_count = max(theme.evidence_count or 0, len(analyses))
    for company in _collect_company_names(theme, analyses)[:max_candidates]:
        matches = match_security(db, company, asset_type="stock", limit=3)
        top = matches[0] if matches else None
        if top is None or top.ambiguity_status == "unmatched":
            _save_candidate(
                db,
                theme=theme,
                security=None,
                source_name=company,
                source_keyword=company,
                source_type="company_name",
                match_score=top.match_score if top else 0.0,
                match_method=top.match_method if top else "none",
                match_status="unmatched",
                evidence_count=evidence_count,
                relevance_score=0.2,
                reason_summary="저장된 종목 마스터에서 일치하는 상장 종목을 찾지 못했습니다.",
            )
            counts["unmatched"] += 1
            continue
        security = _eligible_security(db, top.security_id)
        if security is None:
            counts["unmatched"] += 1
            continue
        status = "ambiguous" if top.ambiguity_status == "ambiguous" else "matched"
        _save_candidate(
            db,
            theme=theme,
            security=security,
            source_name=company,
            source_keyword=company,
            source_type="company_name",
            match_score=top.match_score,
            match_method=top.match_method,
            match_status=status,
            evidence_count=evidence_count,
            relevance_score=0.9,
            reason_summary="테마 관련 회사명이 실제 종목 마스터와 매칭되었습니다.",
        )
        counts["ambiguous" if status == "ambiguous" else "stock"] += 1
    return counts


def _search_etfs(
    db: Session,
    keywords: list[str],
    include_leveraged_inverse: bool,
    holding_names: list[str] | None = None,
) -> list[tuple[Security, str, float, str]]:
    results: dict[int, tuple[Security, str, float, str]] = {}
    for keyword in keywords:
        normalized = normalize_company_name(keyword)
        if not normalized:
            continue
        query = (
            select(Security)
            .outerjoin(SecurityAlias, SecurityAlias.security_id == Security.id)
            .where(
                Security.is_active == True,
                Security.is_recommendation_eligible == True,
                Security.country_code.in_(["KR", "US"]),
                Security.asset_type == "etf",
                or_(
                    Security.name.contains(keyword),
                    Security.english_name.contains(keyword),
                    Security.normalized_name.contains(normalized),
                    Security.issuer_name.contains(keyword),
                    Security.industry.contains(keyword),
                    Security.sector.contains(keyword),
                    SecurityAlias.normalized_alias.contains(normalized),
                ),
            )
            .limit(50)
        )
        for security in db.scalars(query).all():
            if not include_leveraged_inverse and (security.is_leveraged or security.is_inverse):
                continue
            score = 0.65
            haystack = " ".join(
                str(part or "").lower()
                for part in [security.name, security.english_name, security.normalized_name, security.issuer_name, security.industry, security.sector]
            )
            if keyword.lower() in haystack:
                score = 0.85
            current = results.get(security.id)
            if current is None or score > current[2]:
                results[security.id] = (security, keyword, score, "tag_or_alias")
    for holding_name in holding_names or []:
        normalized = normalize_company_name(holding_name)
        ticker = normalize_ticker(holding_name)
        if not normalized and not ticker:
            continue
        rows = db.execute(
            select(Security, ETFHolding)
            .join(ETFHolding, ETFHolding.etf_security_id == Security.id)
            .where(
                Security.is_active == True,
                Security.is_recommendation_eligible == True,
                Security.country_code.in_(["KR", "US"]),
                Security.asset_type == "etf",
                or_(
                    ETFHolding.holding_ticker == ticker,
                    ETFHolding.holding_name.contains(holding_name),
                    ETFHolding.holding_name.contains(normalized),
                ),
            )
            .limit(50)
        ).all()
        for security, holding in rows:
            if not include_leveraged_inverse and (security.is_leveraged or security.is_inverse):
                continue
            exposure = min(1.0, float(holding.weight or 0.0) / 20.0) if holding.weight is not None else 0.35
            score = max(0.72, min(0.95, 0.70 + exposure * 0.25))
            current = results.get(security.id)
            if current is None or score > current[2]:
                results[security.id] = (security, holding_name, score, "holding_exposure")
    return sorted(results.values(), key=lambda item: item[2], reverse=True)


def _generate_etf_candidates(
    db: Session,
    theme: MarketTheme,
    analyses: list[NewsAnalysis],
    include_leveraged_inverse: bool,
    max_candidates: int = 20,
) -> Counter:
    counts: Counter = Counter()
    keywords = _theme_search_tags(theme)
    keywords.extend(_keywords_from_text(theme.theme_name, theme.theme_summary, theme.why_now))
    holding_names = _collect_company_names(theme, analyses)
    evidence_count = max(theme.evidence_count or 0, len(analyses))
    for security, keyword, relevance, source_type in _search_etfs(db, keywords, include_leveraged_inverse, holding_names)[:max_candidates]:
        _save_candidate(
            db,
            theme=theme,
            security=security,
            source_name=security.name,
            source_keyword=keyword,
            source_type="etf_holding" if source_type == "holding_exposure" else "etf_name",
            match_score=0.75,
            match_method="holding_exposure_etf" if source_type == "holding_exposure" else "keyword_etf",
            match_status="matched",
            evidence_count=evidence_count,
            relevance_score=relevance,
            reason_summary="테마/산업 키워드가 ETF명 또는 ETF 별칭과 일치했습니다.",
        )
        counts["etf"] += 1
    return counts


def _generate_weak_industry_candidates(db: Session, theme: MarketTheme, analyses: list[NewsAnalysis]) -> Counter:
    counts: Counter = Counter()
    keywords = _theme_search_tags(theme)[:8]
    if not keywords:
        return counts
    evidence_count = max(theme.evidence_count or 0, len(analyses))
    for keyword in keywords:
        query = (
            select(Security)
            .where(
                Security.is_active == True,
                Security.is_recommendation_eligible == True,
                Security.country_code.in_(["KR", "US"]),
                Security.asset_type == "stock",
                or_(Security.industry.contains(keyword), Security.sector.contains(keyword)),
            )
            .limit(10)
        )
        for security in db.scalars(query).all():
            _save_candidate(
                db,
                theme=theme,
                security=security,
                source_name=security.name,
                source_keyword=keyword,
                source_type="theme_industry",
                match_score=0.45,
                match_method="weak_industry_keyword",
                match_status="matched",
                evidence_count=evidence_count,
                relevance_score=0.45,
                reason_summary="회사명이 직접 언급되지 않은 약한 산업 기반 후보입니다.",
            )
            counts["stock"] += 1
    return counts


def generate_theme_candidates(
    db: Session,
    *,
    theme_run_id: int | None = None,
    theme_id: int | None = None,
    include_weak_industry_candidates: bool = False,
    include_watchlist_themes: bool = False,
    include_leveraged_inverse_etfs: bool = True,
    max_stock_candidates_per_theme: int = 15,
    max_etf_candidates_per_theme: int = 20,
) -> dict[str, Any]:
    ensure_security_tables_schema(db)
    started = time.time()
    run_id = f"THEMECAND-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    latest_run = None if theme_run_id else _latest_completed_theme_run(db)
    selected_theme_run_id = theme_run_id or (latest_run.id if latest_run else None)
    if theme_id is not None:
        theme = get_market_theme(db, theme_id)
        themes = [theme] if theme is not None else []
        selected_theme_run_id = theme.theme_run_id if theme is not None else selected_theme_run_id
    elif selected_theme_run_id is not None:
        themes = list_themes_for_run(db, selected_theme_run_id)
    else:
        themes = []

    run = create_theme_candidate_run(
        db,
        ThemeCandidateRun(run_id=run_id, theme_run_id=selected_theme_run_id, status="running", theme_count=len(themes)),
    )
    if not themes:
        run.status = "insufficient_data"
        run.error_message = "후보를 생성할 completed 테마가 없습니다."
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.time() - started) * 1000)
        update_theme_candidate_run(db, run)
        _log_candidate_error(
            db,
            "THEME_CANDIDATE_INSUFFICIENT_DATA",
            "InsufficientData",
            run.error_message,
            {"run_id": run_id, "theme_id": theme_id, "theme_run_id": theme_run_id, "candidate_count": 0},
        )
        return _run_response(run)

    counts: Counter = Counter()
    try:
        for theme in themes:
            if not _theme_candidate_eligible(theme, include_watchlist_themes=include_watchlist_themes):
                continue
            analyses = _theme_evidence_rows(db, theme.id)
            counts.update(_generate_company_candidates(db, theme, analyses, max_stock_candidates_per_theme))
            counts.update(_generate_etf_candidates(db, theme, analyses, include_leveraged_inverse_etfs, max_etf_candidates_per_theme))
            if include_weak_industry_candidates:
                counts.update(_generate_weak_industry_candidates(db, theme, analyses))
        run.status = "completed"
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.error_message = "테마 후보 생성 중 오류가 발생했습니다."
        _log_candidate_error(
            db,
            "THEME_CANDIDATE_ERROR",
            type(exc).__name__,
            run.error_message,
            {"run_id": run_id, "theme_id": theme_id, "theme_run_id": selected_theme_run_id, "candidate_count": sum(counts.values())},
        )
    run.theme_count = len(themes)
    run.stock_candidate_count = int(counts["stock"])
    run.etf_candidate_count = int(counts["etf"])
    run.ambiguous_count = int(counts["ambiguous"])
    run.unmatched_count = int(counts["unmatched"])
    run.completed_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.time() - started) * 1000)
    update_theme_candidate_run(db, run)
    return _run_response(run)


def _run_response(run: ThemeCandidateRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "theme_count": run.theme_count,
        "stock_candidate_count": run.stock_candidate_count,
        "etf_candidate_count": run.etf_candidate_count,
        "ambiguous_count": run.ambiguous_count,
        "unmatched_count": run.unmatched_count,
        "duration_ms": run.duration_ms,
    }


def theme_candidate_diagnostics(db: Session, theme: MarketTheme, *, selected_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = list_theme_candidates(db, theme.id, limit=1000)
    total = len(rows)
    matched = ambiguous = unmatched = excluded = 0
    domestic_stock = us_stock = domestic_etf = us_etf = 0
    selected_domestic_stock = selected_domestic_etf = overseas_reference = 0
    low_score = insufficient_evidence = 0
    for candidate, security in rows:
        if candidate.match_status == "matched":
            matched += 1
        elif candidate.match_status == "ambiguous":
            ambiguous += 1
        elif candidate.match_status == "unmatched":
            unmatched += 1
        if security is None or candidate.match_status != "matched":
            excluded += 1
        if security is not None:
            if security.country_code == "KR" and security.asset_type == "stock":
                domestic_stock += 1
            elif security.country_code == "US" and security.asset_type == "stock":
                us_stock += 1
            elif security.country_code == "KR" and security.asset_type == "etf":
                domestic_etf += 1
            elif security.country_code == "US" and security.asset_type == "etf":
                us_etf += 1
        if (candidate.final_candidate_score or 0.0) < 0.35:
            low_score += 1
        if (candidate.evidence_score or 0.0) < 0.1 and (candidate.evidence_count or 0) <= 0:
            insufficient_evidence += 1

    for item in selected_items or []:
        if item.get("country_code") == "KR" and item.get("asset_type") == "stock":
            selected_domestic_stock += 1
        elif item.get("country_code") == "KR" and item.get("asset_type") == "etf":
            selected_domestic_etf += 1
        elif item.get("country_code") == "US":
            overseas_reference += 1

    reasons: list[str] = []
    bucket = getattr(theme, "theme_bucket", None) or "low_actionability"
    is_theme_eligible = _theme_candidate_eligible(theme, include_watchlist_themes=False)
    if theme.impact_direction == "negative":
        reasons.append("negative_theme_excluded")
    if bucket in {"risk_alert", "macro_background", "low_actionability"}:
        reasons.append(f"{bucket}_excluded")
    if bucket == "watchlist" and not is_theme_eligible:
        reasons.append("watchlist_not_included")
    if theme.impact_direction == "mixed":
        reasons.append("mixed_theme_risk_penalty")
    if total == 0:
        reasons.append("no_matched_security")
    if not (theme.candidate_search_tags_json or []):
        reasons.append("no_candidate_search_tags")
    if not (theme.issue_tags_json or theme.direct_impact_industries_json or theme.market_theme_tags_json):
        reasons.append("missing_theme_tags")
    if total > 0 and matched == 0:
        reasons.append("no_matched_security")
    if matched == 0 and ambiguous > 0:
        reasons.append("ambiguous_only")
    if domestic_stock == 0 and us_stock > 0:
        reasons.append("only_us_candidates")
    if domestic_stock == 0 and (domestic_etf > 0 or us_etf > 0) and us_stock == 0:
        reasons.append("only_etf_candidates")
    if domestic_stock == 0:
        reasons.append("no_kr_stock_candidate")
    if total > 0 and low_score == total:
        reasons.append("all_candidates_below_score_threshold")
    if total > 0 and insufficient_evidence == total:
        reasons.append("insufficient_evidence")

    unique_reasons = []
    seen = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique_reasons.append(reason)

    return {
        "theme_id": theme.id,
        "theme_name": theme.theme_name,
        "impact_direction": theme.impact_direction,
        "theme_bucket": bucket,
        "theme_bucket_reason": getattr(theme, "theme_bucket_reason", None),
        "actionability_score": float(getattr(theme, "actionability_score", 0.0) or 0.0),
        "price_impact_score": float(getattr(theme, "price_impact_score", 0.0) or 0.0),
        "investable_link_score": float(getattr(theme, "investable_link_score", 0.0) or 0.0),
        "is_investable_theme": bool(getattr(theme, "is_investable_theme", False)),
        "is_candidate_generation_eligible": is_theme_eligible,
        "candidate_exclusion_reason": unique_reasons[0] if unique_reasons else None,
        "candidate_exclusion_reasons": unique_reasons,
        "total_candidate_count": total,
        "matched_candidate_count": matched,
        "domestic_stock_candidate_count": domestic_stock,
        "us_stock_candidate_count": us_stock,
        "domestic_etf_candidate_count": domestic_etf,
        "us_etf_candidate_count": us_etf,
        "ambiguous_candidate_count": ambiguous,
        "unmatched_candidate_count": unmatched,
        "excluded_candidate_count": excluded,
        "selected_domestic_stock_count": selected_domestic_stock,
        "selected_domestic_etf_count": selected_domestic_etf,
        "overseas_reference_count": overseas_reference,
    }


def theme_candidates_for_api(
    db: Session,
    theme_id: int,
    *,
    asset_type: str | None = None,
    match_status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = list_theme_candidates(db, theme_id, asset_type=asset_type, match_status=match_status, limit=limit)
    result: list[dict[str, Any]] = []
    for candidate, security in rows:
        result.append(
            {
                "theme_id": candidate.market_theme_id,
                "theme_name": None,
                "security_id": candidate.security_id,
                "security_key": security.security_key if security else None,
                "ticker": security.ticker if security else None,
                "name": security.name if security else candidate.source_company_name,
                "english_name": security.english_name if security else None,
                "country_code": candidate.country_code,
                "asset_type": candidate.asset_type,
                "exchange_code": security.exchange_code if security else None,
                "final_candidate_score": candidate.final_candidate_score,
                "match_score": candidate.match_score,
                "evidence_score": candidate.evidence_score,
                "relevance_score": candidate.relevance_score,
                "risk_penalty_score": candidate.risk_penalty_score,
                "match_status": candidate.match_status,
                "match_method": candidate.match_method,
                "source_company_name": candidate.source_company_name,
                "source_keyword": candidate.source_keyword,
                "reason_summary": candidate.reason_summary,
                "risk_flags": candidate.risk_flags_json or [],
            }
        )
    return result


def latest_theme_candidates_grouped(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    run = _latest_completed_theme_run(db)
    if run is None:
        return []
    grouped: list[dict[str, Any]] = []
    for theme in list_themes_for_run(db, run.id):
        candidates = theme_candidates_for_api(db, theme.id, limit=limit)
        for item in candidates:
            item["theme_name"] = theme.theme_name
        grouped.append(
            {
                "theme_id": theme.id,
                "theme_name": theme.theme_name,
                "candidate_diagnostics": theme_candidate_diagnostics(db, theme),
                "candidates": candidates,
            }
        )
    return grouped


def _latest_completed_theme_run(db: Session) -> ThemeAnalysisRun | None:
    return db.scalar(
        select(ThemeAnalysisRun)
        .where(ThemeAnalysisRun.status == "completed")
        .order_by(desc(ThemeAnalysisRun.completed_at), desc(ThemeAnalysisRun.id))
        .limit(1)
    )
