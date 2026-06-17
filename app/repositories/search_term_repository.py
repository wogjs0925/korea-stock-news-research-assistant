from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.news_search_term import NewsSearchTerm


def get_search_term_by_id(db: Session, term_id: int) -> NewsSearchTerm | None:
    return db.get(NewsSearchTerm, term_id)


def get_search_term_by_query_and_provider(db: Session, query: str, provider: str | None) -> NewsSearchTerm | None:
    return db.scalar(
        select(NewsSearchTerm).where(NewsSearchTerm.query == query, NewsSearchTerm.provider == provider)
    )


def list_search_terms(
    db: Session,
    active_only: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[NewsSearchTerm]:
    q = select(NewsSearchTerm)
    if active_only is True:
        q = q.where(NewsSearchTerm.is_active == True)
    if active_only is False:
        q = q.where(NewsSearchTerm.is_active == False)
    q = q.order_by(desc(NewsSearchTerm.created_at)).limit(limit).offset(offset)
    return db.scalars(q).all()


def create_search_term(db: Session, term: NewsSearchTerm) -> NewsSearchTerm:
    db.add(term)
    db.commit()
    db.refresh(term)
    return term


def update_search_term(db: Session, term: NewsSearchTerm) -> NewsSearchTerm:
    db.add(term)
    db.commit()
    db.refresh(term)
    return term


def delete_search_term(db: Session, term: NewsSearchTerm) -> None:
    db.delete(term)
    db.commit()


def list_active_search_terms(db: Session) -> list[NewsSearchTerm]:
    return db.scalars(
        select(NewsSearchTerm).where(NewsSearchTerm.is_active == True).order_by(NewsSearchTerm.created_at.asc())
    ).all()


def list_scheduled_search_terms(db: Session) -> list[NewsSearchTerm]:
    return db.scalars(
        select(NewsSearchTerm)
        .where(
            NewsSearchTerm.is_active == True,
            NewsSearchTerm.source_type.in_(("system", "ai")),
        )
        .order_by(NewsSearchTerm.created_at.asc())
    ).all()
