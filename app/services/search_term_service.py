from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text

from app.core.config import get_settings
from app.models.news_search_term import NewsSearchTerm
from app.repositories.search_term_repository import (
    create_search_term as create_search_term_record,
    delete_search_term as delete_search_term_record,
    get_search_term_by_id,
    get_search_term_by_query_and_provider,
    list_search_terms as list_search_term_records,
    update_search_term as update_search_term_record,
)
from app.schemas.news import SearchTermCreate, SearchTermUpdate


settings = get_settings()


def create_search_term(db: Any, payload: SearchTermCreate) -> NewsSearchTerm:
    provider = payload.provider or settings.news_provider
    display = payload.display or settings.news_default_display
    existing = get_search_term_by_query_and_provider(db, payload.query.strip(), provider)
    if existing:
        raise ValueError("same search term already exists")

    term = NewsSearchTerm(
        query=payload.query.strip(),
        provider=provider,
        source_type=payload.source_type,
        display=display,
        sort=payload.sort,
        is_active=payload.is_active,
    )
    return create_search_term_record(db, term)


def list_search_terms(db: Any, active_only: bool | None = None, limit: int = 100, offset: int = 0) -> list[NewsSearchTerm]:
    return list_search_term_records(db, active_only=active_only, limit=limit, offset=offset)


def update_search_term(db: Any, term_id: int, payload: SearchTermUpdate) -> NewsSearchTerm:
    term = get_search_term_by_id(db, term_id)
    if term is None:
        raise LookupError("search term not found")

    if payload.query is not None:
        term.query = payload.query.strip()
    if payload.provider is not None:
        term.provider = payload.provider
    if payload.source_type is not None:
        term.source_type = payload.source_type
    if payload.display is not None:
        term.display = payload.display
    if payload.sort is not None:
        term.sort = payload.sort
    if payload.is_active is not None:
        term.is_active = payload.is_active
    term.updated_at = datetime.now(timezone.utc)

    return update_search_term_record(db, term)


def delete_search_term(db: Any, term_id: int) -> None:
    term = get_search_term_by_id(db, term_id)
    if term is None:
        raise LookupError("search term not found")
    delete_search_term_record(db, term)


DEFAULT_SEARCH_QUERIES = [
    "증시",
    "코스피",
    "코스닥",
    "기업 실적",
    "실적 전망",
    "공급계약",
    "대규모 수주",
    "기업 투자",
    "신규 공장",
    "정부 산업정책",
    "규제 완화",
    "금리",
    "환율",
    "수출",
    "반도체",
    "인공지능",
    "로봇",
    "이차전지",
    "자동차",
    "조선",
    "방산",
    "원전",
    "전력 인프라",
    "바이오",
    "제약",
    "게임",
    "통신",
    "금융",
    "건설",
    "항공",
]


def ensure_search_term_source_type_column(db: Any) -> None:
    inspector = inspect(db.get_bind())
    column_names = [col["name"] for col in inspector.get_columns("news_search_terms")]
    if "source_type" not in column_names:
        db.execute(text("ALTER TABLE news_search_terms ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'manual'"))
        db.commit()
    db.execute(text("UPDATE news_search_terms SET source_type = 'manual' WHERE source_type IS NULL"))
    db.commit()


def ensure_default_search_terms(db: Any) -> None:
    ensure_search_term_source_type_column(db)

    for query in DEFAULT_SEARCH_QUERIES:
        existing = get_search_term_by_query_and_provider(db, query, settings.news_provider)
        if existing is None:
            term = NewsSearchTerm(
                query=query,
                provider=settings.news_provider,
                source_type="system",
                display=settings.news_default_display,
                sort="date",
                is_active=True,
            )
            create_search_term_record(db, term)
        elif existing.source_type == "manual":
            existing.source_type = "system"
            existing.display = settings.news_default_display
            existing.sort = "date"
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            update_search_term_record(db, existing)

    db.execute(text("UPDATE news_search_terms SET is_active = 0 WHERE source_type = 'manual' AND is_active = 1"))
    db.commit()
