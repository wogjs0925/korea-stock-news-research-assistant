from __future__ import annotations

import asyncio
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.market_theme import MarketTheme
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_news_link import ThemeNewsLink
from app.providers.ai.mock_theme_analyzer import MockThemeAnalyzer
from app.providers.ai.openai_news_analyzer import (
    OpenAIAPIKeyMissingError,
    OpenAIConfigurationError,
    OpenAIInvalidRequestError,
    OpenAIQuotaExceededError,
    OpenAIRateLimitError,
    OpenAIResponseError,
)
from app.providers.ai.openai_theme_analyzer import OpenAIThemeAnalyzer
from app.repositories.theme_analysis_repository import (
    create_market_theme,
    create_theme_analysis_run,
    create_theme_news_link,
    ensure_theme_tables_schema,
    list_theme_source_analyses,
    update_theme_analysis_run,
)
from app.schemas.error import ErrorLogCreate
from app.schemas.theme_analysis import SelectedThemeCandidate, ThemeSelectionOutput
from app.services.app_setting_service import runtime_openai_model
from app.services.error_service import create_error_log
from app.services.theme_actionability_service import score_theme_actionability
from app.services.theme_tag_enrichment_service import build_tag_confidence, enrich_theme_tags

settings = get_settings()

SAFE_THEME_OPENAI_CONTEXT_KEYS = {
    "http_status_code",
    "request_id",
    "original_exception_type",
    "original_error_code",
    "original_error_type",
    "original_param",
    "model_name",
    "schema_name",
    "input_item_count",
    "prompt_version",
    "response_status",
    "incomplete_reason",
    "has_refusal",
    "has_output_parsed",
    "has_output_text",
    "output_text_length",
    "retryable",
}

THEME_ERROR_MESSAGES = {
    "OPENAI_AUTH_ERROR": "OpenAI 인증 오류가 발생했습니다.",
    "OPENAI_MODEL_CONFIG_ERROR": "OpenAI 모델 설정이 올바르지 않습니다.",
    "OPENAI_RATE_LIMIT_ERROR": "OpenAI API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
    "OPENAI_QUOTA_ERROR": "OpenAI API 사용 가능 잔액 또는 결제 한도를 확인하세요.",
    "OPENAI_CONNECTION_ERROR": "OpenAI API 연결 오류가 발생했습니다.",
    "OPENAI_TIMEOUT_ERROR": "OpenAI 요청 시간이 초과되었습니다.",
    "OPENAI_OUTPUT_PARSED_NONE": "OpenAI 응답에 구조화된 테마 결과가 없습니다.",
    "OPENAI_SCHEMA_VALIDATION_ERROR": "테마 분석 결과가 스키마 검증을 통과하지 못했습니다.",
    "OPENAI_INCOMPLETE": "테마 분석 응답이 중간에 중단됐습니다.",
    "OPENAI_MAX_OUTPUT_TOKENS": "테마 분석 응답이 출력 토큰 제한으로 중단되었습니다.",
    "THEME_ANALYSIS_EMPTY_THEMES": "테마 분석 결과에 저장 가능한 테마가 없습니다.",
    "OPENAI_RESPONSE_ERROR": "OpenAI API 응답 오류가 발생했습니다.",
}


def normalize_theme_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _as_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def calculate_theme_score(theme: SelectedThemeCandidate, source_by_id: dict[int, dict[str, Any]], window_end: datetime) -> float:
    evidence_sources = [source_by_id[e.news_analysis_id] for e in theme.evidence if e.news_analysis_id in source_by_id]
    if not evidence_sources:
        return 0.0
    n = len(evidence_sources)
    avg_importance = sum(float(s.get("importance_score") or 0.0) for s in evidence_sources) / n
    avg_market = sum(float(s.get("market_relevance_score") or 0.0) for s in evidence_sources) / n
    avg_novelty = sum(float(s.get("novelty_score") or 0.0) for s in evidence_sources) / n
    evidence_score = min(1.0, math.log1p(n) / math.log1p(6))
    publishers = {s.get("publisher") for s in evidence_sources if s.get("publisher")}
    publisher_score = min(1.0, len(publishers) / 4)
    recency_values = []
    for source in evidence_sources:
        available_at = source.get("available_at")
        if available_at is None:
            continue
        age_hours = max(0.0, (_as_aware(window_end) - _as_aware(available_at)).total_seconds() / 3600)
        recency_values.append(max(0.0, 1.0 - age_hours / max(1, settings.theme_analysis_window_hours)))
    recency_score = sum(recency_values) / len(recency_values) if recency_values else 0.0
    return _clamp(
        avg_importance * 0.30
        + avg_market * 0.25
        + avg_novelty * 0.15
        + evidence_score * 0.15
        + publisher_score * 0.10
        + recency_score * 0.05
    )


