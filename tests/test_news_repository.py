import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.news_article import NewsArticle
from app.models.news_collection_run import NewsCollectionRun
from app.repositories.news_repository import create_article, create_collection_run, get_article_by_hash, list_articles, news_summary


def _make_session():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_news_repository_crud_and_summary():
    engine, Session = _make_session()
    try:
        with Session() as db:
            article = NewsArticle(provider="mock", external_id="1", query="q", title="t", description="d", link="l", title_normalized="t", content_hash="h1")
            create_article(db, article)
            fetched = db.get(NewsArticle, article.id)
            assert fetched is not None

            run = NewsCollectionRun(run_id="r1", provider="mock", query="q")
            create_collection_run(db, run)

            # hash lookup
            assert get_article_by_hash(db, "h1") is not None

            summary = news_summary(db)
            assert summary["total_articles"] >= 1
    finally:
        engine.dispose()
