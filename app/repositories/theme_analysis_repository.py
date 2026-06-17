from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.market_theme import MarketTheme
from app.models.news_analysis import NewsAnalysis
from app.models.news_article import NewsArticle
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_news_link import ThemeNewsLink
from app.services.theme_actionability_service import score_news_selection


def ensure_theme_tables_schema(db: Session) -> None:
    bind = db.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "sqlite":
        return

    existing_columns = {
        row[1]
        for row in db.connection().exec_driver_sql("PRAGMA table_info(theme_analysis_runs)").fetchall()
    }
    if "duration_ms" not in existing_columns:
        db.connection().exec_driver_sql("ALTER TABLE theme_analysis_runs ADD COLUMN duration_ms INTEGER")
    if "error_code" not in existing_columns:
        db.connection().exec_driver_sql("ALTER TABLE theme_analysis_runs ADD COLUMN error_code VARCHAR(128)")
    theme_columns = {
        row[1]
        for row in db.connection().exec_driver_sql("PRAGMA table_info(market_themes)").fetchall()
    }
    json_columns = {
        "issue_tags_json": "JSON NOT NULL DEFAULT '[]'",
        "direct_impact_industries_json": "JSON NOT NULL DEFAULT '[]'",
        "entity_business_industries_json": "JSON NOT NULL DEFAULT '[]'",
        "market_theme_tags_json": "JSON NOT NULL DEFAULT '[]'",
        "candidate_search_tags_json": "JSON NOT NULL DEFAULT '[]'",
        "tag_confidence_json": "JSON NOT NULL DEFAULT '{}'",
        "actionability_score": "FLOAT NOT NULL DEFAULT 0",
        "price_impact_score": "FLOAT NOT NULL DEFAULT 0",
        "investable_link_score": "FLOAT NOT NULL DEFAULT 0",
        "is_investable_theme": "BOOLEAN NOT NULL DEFAULT 0",
        "theme_bucket": "VARCHAR(64) NOT NULL DEFAULT 'low_actionability'",
        "theme_bucket_reason": "VARCHAR(512)",
    }
    for column, ddl in json_columns.items():
        if column not in theme_columns:
            db.connection().exec_driver_sql(f"ALTER TABLE market_themes ADD COLUMN {column} {ddl}")
    db.commit()


def _trim_text(value: str | None, max_length: int = 500) -> str | None:
    if value is None:
        return None
    return value[:max_length]


def list_theme_source_analyses(
    db: Session,
    window_start: datetime,
    window_end: datetime,
    min_importance: float,
    min_market_relevance: float,
    max_sources: int,
) -> list[dict[str, Any]]:
    candidate_pool_limit = max(max_sources, min(max_sources * 3, 500))
    query = (
        select(NewsAnalysis, NewsArticle)
        .join(NewsArticle, NewsArticle.id == NewsAnalysis.news_article_id)
        .where(
            NewsAnalysis.status == "completed",
            NewsAnalysis.is_investment_relevant == True,
            NewsAnalysis.importance_score >= min_importance,
            NewsAnalysis.market_relevance_score >= min_market_relevance,
            NewsArticle.is_active == True,
            NewsArticle.is_duplicate == False,
            NewsArticle.available_at >= window_start,
            NewsArticle.available_at <= window_end,
        )
        .order_by(desc(NewsArticle.available_at), desc(NewsAnalysis.importance_score))
        .limit(candidate_pool_limit)
    )
    rows = db.execute(query).all()
    result: list[dict[str, Any]] = []
    for analysis, article in rows:
        item = {
            "news_analysis_id": analysis.id,
            "news_article_id": article.id,
            "title": _trim_text(article.title, 300),
            "publisher": article.publisher,
            "published_at": article.published_at,
            "available_at": article.available_at,
            "summary": _trim_text(analysis.summary, 500),
            "event_type": analysis.event_type,
            "impact_direction": analysis.impact_direction,
            "importance_score": analysis.importance_score or 0.0,
            "novelty_score": analysis.novelty_score or 0.0,
            "market_relevance_score": analysis.market_relevance_score or 0.0,
            "confidence_score": analysis.confidence_score or 0.0,
            "time_horizon": analysis.time_horizon,
            "candidate_themes": list((analysis.candidate_themes_json or [])[:10]),
            "companies": [
                c.get("company_name")
                for c in (analysis.companies_json or [])
                if isinstance(c, dict) and c.get("company_name")
            ][:15],
            "evidence_points": list((analysis.evidence_points_json or [])[:5]),
            "risk_factors": list((analysis.risk_factors_json or [])[:5]),
        }
        item.update(score_news_selection(item))
        result.append(item)
    result.sort(
        key=lambda item: (
            item.get("final_news_selection_score", 0.0),
            item.get("price_impact_score", 0.0),
            item.get("investable_link_score", 0.0),
            item.get("available_at"),
        ),
        reverse=True,
    )
    return result[:max_sources]