def _theme_actionability(
    theme: SelectedThemeCandidate,
    source_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    evidence_sources = [source_by_id[e.news_analysis_id] for e in theme.evidence if e.news_analysis_id in source_by_id]
    source_scores = [
        {
            "price_impact_score": float(source.get("price_impact_score") or 0.0),
            "investable_link_score": float(source.get("investable_link_score") or 0.0),
        }
        for source in evidence_sources
    ]
    return score_theme_actionability(
        theme_name=theme.theme_name,
        theme_summary=theme.theme_summary,
        why_now=theme.why_now,
        impact_direction=theme.impact_direction,
        issue_tags=theme.issue_tags,
        direct_impact_industries=theme.direct_impact_industries,
        market_theme_tags=theme.market_theme_tags,
        candidate_search_tags=theme.candidate_search_tags,
        related_companies=theme.related_companies,
        evidence_count=len(theme.evidence),
        source_scores=source_scores,
    )


def _theme_bucket_rank(bucket: str) -> int:
    return {
        "investable_opportunity": 4,
        "watchlist": 3,
        "macro_background": 2,
        "risk_alert": 1,
        "low_actionability": 0,
    }.get(bucket, 0)


def validate_theme_output(output: ThemeSelectionOutput, sources: list[dict[str, Any]]) -> ThemeSelectionOutput:
    source_by_id = {int(source["news_analysis_id"]): source for source in sources}
    allowed_ids = set(source_by_id)
    allowed_companies = {
        company
        for source in sources
        for company in source.get("companies", [])
        if isinstance(company, str) and company
    }
    themes: list[SelectedThemeCandidate] = []
    seen_names: set[str] = set()
    for theme in output.themes:
        normalized_name = normalize_theme_name(theme.theme_name)
        if not normalized_name or normalized_name in seen_names:
            continue
        evidence_by_id = {}
        for evidence in theme.evidence:
            if evidence.news_analysis_id in allowed_ids and evidence.news_analysis_id not in evidence_by_id:
                evidence_by_id[evidence.news_analysis_id] = evidence
        evidence = list(evidence_by_id.values())
        if len(evidence) < min(2, len(sources)):
            continue
        related_companies = [company for company in _unique(theme.related_companies) if company in allowed_companies]
        themes.append(
            theme.model_copy(
                update={
                    "theme_name": theme.theme_name.strip(),
                    "related_industries": _unique(theme.related_industries)[:10],
                    "related_companies": related_companies[:15],
                    "evidence": evidence[:20],
                    "risk_factors": _unique(theme.risk_factors)[:5],
                }
            )
        )
        seen_names.add(normalized_name)
        if len(themes) >= settings.theme_analysis_max_themes:
            break
    return ThemeSelectionOutput(
        market_overview=output.market_overview,
        themes=themes,
        insufficient_data_reason=output.insufficient_data_reason,
    )


def _provider_for(name: str):
    if name == "mock":
        return MockThemeAnalyzer()
    return OpenAIThemeAnalyzer()


def _log_theme_error(db: Session, error_code: str, error_type: str, message: str, context: dict[str, Any]) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=error_code,
                severity="ERROR",
                component="theme_ai_analyzer",
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except Exception:
        pass


def _theme_error_code(exc: Exception) -> str:
    if isinstance(exc, ThemeAnalysisEmptyThemesError):
        return ThemeAnalysisEmptyThemesError.error_code
    if isinstance(exc, (OpenAIConfigurationError, OpenAIInvalidRequestError, OpenAIResponseError)):
        return getattr(exc, "error_code", "OPENAI_RESPONSE_ERROR")
    return "THEME_ANALYSIS_ERROR"


def _safe_theme_openai_context(exc: Exception) -> dict[str, Any]:
    diagnostics = getattr(exc, "safe_context", None) or getattr(exc, "diagnostic_context", {}) or {}
    return {key: diagnostics.get(key) for key in SAFE_THEME_OPENAI_CONTEXT_KEYS if key in diagnostics}


class ThemeAnalysisEmptyThemesError(RuntimeError):
    error_code = "THEME_ANALYSIS_EMPTY_THEMES"
    safe_message = "테마 분석 결과에 저장 가능한 테마가 없습니다."


def run_theme_analysis(
    db: Session,
    window_hours: int | None = None,
    max_sources: int | None = None,
    provider: str = "openai",
    force: bool = False,
) -> dict[str, Any]:
    ensure_theme_tables_schema(db)
    window_hours = window_hours or settings.theme_analysis_window_hours
    max_sources = max_sources or settings.theme_analysis_max_sources
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)
    run_id = f"THEME-{window_end.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    start = time.time()
    model_name = runtime_openai_model() or settings.openai_model

    sources = list_theme_source_analyses(
        db,
        window_start,
        window_end,
        settings.theme_analysis_min_importance,
        settings.theme_analysis_min_market_relevance,
        max_sources,
    )
    run = create_theme_analysis_run(
        db,
        ThemeAnalysisRun(
            run_id=run_id,
            model_name=model_name,
            prompt_version=settings.theme_analysis_prompt_version,
            window_start=window_start,
            window_end=window_end,
            requested_source_count=max_sources,
            selected_source_count=len(sources),
            status="running",
            started_at=window_end,
        ),
    )

    if len(sources) == 0:
        run.status = "insufficient_data"
        run.insufficient_data_reason = "No eligible completed news analyses were found."
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.time() - start) * 1000)
        update_theme_analysis_run(db, run)
        return _run_response(run, [])

    try:
        analyzer = _provider_for(provider)
        output, meta = asyncio.run(analyzer.analyze(sources, window_start, window_end))
        output = validate_theme_output(output, sources)
        output = output.model_copy(
            update={"themes": [enrich_theme_tags(theme, sources) for theme in output.themes]}
        )
        source_by_id = {int(source["news_analysis_id"]): source for source in sources}
        scored_themes = []
        for theme in output.themes[: settings.theme_analysis_max_themes]:
            base_score = calculate_theme_score(theme, source_by_id, window_end)
            actionability = _theme_actionability(theme, source_by_id)
            final_score = _clamp(base_score * 0.35 + float(actionability["actionability_score"]) * 0.65)
            scored_themes.append((theme, final_score, actionability))
        ranked = sorted(
            scored_themes,
            key=lambda item: (
                _theme_bucket_rank(str(item[2]["theme_bucket"])),
                float(item[2]["price_impact_score"]),
                float(item[2]["investable_link_score"]),
                len(item[0].evidence),
                item[1],
                item[0].confidence_score,
            ),
            reverse=True,
        )[: settings.theme_analysis_max_themes]
        if not ranked:
            raise ThemeAnalysisEmptyThemesError(ThemeAnalysisEmptyThemesError.safe_message)

        theme_ids: list[int] = []
        for rank, (theme, calculated_score, actionability) in enumerate(ranked, start=1):
            evidence_sources = [source_by_id[e.news_analysis_id] for e in theme.evidence if e.news_analysis_id in source_by_id]
            publishers = {s.get("publisher") for s in evidence_sources if s.get("publisher")}
            saved_theme = create_market_theme(
                db,
                MarketTheme(
                    theme_run_id=run.id,
                    rank=rank,
                    theme_name=theme.theme_name,
                    normalized_theme_name=normalize_theme_name(theme.theme_name),
                    theme_summary=theme.theme_summary,
                    why_now=theme.why_now,
                    impact_direction=theme.impact_direction,
                    confidence_score=theme.confidence_score,
                    calculated_score=calculated_score,
                    actionability_score=actionability["actionability_score"],
                    price_impact_score=actionability["price_impact_score"],
                    investable_link_score=actionability["investable_link_score"],
                    is_investable_theme=actionability["is_investable_theme"],
                    theme_bucket=actionability["theme_bucket"],
                    theme_bucket_reason=actionability["theme_bucket_reason"],
                    time_horizon=theme.time_horizon,
                    related_industries_json=theme.related_industries,
                    related_companies_json=theme.related_companies,
                    risk_factors_json=theme.risk_factors,
                    issue_tags_json=theme.issue_tags,
                    direct_impact_industries_json=theme.direct_impact_industries,
                    entity_business_industries_json=[item.model_dump() for item in theme.entity_business_industries],
                    market_theme_tags_json=theme.market_theme_tags,
                    candidate_search_tags_json=theme.candidate_search_tags,
                    tag_confidence_json=build_tag_confidence(theme),
                    evidence_count=len(theme.evidence),
                    source_publisher_count=len(publishers),
                ),
            )
            theme_ids.append(saved_theme.id)
            for evidence in theme.evidence:
                create_theme_news_link(
                    db,
                    ThemeNewsLink(
                        market_theme_id=saved_theme.id,
                        news_analysis_id=evidence.news_analysis_id,
                        relevance_score=evidence.relevance_score,
                        evidence_reason=evidence.reason,
                    ),
                )

        tokens = meta.get("tokens", {})
        if not theme_ids:
            raise ThemeAnalysisEmptyThemesError(ThemeAnalysisEmptyThemesError.safe_message)
        run.status = "completed"
        run.market_overview = output.market_overview
        run.insufficient_data_reason = None
        run.selected_theme_count = len(theme_ids)
        run.input_tokens = tokens.get("input")
        run.output_tokens = tokens.get("output")
        run.total_tokens = tokens.get("total")
        run.latency_ms = meta.get("latency_ms")
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.time() - start) * 1000)
        update_theme_analysis_run(db, run)
        return _run_response(run, theme_ids)
    except Exception as exc:
        error_code = _theme_error_code(exc)
        safe_message = THEME_ERROR_MESSAGES.get(error_code, getattr(exc, "safe_message", None) or "테마 분석 중 오류가 발생했습니다.")
        run.status = "failed"
        run.error_code = error_code
        run.error_message = safe_message
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.time() - start) * 1000)
        update_theme_analysis_run(db, run)
        safe_context = _safe_theme_openai_context(exc)
        _log_theme_error(
            db,
            error_code,
            type(exc).__name__,
            safe_message,
            {
                "run_id": run_id,
                "model_name": model_name,
                "prompt_version": settings.theme_analysis_prompt_version,
                "selected_source_count": len(sources),
                "error_code": error_code,
                "error_message_safe": safe_message,
                **safe_context,
            },
        )
        return _run_response(run, [])


