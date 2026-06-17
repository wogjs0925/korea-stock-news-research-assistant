import os
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.backend.main import app
from app.database.session import get_db
from app.providers.news.naver import NaverNewsProvider


def _make_session():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_news_collect_endpoint():
    engine, Session = _make_session()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            resp = client.post("/news/collect", json={"query":"테스트","display":3,"sort":"date","provider":"mock"})
            assert resp.status_code == 201
            data = resp.json()
            assert data["saved_count"] == 3
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_news_collection_status_endpoint(client: TestClient):
    resp = client.get("/news/collection-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "interval_minutes" in data
    assert "active_profile_count" in data
    assert "total_articles" in data


def test_news_collect_all_endpoint(monkeypatch, client: TestClient):
    def fake_run():
        return {"active_terms": 0, "succeeded": 0, "failed": 0}

    import app.backend.routes.news as news_router_module

    monkeypatch.setattr(news_router_module, "run_scheduled_search_terms", fake_run)
    resp = client.post("/news/collect-all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_terms"] == 0


def test_news_collect_endpoint_with_naver_provider(monkeypatch):
    engine, Session = _make_session()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")

    async def fake_search(self, query, display, sort):
        return [
            {
                "title": "Naver Article",
                "description": "Naver description",
                "link": "https://naver.example.com/article",
                "original_link": "https://origin.example.com/article",
                "published_at": "2024-01-01T00:00:00+09:00",
                "publisher": None,
                "raw_data": {"provider": "naver"},
            }
        ]

    monkeypatch.setattr(NaverNewsProvider, "search", fake_search)

    try:
        with TestClient(app) as client:
            resp = client.post("/news/collect", json={"query":"테스트","display":1,"sort":"date","provider":"naver"})
            assert resp.status_code == 201
            data = resp.json()
            assert data["provider"] == "naver"
            assert data["saved_count"] == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
