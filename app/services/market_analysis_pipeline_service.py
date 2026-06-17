from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.news_analysis import NewsAnalysis
from app.models.news_article import NewsArticle
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log
from app.services.news_analysis_service import run_analysis
from app.repositories.theme_analysis_repository import list_theme_source_analyses
from app.services.theme_analysis_service import run_theme_analysis
from app.services.theme_candidate_service import generate_theme_candidates
from app.services.recommendation_service import run_recommendations as run_recommendation_engine


FATAL_OPENAI_CODES = {
    "OPENAI_AUTH_ERROR",
    "OPENAI_MODEL_CONFIG_ERROR",
    "OPENAI_QUOTA_ERROR",
    "OPENAI_QUOTA_EXCEEDED",
}

settings = get_settings()


def _status_from_steps(
    news: dict[str, Any],
    theme: dict[str, Any] | None,
    candidates: dict[str, Any] | None,
    recommendations: dict[str, Any] | None = None,
) -> str:
    if news.get("completed", 0) <= 0:
        return "failed" if news.get("failed", 0) else "insufficient_data"
    if theme is None:
        return "partial"
    if theme.get("status") != "completed":
        return "partial"
    if candidates is not None and candidates.get("status") != "completed":
        return "partial"
    if recommendations is not None and recommendations.get("status") != "completed":
        return "partial"
    return "completed"


def _duration_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _log_pipeline_error(db: Session, run_id: str, stage: str, error: Exception) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code="MARKET_ANALYSIS_PIPELINE_ERROR",
                severity="ERROR",
                component="market_analysis_pipeline",
                error_type=type(error).__name__,
                message="시장 분석 통합 실행 중 오류가 발생했습니다.",
                context_json={"run_id": run_id, "stage": stage, "error_type": type(error).__name__},
            ),
        )
    except Exception:
        pass