def test_theme_openai(
    db: Session,
    max_sources: int = 5,
    dry_run: bool = True,
) -> dict[str, Any]:
    del dry_run
    ensure_theme_tables_schema(db)
    window_hours = settings.theme_analysis_window_hours
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)
    model_name = runtime_openai_model() or settings.openai_model
    sources = list_theme_source_analyses(
        db,
        window_start,
        window_end,
        settings.theme_analysis_min_importance,
        settings.theme_analysis_min_market_relevance,
        max(3, min(max_sources, 5)),
    )
    if len(sources) < 3:
        return {
            "status": "insufficient_data",
            "selected_source_count": len(sources),
            "model_name": model_name,
            "theme_count": 0,
            "error_code": None,
            "diagnostics": {},
        }
    try:
        analyzer = OpenAIThemeAnalyzer()
        output, meta = asyncio.run(analyzer.analyze(sources, window_start, window_end))
        output = validate_theme_output(output, sources)
        return {
            "status": "completed",
            "selected_source_count": len(sources),
            "model_name": meta.get("model_name") or model_name,
            "theme_count": len(output.themes),
            "response_status": meta.get("response_status"),
            "tokens": meta.get("tokens"),
        }
    except Exception as exc:
        error_code = _theme_error_code(exc)
        safe_message = THEME_ERROR_MESSAGES.get(error_code, getattr(exc, "safe_message", None) or "테마 OpenAI 테스트 중 오류가 발생했습니다.")
        diagnostics = {
            "model_name": model_name,
            "selected_source_count": len(sources),
            "error_code": error_code,
            "error_message_safe": safe_message,
            **_safe_theme_openai_context(exc),
        }
        return {
            "status": "failed",
            "selected_source_count": len(sources),
            "model_name": model_name,
            "theme_count": 0,
            "error_code": error_code,
            "message": safe_message,
            "diagnostics": diagnostics,
        }


