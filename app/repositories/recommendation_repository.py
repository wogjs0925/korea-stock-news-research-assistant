from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.market_theme import MarketTheme
from app.models.recommendation_item import RecommendationItem
from app.models.recommendation_run import RecommendationRun
from app.models.security import Security
from app.models.theme_candidate_run import ThemeCandidateRun
from app.models.theme_recommendation import ThemeRecommendation
from app.models.theme_security_candidate import ThemeSecurityCandidate


def ensure_recommendation_tables_schema(db: Session) -> None:
    bind = db.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "sqlite":
        return
    column_specs = {
        "recommendation_runs": {
            "source_candidate_run_id": "INTEGER",
            "error_code": "VARCHAR(128)",
            "error_message": "TEXT",
        },
        "recommendation_items": {
            "diversification_score": "FLOAT NOT NULL DEFAULT 0",
            "exclusion_flags_json": "JSON NOT NULL DEFAULT '[]'",
            "excluded_reason": "TEXT",
        },
    }
    for table, specs in column_specs.items():
        existing = {row[1] for row in db.connection().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        for column, ddl in specs.items():
            if column not in existing:
                db.connection().exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    db.commit()


def create_recommendation_run(db: Session, run: RecommendationRun) -> RecommendationRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_recommendation_run(db: Session, run: RecommendationRun) -> RecommendationRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def create_theme_recommendation(db: Session, row: ThemeRecommendation) -> ThemeRecommendation:
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_recommendation_item(db: Session, row: RecommendationItem) -> RecommendationItem:
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest_candidate_run_for_theme_run(db: Session, theme_run_id: int | None) -> ThemeCandidateRun | None:
    if theme_run_id is None:
        return None
    return db.scalar(
        select(ThemeCandidateRun)
        .where(ThemeCandidateRun.theme_run_id == theme_run_id)
        .order_by(desc(ThemeCandidateRun.completed_at), desc(ThemeCandidateRun.id))
        .limit(1)
    )


def list_candidate_rows_for_theme(db: Session, theme_id: int) -> list[tuple[ThemeSecurityCandidate, Security | None]]:
    query = (
        select(ThemeSecurityCandidate, Security)
        .outerjoin(Security, Security.id == ThemeSecurityCandidate.security_id)
        .where(ThemeSecurityCandidate.market_theme_id == theme_id)
        .order_by(desc(ThemeSecurityCandidate.final_candidate_score), desc(ThemeSecurityCandidate.evidence_score))
    )
    return list(db.execute(query).all())


def get_recommendation_run_by_run_id(db: Session, run_id: str) -> RecommendationRun | None:
    return db.scalar(select(RecommendationRun).where(RecommendationRun.run_id == run_id))


def get_latest_recommendation_run(db: Session) -> RecommendationRun | None:
    return db.scalar(
        select(RecommendationRun)
        .order_by(desc(RecommendationRun.completed_at), desc(RecommendationRun.id))
        .limit(1)
    )


def list_recommendation_runs(db: Session, limit: int = 100, offset: int = 0) -> list[RecommendationRun]:
    return list(
        db.scalars(
            select(RecommendationRun)
            .order_by(desc(RecommendationRun.created_at), desc(RecommendationRun.id))
            .offset(offset)
            .limit(limit)
        ).all()
    )


def list_theme_recommendations(db: Session, recommendation_run_id: int) -> list[ThemeRecommendation]:
    return list(
        db.scalars(
            select(ThemeRecommendation)
            .where(ThemeRecommendation.recommendation_run_id == recommendation_run_id)
            .order_by(desc(ThemeRecommendation.theme_score), ThemeRecommendation.id)
        ).all()
    )


def list_recommendation_items(db: Session, theme_recommendation_id: int) -> list[RecommendationItem]:
    return list(
        db.scalars(
            select(RecommendationItem)
            .where(RecommendationItem.theme_recommendation_id == theme_recommendation_id)
            .order_by(RecommendationItem.is_excluded, RecommendationItem.asset_type, RecommendationItem.rank, desc(RecommendationItem.final_score))
        ).all()
    )


def get_latest_theme_recommendation(db: Session, theme_id: int) -> ThemeRecommendation | None:
    return db.scalar(
        select(ThemeRecommendation)
        .join(RecommendationRun, RecommendationRun.id == ThemeRecommendation.recommendation_run_id)
        .where(ThemeRecommendation.market_theme_id == theme_id)
        .order_by(desc(RecommendationRun.completed_at), desc(RecommendationRun.id))
        .limit(1)
    )
