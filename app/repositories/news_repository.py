from datetime import datetime, timezone
from typing import List

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.news_article import NewsArticle
from app.models.news_collection_run import NewsCollectionRun
from datetime import datetime, timezone, timedelta


def ensure_news_schema(db: Session) -> None:
    bind = db.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "sqlite":
        return
    specs = {
        "canonical_url": "TEXT",
        "normalized_title": "TEXT",
        "content_fingerprint": "VARCHAR(64)",
        "duplicate_group_id": "VARCHAR(64)",
        "duplicate_of_article_id": "INTEGER",
        "duplicate_reason": "VARCHAR(256)",
        "market_relevance_score": "FLOAT NOT NULL DEFAULT 1",
        "is_market_relevant": "BOOLEAN NOT NULL DEFAULT 1",
        "is_analysis_candidate": "BOOLEAN NOT NULL DEFAULT 1",
    }
    existing = {row[1] for row in db.connection().exec_driver_sql("PRAGMA table_info(news_articles)").fetchall()}
    for column, ddl in specs.items():
        if column not in existing:
            db.connection().exec_driver_sql(f"ALTER TABLE news_articles ADD COLUMN {column} {ddl}")
    db.commit()


def get_article_by_hash(db: Session, content_hash: str) -> NewsArticle | None:
    return db.scalar(select(NewsArticle).where(NewsArticle.content_hash == content_hash))


def create_article(db: Session, article: NewsArticle) -> NewsArticle:
    ensure_news_schema(db)
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def update_article(db: Session, article: NewsArticle) -> NewsArticle:
    ensure_news_schema(db)
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def get_article(db: Session, article_id: int) -> NewsArticle | None:
    return db.get(NewsArticle, article_id)


def list_articles(
    db: Session,
    query: str | None = None,
    provider: str | None = None,
    publisher: str | None = None,
    is_duplicate: bool | None = None,
    is_market_relevant: bool | None = None,
    is_analysis_candidate: bool | None = None,
    from_datetime: datetime | None = None,
    to_datetime: datetime | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> List[NewsArticle]:
    ensure_news_schema(db)
    q = select(NewsArticle)
    if query:
        q = q.where(NewsArticle.query == query)
    if provider:
        q = q.where(NewsArticle.provider == provider)
    if publisher:
        q = q.where(NewsArticle.publisher == publisher)
    if is_duplicate is not None:
        q = q.where(NewsArticle.is_duplicate == is_duplicate)
    if is_market_relevant is not None:
        q = q.where(NewsArticle.is_market_relevant == is_market_relevant)
    if is_analysis_candidate is not None:
        q = q.where(NewsArticle.is_analysis_candidate == is_analysis_candidate)
    if from_datetime:
        q = q.where(NewsArticle.published_at >= from_datetime)
    if to_datetime:
        q = q.where(NewsArticle.published_at <= to_datetime)
    if keyword:
        like = f"%{keyword}%"
        q = q.where((NewsArticle.title.ilike(like)) | (NewsArticle.description.ilike(like)))

    # order: published_at desc, collected_at desc, id desc
    q = q.order_by(desc(NewsArticle.published_at), desc(NewsArticle.collected_at), desc(NewsArticle.id)).limit(limit).offset(offset)
    return db.scalars(q).all()


def find_duplicate_candidate(
    db: Session,
    *,
    canonical_url: str,
    normalized_title: str,
    content_fingerprint: str,
    publisher: str | None = None,
    published_at: datetime | None = None,
) -> tuple[NewsArticle | None, str | None]:
    ensure_news_schema(db)
    if canonical_url:
        row = db.scalar(select(NewsArticle).where(NewsArticle.canonical_url == canonical_url).order_by(NewsArticle.id).limit(1))
        if row:
            return row, "canonical_url"
    if content_fingerprint:
        row = db.scalar(
            select(NewsArticle).where(NewsArticle.content_fingerprint == content_fingerprint).order_by(NewsArticle.id).limit(1)
        )
        if row:
            return row, "content_fingerprint"
    if normalized_title:
        q = select(NewsArticle).where(NewsArticle.normalized_title == normalized_title)
        if publisher:
            q = q.where(NewsArticle.publisher == publisher)
        row = db.scalar(q.order_by(NewsArticle.id).limit(1))
        if row:
            return row, "normalized_title"
    return None, None


def create_collection_run(db: Session, run: NewsCollectionRun) -> NewsCollectionRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_collection_run(db: Session, run: NewsCollectionRun) -> NewsCollectionRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_collection_run_by_run_id(db: Session, run_id: str) -> NewsCollectionRun | None:
    return db.scalar(select(NewsCollectionRun).where(NewsCollectionRun.run_id == run_id))


def list_collection_runs(db: Session, provider: str | None = None, status: str | None = None, limit: int = 100):
    q = select(NewsCollectionRun)
    if provider:
        q = q.where(NewsCollectionRun.provider == provider)
    if status:
        q = q.where(NewsCollectionRun.status == status)
    q = q.order_by(desc(NewsCollectionRun.started_at)).limit(limit)
    return db.scalars(q).all()


def news_summary(db: Session) -> dict:
    ensure_news_schema(db)
    total = db.scalar(select(func.count()).select_from(NewsArticle)) or 0
    active = db.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.is_active == True)) or 0
    dup = db.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.is_duplicate == True)) or 0
    noise = db.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.is_analysis_candidate == False)) or 0
    threshold = datetime.now(timezone.utc) - timedelta(days=1)
    last_24 = db.scalar(
        select(func.count()).select_from(NewsArticle).where(NewsArticle.collected_at >= threshold)
    ) or 0
    latest = db.scalar(select(func.max(NewsArticle.collected_at)))
    runs = db.scalar(select(func.count()).select_from(NewsCollectionRun)) or 0
    failed_runs = db.scalar(select(func.count()).select_from(NewsCollectionRun).where(NewsCollectionRun.status == 'failed')) or 0
    return {
        'total_articles': int(total),
        'active_articles': int(active),
        'duplicate_articles': int(dup),
        'noise_articles': int(noise),
        'articles_last_24h': int(last_24),
        'latest_collected_at': latest,
        'total_collection_runs': int(runs),
        'failed_collection_runs': int(failed_runs),
    }
