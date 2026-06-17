import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.news_search_term import NewsSearchTerm
from app.services.news_scheduler import run_scheduled_search_terms
from app.services.search_term_service import ensure_default_search_terms


def _make_session():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_ensure_default_search_terms_inserts_default_profiles():
    engine, Session = _make_session()
    try:
        with Session() as db:
            ensure_default_search_terms(db)
            terms = db.scalars(select(NewsSearchTerm)).all()
            assert len(terms) == 30
            assert any(term.query == "증시" for term in terms)
            assert all(term.provider == "naver" for term in terms)
            assert all(term.source_type == "system" for term in terms)
    finally:
        engine.dispose()


def test_ensure_default_search_terms_is_idempotent():
    engine, Session = _make_session()
    try:
        with Session() as db:
            ensure_default_search_terms(db)
            ensure_default_search_terms(db)
            terms = db.scalars(select(NewsSearchTerm)).all()
            assert len(terms) == 30
    finally:
        engine.dispose()


def test_ensure_default_search_terms_converts_existing_manual_terms_to_system_and_disables_manuals():
    engine, Session = _make_session()
    try:
        with Session() as db:
            db.add(
                NewsSearchTerm(
                    query="증시",
                    provider="naver",
                    display=10,
                    sort="date",
                    is_active=True,
                    source_type="manual",
                )
            )
            db.add(
                NewsSearchTerm(
                    query="과거 수동 검색",
                    provider="naver",
                    display=10,
                    sort="date",
                    is_active=True,
                    source_type="manual",
                )
            )
            db.commit()

            ensure_default_search_terms(db)

            terms = db.scalars(select(NewsSearchTerm)).all()
            assert len(terms) == 31
            assert any(term.query == "증시" and term.source_type == "system" and term.is_active for term in terms)
            assert any(term.query == "과거 수동 검색" and term.source_type == "manual" and not term.is_active for term in terms)
    finally:
        engine.dispose()


def test_run_scheduled_search_terms_uses_only_system_and_ai_terms(monkeypatch):
    engine, Session = _make_session()
    try:
        with Session() as db:
            db.add(
                NewsSearchTerm(query="system-query", provider="naver", display=10, sort="date", is_active=True, source_type="system"),
            )
            db.add(
                NewsSearchTerm(query="ai-query", provider="naver", display=10, sort="date", is_active=True, source_type="ai"),
            )
            db.add(
                NewsSearchTerm(query="manual-query", provider="naver", display=10, sort="date", is_active=True, source_type="manual"),
            )
            db.add(
                NewsSearchTerm(query="disabled-system", provider="naver", display=10, sort="date", is_active=False, source_type="system"),
            )
            db.commit()

        collected_queries = []

        def fake_collect_news(db_session, request):
            collected_queries.append(request.query)
            return {}

        import app.services.news_scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, "SessionLocal", Session)
        monkeypatch.setattr(scheduler_module, "collect_news", fake_collect_news)

        result = run_scheduled_search_terms()
        assert result["active_terms"] == 2
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert collected_queries == ["system-query", "ai-query"]
    finally:
        engine.dispose()
