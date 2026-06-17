from datetime import datetime, timezone

from urllib.parse import urlparse

from app.utils.publisher import infer_publisher


def test_infer_publisher_exact_mapping():
    p = infer_publisher(None, "https://www.yna.co.kr/view/AKR20260612")
    assert p == "연합뉴스"


def test_infer_publisher_subdomain_mapping():
    p = infer_publisher(None, "https://finance.hankyung.com/article/123")
    assert p == "한국경제"


def test_infer_publisher_prefix_removal():
    p = infer_publisher(None, "https://m.news1.kr/articles/abcd")
    assert p == "뉴스1"


def test_infer_publisher_original_precedence():
    orig = "https://chosun.com/article/1"
    link = "https://other.com/"
    p = infer_publisher(orig, link)
    assert p == "조선일보"


def test_infer_publisher_unknown_domain_returns_hostname():
    p = infer_publisher(None, "https://sub.example-news.co.kr/path")
    assert p == "sub.example-news.co.kr"


def test_infer_publisher_bad_url():
    p = infer_publisher(None, "not-a-url")
    assert p is None


def _make_session():
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database.base import Base

    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_collect_news_saves_inferred_publisher(monkeypatch):
    engine, Session = _make_session()
    try:
        with Session() as db:
            from app.schemas.news import NewsCollectionRequest
            from app.services.news_service import collect_news

            async def fake_call(provider_name, query, display, sort):
                return [
                    {"title": "t", "description": "d", "link": "https://www.hankyung.com/a", "original_link": None, "published_at": None, "raw_data": {}},
                ]

            monkeypatch.setattr('app.services.news_service._call_provider', fake_call)

            req = NewsCollectionRequest(query="테스트", display=1, sort="date", provider="naver")
            res = collect_news(db, req)
            # verify article saved with inferred publisher
            from app.repositories.news_repository import list_articles

            articles = list_articles(db, limit=10)
            assert len(articles) == 1
            assert articles[0].publisher == "한국경제"
    finally:
        engine.dispose()


def test_backfill_endpoint_and_idempotent(client, sqlite_session_local):
    from app.models.news_article import NewsArticle
    # create article with empty publisher
    db = sqlite_session_local()
    try:
        a = NewsArticle(
            provider="naver",
            external_id=None,
            query="q",
            title="t",
            description=None,
            link="https://sub.example-news.co.kr/1",
            original_link=None,
            publisher=None,
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc),
            title_normalized="t",
            content_hash="h1",
            is_duplicate=False,
            is_active=True,
            raw_data={},
        )
        db.add(a)
        db.commit()

        r = client.post("/news/backfill-publishers")
        assert r.status_code == 200
        body = r.json()
        assert body["checked"] == 1
        assert body["updated"] == 1

        # second run should not update again
        r2 = client.post("/news/backfill-publishers")
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["checked"] == 0 or b2["updated"] == 0
    finally:
        db.close()


def test_backfill_forbidden_in_production(client):
    from app.core.config import get_settings
    s = get_settings()
    old = s.app_env
    s.app_env = "production"
    try:
        r = client.post("/news/backfill-publishers")
        assert r.status_code == 403
    finally:
        s.app_env = old

