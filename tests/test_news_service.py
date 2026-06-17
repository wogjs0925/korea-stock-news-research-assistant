import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.news_article import NewsArticle
from app.models.news_collection_run import NewsCollectionRun
from app.services.news_service import collect_news
from app.schemas.news import NewsCollectionRequest


def _make_session():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_collect_with_mock_provider():
    engine, Session = _make_session()
    try:
        with Session() as db:
            req = NewsCollectionRequest(query="test", display=3, sort="date", provider="mock")
            res = collect_news(db, req)
            assert res.get("saved_count") == 3
            assert res.get("received_count") == 3
            assert res.get("duplicate_count") == 0
    finally:
        engine.dispose()