def _news_selection_summary(
    db: Session,
    *,
    window_hours: int,
    max_news_analysis_count: int,
    max_theme_source_count: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    base = select(func.count()).select_from(NewsArticle).where(NewsArticle.is_active == True, NewsArticle.available_at <= now)
    scanned = db.scalar(base) or 0
    duplicate_excluded = db.scalar(
        select(func.count()).select_from(NewsArticle).where(NewsArticle.is_active == True, NewsArticle.is_duplicate == True)
    ) or 0
    low_relevance_excluded = db.scalar(
        select(func.count())
        .select_from(NewsArticle)
        .where(
            NewsArticle.is_active == True,
            NewsArticle.is_duplicate == False,
            (NewsArticle.is_analysis_candidate == False) | (NewsArticle.market_relevance_score < 0.4),
        )
    ) or 0
    already_analyzed = db.scalar(
        select(func.count())
        .select_from(NewsAnalysis)
        .join(NewsArticle, NewsArticle.id == NewsAnalysis.news_article_id)
        .where(NewsAnalysis.status == "completed", NewsArticle.is_active == True)
    ) or 0
    sources = list_theme_source_analyses(
        db,
        window_start,
        now,
        settings.theme_analysis_min_importance,
        settings.theme_analysis_min_market_relevance,
        max_theme_source_count,
    )
    selected_articles = [
        {
            "article_id": source.get("news_article_id"),
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "published_at": source.get("published_at"),
            "market_relevance_score": source.get("market_relevance_score"),
            "price_impact_score": source.get("price_impact_score"),
            "investable_link_score": source.get("investable_link_score"),
            "final_news_selection_score": source.get("final_news_selection_score"),
            "is_duplicate": False,
            "is_analysis_candidate": True,
            "analysis_status": "completed",
            "selection_reason": "최근 기간 내 중복이 아니며 시장 관련성 기준을 통과한 완료 분석",
        }
        for source in sources[:20]
    ]
    return {
        "scanned_count": int(scanned),
        "duplicate_excluded_count": int(duplicate_excluded),
        "low_relevance_excluded_count": int(low_relevance_excluded),
        "already_analyzed_count": int(already_analyzed),
        "selected_for_ai_analysis_count": max_news_analysis_count,
        "selected_for_theme_count": len(sources),
        "selection_policy": {
            "duplicate_news_excluded": True,
            "low_relevance_news_excluded": True,
            "analysis_candidates_only": True,
            "window_hours": window_hours,
            "max_news_analysis_count": max_news_analysis_count,
            "max_theme_source_count": max_theme_source_count,
            "minimum_importance_score": settings.theme_analysis_min_importance,
            "minimum_market_relevance_score": settings.theme_analysis_min_market_relevance,
            "score_formula": "market_relevance_score * 0.3 + price_impact_score * 0.4 + investable_link_score * 0.3",
            "price_impact_weight": 0.4,
            "investable_link_weight": 0.3,
            "market_relevance_weight": 0.3,
            "sort_order": "final_news_selection_score",
        },
        "selected_articles": selected_articles,
    }


def run_market_analysis_pipeline(
    db: Session,
    *,
    analysis_window_hours: int,
    max_news_analysis_count: int,
    max_theme_source_count: int,
    force_reanalyze: bool = False,
    run_candidate_generation: bool = True,
    include_weak_industry_candidates: bool = False,
    include_watchlist_themes: bool = False,
    include_leveraged_inverse_etfs: bool = True,
    max_stock_candidates_per_theme: int = 15,
    max_etf_candidates_per_theme: int = 20,
    run_recommendations: bool = True,
    max_stocks_per_theme: int = 3,
    max_etfs_per_theme: int = 2,
    diversify_country: bool = True,
) -> dict[str, Any]:
    started = time.time()
    run_id = f"MARKET-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    stage = "news_analysis"
    news_result: dict[str, Any] | None = None
    theme_result: dict[str, Any] | None = None
    candidate_result: dict[str, Any] | None = None
    recommendation_result: dict[str, Any] | None = None
    news_selection: dict[str, Any] | None = None
    try:
        news_result = run_analysis(db, limit=max_news_analysis_count, provider="openai", force=force_reanalyze)
        news_selection = _news_selection_summary(
            db,
            window_hours=analysis_window_hours,
            max_news_analysis_count=max_news_analysis_count,
            max_theme_source_count=max_theme_source_count,
        )
        news_error_codes = set(news_result.get("error_codes") or [])
        if news_error_codes & FATAL_OPENAI_CODES or int(news_result.get("completed") or 0) <= 0:
            return {
                "run_id": run_id,
                "status": _status_from_steps(news_result, None, None),
                "failed_stage": stage,
                "news_selection": news_selection,
                "news_analysis": news_result,
                "theme_analysis": None,
                "candidate_generation": None,
                "recommendations": None,
                "duration_ms": _duration_ms(started),
            }

        stage = "theme_analysis"
        theme_result = run_theme_analysis(
            db,
            window_hours=analysis_window_hours,
            max_sources=max_theme_source_count,
            provider="openai",
        )
        if theme_result.get("status") != "completed" or int(theme_result.get("selected_theme_count") or 0) <= 0:
            return {
                "run_id": run_id,
                "status": _status_from_steps(news_result, theme_result, None),
                "failed_stage": stage,
                "news_selection": news_selection,
                "news_analysis": news_result,
                "theme_analysis": theme_result,
                "candidate_generation": None,
                "recommendations": None,
                "duration_ms": _duration_ms(started),
            }

        if run_candidate_generation:
            stage = "candidate_generation"
            candidate_result = generate_theme_candidates(
                db,
                include_weak_industry_candidates=include_weak_industry_candidates,
                include_watchlist_themes=include_watchlist_themes,
                include_leveraged_inverse_etfs=include_leveraged_inverse_etfs,
                max_stock_candidates_per_theme=max_stock_candidates_per_theme,
                max_etf_candidates_per_theme=max_etf_candidates_per_theme,
            )
            if candidate_result.get("status") != "completed":
                status = _status_from_steps(news_result, theme_result, candidate_result, None)
                return {
                    "run_id": run_id,
                    "status": status,
                    "failed_stage": stage,
                    "news_selection": news_selection,
                    "news_analysis": news_result,
                    "theme_analysis": theme_result,
                    "candidate_generation": candidate_result,
                    "recommendations": None,
                    "duration_ms": _duration_ms(started),
                }

        if run_recommendations and run_candidate_generation:
            stage = "recommendations"
            recommendation_result = run_recommendation_engine(
                db,
                max_stocks_per_theme=max_stocks_per_theme,
                max_etfs_per_theme=max_etfs_per_theme,
                include_leveraged_inverse_etfs=include_leveraged_inverse_etfs,
                diversify_country=diversify_country,
            )

        status = _status_from_steps(news_result, theme_result, candidate_result, recommendation_result)
        return {
            "run_id": run_id,
            "status": status,
            "failed_stage": None if status == "completed" else stage,
            "news_selection": news_selection,
            "news_analysis": news_result,
            "theme_analysis": theme_result,
            "candidate_generation": candidate_result,
            "recommendations": recommendation_result,
            "duration_ms": _duration_ms(started),
        }
    except Exception as exc:
        _log_pipeline_error(db, run_id, stage, exc)
        return {
            "run_id": run_id,
            "status": "failed",
            "failed_stage": stage,
            "news_selection": news_selection,
            "news_analysis": news_result,
            "theme_analysis": theme_result,
            "candidate_generation": candidate_result,
            "recommendations": recommendation_result,
            "error_code": "MARKET_ANALYSIS_PIPELINE_ERROR",
            "error_message": "시장 분석 통합 실행 중 오류가 발생했습니다.",
            "duration_ms": _duration_ms(started),
        }
