from datetime import datetime, timezone
from typing import Any, List
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.news_analysis import NewsAnalysis
from app.models.news_analysis_run import NewsAnalysisRun
from app.models.news_article import NewsArticle
from app.repositories.news_repository import ensure_news_schema
from app.utils.news_quality import ANALYSIS_CANDIDATE_THRESHOLD


def create_analysis_run(db: Session, run: NewsAnalysisRun) -> NewsAnalysisRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_analysis_run(db: Session, run: NewsAnalysisRun) -> NewsAnalysisRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def save_analysis(db: Session, analysis: NewsAnalysis) -> NewsAnalysis:
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def get_existing_analysis(db: Session, news_article_id: int, model_name: str, prompt_version: str):
    return db.scalar(
        select(NewsAnalysis).where(
            NewsAnalysis.news_article_id == news_article_id,
            NewsAnalysis.model_name == model_name,
            NewsAnalysis.prompt_version == prompt_version,
            NewsAnalysis.status == "completed",
        )
    )


def get_existing_analysis_for_update(
    db: Session,
    news_article_id: int,
    model_name: str,
    prompt_version: str,
) -> NewsAnalysis | None:
    return db.scalar(
        select(NewsAnalysis)
        .where(
            NewsAnalysis.news_article_id == news_article_id,
            NewsAnalysis.model_name == model_name,
            NewsAnalysis.prompt_version == prompt_version,
        )
        .order_by(NewsAnalysis.id.desc())
        .limit(1)
    )


def save_or_update_analysis(db: Session, analysis: NewsAnalysis) -> NewsAnalysis:
    existing = get_existing_analysis_for_update(
        db,
        analysis.news_article_id,
        analysis.model_name,
        analysis.prompt_version,
    )
    if existing is None:
        return save_analysis(db, analysis)

    update_fields: dict[str, Any] = {
        "analysis_run_id": analysis.analysis_run_id,
        "status": analysis.status,
        "summary": analysis.summary,
        "event_type": analysis.event_type,
        "impact_direction": analysis.impact_direction,
        "sentiment_score": analysis.sentiment_score,
        "importance_score": analysis.importance_score,
        "novelty_score": analysis.novelty_score,
        "market_relevance_score": analysis.market_relevance_score,
        "confidence_score": analysis.confidence_score,
        "time_horizon": analysis.time_horizon,
        "candidate_themes_json": analysis.candidate_themes_json,
        "companies_json": analysis.companies_json,
        "evidence_points_json": analysis.evidence_points_json,
        "risk_factors_json": analysis.risk_factors_json,
        "is_investment_relevant": analysis.is_investment_relevant,
        "input_tokens": analysis.input_tokens,
        "output_tokens": analysis.output_tokens,
        "total_tokens": analysis.total_tokens,
        "latency_ms": analysis.latency_ms,
        "openai_request_id": analysis.openai_request_id,
        "error_message": analysis.error_message,
        "analyzed_at": datetime.now(timezone.utc),
    }
    for field_name, value in update_fields.items():
        setattr(existing, field_name, value)

    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def list_unanalyzed_news(
    db: Session,
    model_name: str,
    prompt_version: str,
    limit: int = 10,
    include_completed: bool = False,
) -> List[NewsArticle]:
    ensure_news_schema(db)
    completed_exists = (
        select(NewsAnalysis.id)
        .where(
            NewsAnalysis.news_article_id == NewsArticle.id,
            NewsAnalysis.model_name == model_name,
            NewsAnalysis.prompt_version == prompt_version,
            NewsAnalysis.status == "completed",
        )
        .exists()
    )

    conditions = [
        NewsArticle.is_active == True,
        NewsArticle.available_at <= datetime.now(timezone.utc),
    ]
    if not include_completed:
        conditions.extend(
            [
                NewsArticle.is_duplicate == False,
                NewsArticle.is_analysis_candidate == True,
                NewsArticle.market_relevance_score >= ANALYSIS_CANDIDATE_THRESHOLD,
            ]
        )
        conditions.append(~completed_exists)

    q = (
        select(NewsArticle)
        .where(*conditions)
        .order_by(NewsArticle.available_at.desc())
        .limit(limit)
    )
    return list(db.scalars(q).all())