def backfill_theme_tags(db: Session) -> dict[str, int]:
    ensure_theme_tables_schema(db)
    from app.models.news_analysis import NewsAnalysis
    from app.models.news_article import NewsArticle
    from app.models.theme_news_link import ThemeNewsLink
    from sqlalchemy import select

    themes = list(db.query(MarketTheme).all())
    scanned = len(themes)
    updated = 0
    skipped = 0
    for theme in themes:
        rows = db.execute(
            select(ThemeNewsLink, NewsAnalysis, NewsArticle)
            .join(NewsAnalysis, NewsAnalysis.id == ThemeNewsLink.news_analysis_id)
            .join(NewsArticle, NewsArticle.id == NewsAnalysis.news_article_id)
            .where(ThemeNewsLink.market_theme_id == theme.id)
        ).all()
        sources = [
            {
                "news_analysis_id": analysis.id,
                "title": article.title,
                "summary": analysis.summary,
                "event_type": analysis.event_type,
                "impact_direction": analysis.impact_direction,
                "market_relevance_score": analysis.market_relevance_score,
                "candidate_themes": analysis.candidate_themes_json or [],
                "companies": [
                    item.get("company_name")
                    for item in (analysis.companies_json or [])
                    if isinstance(item, dict) and item.get("company_name")
                ],
            }
            for _link, analysis, article in rows
        ]
        candidate = SelectedThemeCandidate(
            theme_name=theme.theme_name,
            theme_summary=theme.theme_summary,
            why_now=theme.why_now,
            impact_direction=theme.impact_direction,
            confidence_score=theme.confidence_score,
            time_horizon=theme.time_horizon,
            related_industries=list(theme.related_industries_json or []),
            related_companies=list(theme.related_companies_json or []),
            issue_tags=list(getattr(theme, "issue_tags_json", None) or []),
            direct_impact_industries=list(getattr(theme, "direct_impact_industries_json", None) or []),
            entity_business_industries=list(getattr(theme, "entity_business_industries_json", None) or []),
            market_theme_tags=list(getattr(theme, "market_theme_tags_json", None) or []),
            candidate_search_tags=list(getattr(theme, "candidate_search_tags_json", None) or []),
            risk_factors=list(theme.risk_factors_json or []),
        )
        enriched = enrich_theme_tags(candidate, sources)
        actionability = _theme_actionability(
            enriched,
            {
                int(source["news_analysis_id"]): {
                    **source,
                    "price_impact_score": 0.0,
                    "investable_link_score": 0.0,
                }
                for source in sources
            },
        )
        before = (
            theme.issue_tags_json,
            theme.direct_impact_industries_json,
            theme.entity_business_industries_json,
            theme.market_theme_tags_json,
            theme.candidate_search_tags_json,
            theme.tag_confidence_json,
            theme.actionability_score,
            theme.price_impact_score,
            theme.investable_link_score,
            theme.is_investable_theme,
            theme.theme_bucket,
            theme.theme_bucket_reason,
        )
        theme.issue_tags_json = enriched.issue_tags
        theme.direct_impact_industries_json = enriched.direct_impact_industries
        theme.entity_business_industries_json = [item.model_dump() for item in enriched.entity_business_industries]
        theme.market_theme_tags_json = enriched.market_theme_tags
        theme.candidate_search_tags_json = enriched.candidate_search_tags
        theme.tag_confidence_json = build_tag_confidence(enriched)
        theme.actionability_score = actionability["actionability_score"]
        theme.price_impact_score = actionability["price_impact_score"]
        theme.investable_link_score = actionability["investable_link_score"]
        theme.is_investable_theme = actionability["is_investable_theme"]
        theme.theme_bucket = actionability["theme_bucket"]
        theme.theme_bucket_reason = actionability["theme_bucket_reason"]
        after = (
            theme.issue_tags_json,
            theme.direct_impact_industries_json,
            theme.entity_business_industries_json,
            theme.market_theme_tags_json,
            theme.candidate_search_tags_json,
            theme.tag_confidence_json,
            theme.actionability_score,
            theme.price_impact_score,
            theme.investable_link_score,
            theme.is_investable_theme,
            theme.theme_bucket,
            theme.theme_bucket_reason,
        )
        if before != after:
            updated += 1
        else:
            skipped += 1
    db.commit()
    return {"scanned_count": scanned, "updated_count": updated, "skipped_count": skipped}


def _run_response(run: ThemeAnalysisRun, theme_ids: list[int]) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "source_count": run.selected_source_count,
        "selected_theme_count": run.selected_theme_count,
        "theme_ids": theme_ids,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
        "duration_ms": run.duration_ms,
        "insufficient_data_reason": run.insufficient_data_reason,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "model_name": run.model_name,
        "retryable": None,
    }