def create_theme_analysis_run(db: Session, run: ThemeAnalysisRun) -> ThemeAnalysisRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_theme_analysis_run(db: Session, run: ThemeAnalysisRun) -> ThemeAnalysisRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def create_market_theme(db: Session, theme: MarketTheme) -> MarketTheme:
    db.add(theme)
    db.commit()
    db.refresh(theme)
    return theme


def create_theme_news_link(db: Session, link: ThemeNewsLink) -> ThemeNewsLink:
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def get_latest_theme_run(db: Session) -> ThemeAnalysisRun | None:
    return db.scalar(
        select(ThemeAnalysisRun)
        .where(ThemeAnalysisRun.status.in_(["completed", "insufficient_data"]))
        .order_by(desc(ThemeAnalysisRun.completed_at), desc(ThemeAnalysisRun.id))
        .limit(1)
    )


def get_theme_run_by_run_id(db: Session, run_id: str) -> ThemeAnalysisRun | None:
    return db.scalar(select(ThemeAnalysisRun).where(ThemeAnalysisRun.run_id == run_id))


def list_theme_runs(db: Session, limit: int = 100, offset: int = 0) -> list[ThemeAnalysisRun]:
    return list(
        db.scalars(
            select(ThemeAnalysisRun)
            .order_by(desc(ThemeAnalysisRun.created_at), desc(ThemeAnalysisRun.id))
            .offset(offset)
            .limit(limit)
        ).all()
    )


def list_market_themes(
    db: Session,
    run_id: str | None = None,
    impact_direction: str | None = None,
    minimum_score: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MarketTheme]:
    query = select(MarketTheme).join(ThemeAnalysisRun, ThemeAnalysisRun.id == MarketTheme.theme_run_id)
    if run_id:
        query = query.where(ThemeAnalysisRun.run_id == run_id)
    if impact_direction:
        query = query.where(MarketTheme.impact_direction == impact_direction)
    if minimum_score is not None:
        query = query.where(MarketTheme.calculated_score >= minimum_score)
    query = query.order_by(desc(MarketTheme.calculated_score), MarketTheme.rank).offset(offset).limit(limit)
    return list(db.scalars(query).all())


def list_themes_for_run(db: Session, theme_run_id: int) -> list[MarketTheme]:
    return list(db.scalars(select(MarketTheme).where(MarketTheme.theme_run_id == theme_run_id).order_by(MarketTheme.rank)).all())


def get_market_theme(db: Session, theme_id: int) -> MarketTheme | None:
    return db.get(MarketTheme, theme_id)


def list_theme_evidence(db: Session, theme_id: int) -> list[dict[str, Any]]:
    query = (
        select(ThemeNewsLink, NewsAnalysis, NewsArticle)
        .join(NewsAnalysis, NewsAnalysis.id == ThemeNewsLink.news_analysis_id)
        .join(NewsArticle, NewsArticle.id == NewsAnalysis.news_article_id)
        .where(ThemeNewsLink.market_theme_id == theme_id)
        .order_by(desc(ThemeNewsLink.relevance_score), desc(NewsArticle.available_at))
    )
    return [
        {
            "news_analysis_id": analysis.id,
            "news_article_id": article.id,
            "title": article.title,
            "publisher": article.publisher,
            "published_at": article.published_at,
            "summary": analysis.summary,
            "relevance_score": link.relevance_score,
            "evidence_reason": link.evidence_reason,
        }
        for link, analysis, article in db.execute(query).all()
    ]
