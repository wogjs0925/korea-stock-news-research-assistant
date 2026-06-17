from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.models.news_article import NewsArticle
from app.repositories.news_analysis_repository import list_unanalyzed_news
from app.repositories.news_repository import create_article, list_articles
from app.schemas.news import NewsCollectionRequest
from app.services.news_quality_service import run_news_dedupe
from app.services.news_service import collect_news
from app.utils.news_quality import canonicalize_url, market_relevance, normalize_title_for_dedupe


def _article(
    suffix: str,
    *,
    title: str = "반도체 투자 확대에 관련 기업 주가 강세",
    description: str = "반도체와 AI 투자 확대가 시장 전반의 관심을 받고 있습니다.",
    link: str | None = None,
    original_link: str | None = None,
    publisher: str = "Example",
    query: str = "반도체 주가",
    score: float = 1.0,
    candidate: bool = True,
    duplicate: bool = False,
) -> NewsArticle:
    now = datetime.now(timezone.utc)
    return NewsArticle(
        provider="mock",
        external_id=suffix,
        query=query,
        title=title,
        description=description,
        link=link or f"https://example.com/news/{suffix}",
        original_link=original_link,
        publisher=publisher,
        published_at=now,
        collected_at=now,
        available_at=now,
        title_normalized=title.lower(),
        content_hash=f"hash-{suffix}",
        market_relevance_score=score,
        is_market_relevant=score >= 0.2,
        is_analysis_candidate=candidate,
        is_duplicate=duplicate,
        is_active=True,
        raw_data={},
    )


def test_canonical_url_and_normalized_title_remove_noise() -> None:
    assert canonicalize_url("http://www.example.com/a?utm_source=x&b=1#top") == "https://example.com/a?b=1"
    assert canonicalize_url("https://news.naver.com/main?url=https%3A%2F%2Fpress.example.com%2Fa%3Futm_medium%3Dx") == "https://press.example.com/a"
    assert normalize_title_for_dedupe("[포토] 반도체 투자 확대!") == "반도체 투자 확대"


def test_market_relevance_marks_photo_sports_as_noise() -> None:
    score, is_market, is_candidate, reason = market_relevance("[포토] 야구 경기 사진", "오늘 경기 사진 모음입니다.", "야구")
    assert score < 0.4
    assert is_market is False
    assert is_candidate is False
    assert reason in {"photo_or_notice", "sports_entertainment_or_notice"}


def test_market_relevance_keeps_market_news_as_candidate() -> None:
    score, is_market, is_candidate, reason = market_relevance(
        "AI 반도체 투자 확대에 나스닥 관련주 강세",
        "반도체 기업 실적과 신규 투자 계획이 시장 관심을 받고 있습니다.",
        "AI 반도체",
    )
    assert score >= 0.4
    assert is_market is True
    assert is_candidate is True
    assert reason is None


def test_dedupe_run_flags_existing_duplicates_and_noise(sqlite_session_local) -> None:
    with sqlite_session_local() as db:
        first = create_article(
            db,
            _article(
                "1",
                title="[포토] 반도체 투자 확대",
                link="https://press.example.com/a?utm_source=x",
            ),
        )
        second = create_article(
            db,
            _article(
                "2",
                title="반도체 투자 확대",
                link="http://www.press.example.com/a",
            ),
        )
        create_article(
            db,
            _article(
                "3",
                title="[포토] 야구 경기 사진",
                description="오늘 경기 사진입니다.",
                query="야구",
                publisher="Sports",
            ),
        )

        result = run_news_dedupe(db)

        assert result["scanned_count"] == 3
        assert result["duplicate_count"] == 1
        assert result["noise_count"] >= 1
        db.refresh(first)
        db.refresh(second)
        assert first.is_duplicate is False
        assert second.is_duplicate is True
        assert second.duplicate_of_article_id == first.id
        assert second.duplicate_reason in {"canonical_url", "content_fingerprint", "normalized_title"}


def test_news_list_filters_analysis_candidates(sqlite_session_local) -> None:
    with sqlite_session_local() as db:
        create_article(db, _article("ok", score=0.8, candidate=True))
        create_article(db, _article("noise", title="야구 경기 사진", query="야구", score=0.1, candidate=False))

        rows = list_articles(db, is_analysis_candidate=True)

        assert [row.external_id for row in rows] == ["ok"]


def test_unanalyzed_news_excludes_duplicate_and_noise_but_force_includes(sqlite_session_local) -> None:
    with sqlite_session_local() as db:
        create_article(db, _article("ok", score=0.8, candidate=True))
        create_article(db, _article("dup", score=0.8, candidate=False, duplicate=True))
        create_article(db, _article("noise", score=0.1, candidate=False))

        normal = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=10)
        forced = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=10, include_completed=True)

        assert {row.external_id for row in normal} == {"ok"}
        assert {row.external_id for row in forced} == {"ok", "dup", "noise"}


def test_dedupe_api_returns_counts(client) -> None:
    response = client.post("/news/dedupe/run")
    assert response.status_code == 200
    data = response.json()
    assert {"scanned_count", "duplicate_count", "noise_count", "analysis_candidate_count", "updated_count"} <= set(data)


def test_collect_news_saves_duplicate_with_flags(monkeypatch, sqlite_session_local) -> None:
    async def fake_provider(provider_name: str, query: str, display: int, sort: str):
        return [
            {
                "id": "1",
                "title": "AI 반도체 투자 확대",
                "description": "AI 반도체 투자 확대가 시장 관심을 받고 있습니다.",
                "link": "https://press.example.com/a?utm_source=x",
                "original_link": None,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "publisher": "Example",
                "raw_data": {"secret": "provider payload should not be stored"},
            },
            {
                "id": "2",
                "title": "[포토] AI 반도체 투자 확대",
                "description": "AI 반도체 투자 확대가 시장 관심을 받고 있습니다.",
                "link": "http://www.press.example.com/a",
                "original_link": None,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "publisher": "Example",
                "raw_data": {"secret": "provider payload should not be stored"},
            },
        ]

    import app.services.news_service as news_service

    monkeypatch.setattr(news_service, "_call_provider", fake_provider)

    with sqlite_session_local() as db:
        result = collect_news(db, NewsCollectionRequest(query="AI 반도체", display=2, sort="date", provider="mock"))
        rows = list_articles(db, limit=10)

        assert result["saved_count"] == 2
        assert result["duplicate_count"] == 1
        assert sum(1 for row in rows if row.is_duplicate) == 1
        assert all(row.raw_data == {"provider": "mock"} for row in rows)


def test_dashboard_has_news_quality_filters() -> None:
    source = Path("app/dashboard.py").read_text(encoding="utf-8")
    assert "중복 뉴스 표시" in source
    assert "저관련 뉴스 표시" in source
    assert "분석 후보만 보기" in source
    assert "is_analysis_candidate" in source
