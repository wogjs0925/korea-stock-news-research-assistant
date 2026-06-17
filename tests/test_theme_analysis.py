from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.main import app
from app.database.base import Base
from app.database.session import get_db
from app.models.news_analysis import NewsAnalysis
from app.models.news_article import NewsArticle
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.market_theme import MarketTheme
from app.models.theme_news_link import ThemeNewsLink
from app.providers.ai.openai_theme_analyzer import OpenAIThemeAnalyzer
from app.providers.ai import openai_theme_analyzer
from app.providers.ai import openai_news_analyzer
from app.providers.ai.openai_news_analyzer import OpenAIAuthError, OpenAIInvalidRequestError, OpenAIModelConfigError
from app.repositories.theme_analysis_repository import list_theme_source_analyses
from app.repositories.theme_analysis_repository import ensure_theme_tables_schema
from app.schemas.theme_analysis import SelectedThemeCandidate, ThemeEvidence, ThemeSelectionOutput
from app.services.theme_analysis_service import calculate_theme_score, run_theme_analysis, validate_theme_output


def _article(suffix: str, available_at: datetime | None = None, duplicate: bool = False) -> NewsArticle:
    return NewsArticle(
        provider="mock",
        external_id=f"theme-{suffix}",
        query="theme",
        title=f"Theme news {suffix}",
        description="description",
        link=f"https://example.com/theme-{suffix}",
        publisher=f"Publisher {suffix}",
        available_at=available_at or datetime.now(timezone.utc),
        title_normalized=f"theme news {suffix}",
        content_hash=f"theme-hash-{suffix}",
        is_duplicate=duplicate,
        raw_data={"raw": "not sent"},
    )


def _analysis(article: NewsArticle, suffix: str, relevant: bool = True, importance: float = 0.8) -> NewsAnalysis:
    return NewsAnalysis(
        news_article_id=article.id,
        analysis_run_id=f"analysis-run-{suffix}",
        model_name="gpt-5.4-mini",
        prompt_version="news-analysis-v1",
        status="completed",
        summary=f"Summary {suffix}",
        event_type="technology",
        impact_direction="positive",
        sentiment_score=0.5,
        importance_score=importance,
        novelty_score=0.7,
        market_relevance_score=0.8,
        confidence_score=0.9,
        time_horizon="short_term",
        candidate_themes_json=["AI infrastructure"],
        companies_json=[{"company_name": f"Company {suffix}"}],
        evidence_points_json=["evidence"],
        risk_factors_json=["risk"],
        is_investment_relevant=relevant,
    )


def _seed_analysis(db, suffix: str, **kwargs) -> NewsAnalysis:
    article = _article(suffix, kwargs.pop("available_at", None), kwargs.pop("duplicate", False))
    db.add(article)
    db.commit()
    analysis = _analysis(article, suffix, **kwargs)
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def test_theme_schema_allows_empty_lists_and_limits():
    output = ThemeSelectionOutput(
        market_overview="overview",
        themes=[
            SelectedThemeCandidate(
                theme_name="AI",
                theme_summary="summary",
                why_now="now",
                impact_direction="mixed",
                confidence_score=0.5,
                time_horizon="short_term",
                evidence=[],
            )
        ],
    )
    assert output.themes[0].related_companies == []
    assert output.themes[0].evidence == []


def test_theme_repository_filters_and_orders_sources(sqlite_session_local):
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        old = _seed_analysis(db, "old", available_at=now - timedelta(hours=30))
        duplicate = _seed_analysis(db, "dup", duplicate=True)
        weak = _seed_analysis(db, "weak", importance=0.1)
        selected = _seed_analysis(db, "selected", available_at=now - timedelta(hours=1))

        rows = list_theme_source_analyses(
            db,
            now - timedelta(hours=24),
            now,
            min_importance=0.3,
            min_market_relevance=0.3,
            max_sources=10,
        )

        ids = [row["news_analysis_id"] for row in rows]
        assert selected.id in ids
        assert old.id not in ids
        assert duplicate.id not in ids
        assert weak.id not in ids
        assert "raw_data" not in rows[0]


def test_validate_theme_output_removes_invalid_evidence_and_companies(sqlite_session_local):
    with sqlite_session_local() as db:
        first = _seed_analysis(db, "1")
        second = _seed_analysis(db, "2")
        sources = list_theme_source_analyses(
            db,
            datetime.now(timezone.utc) - timedelta(hours=24),
            datetime.now(timezone.utc),
            0.3,
            0.3,
            10,
        )
        output = ThemeSelectionOutput(
            market_overview="overview",
            themes=[
                SelectedThemeCandidate(
                    theme_name="AI infrastructure",
                    theme_summary="summary",
                    why_now="now",
                    impact_direction="positive",
                    confidence_score=0.7,
                    time_horizon="short_term",
                    related_companies=["Company 1", "Invented Corp"],
                    evidence=[
                        ThemeEvidence(news_analysis_id=first.id, relevance_score=0.9, reason="valid"),
                        ThemeEvidence(news_analysis_id=first.id, relevance_score=0.8, reason="duplicate"),
                        ThemeEvidence(news_analysis_id=9999, relevance_score=0.9, reason="invalid"),
                        ThemeEvidence(news_analysis_id=second.id, relevance_score=0.8, reason="valid"),
                    ],
                )
            ],
        )

        validated = validate_theme_output(output, sources)

        assert len(validated.themes) == 1
        assert [e.news_analysis_id for e in validated.themes[0].evidence] == [first.id, second.id]
        assert validated.themes[0].related_companies == ["Company 1"]


def test_calculate_theme_score_range_and_order(sqlite_session_local):
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        first = _seed_analysis(db, "score1", available_at=now - timedelta(hours=1), importance=0.9)
        second = _seed_analysis(db, "score2", available_at=now - timedelta(hours=2), importance=0.9)
        sources = list_theme_source_analyses(db, now - timedelta(hours=24), now, 0.3, 0.3, 10)
        source_by_id = {row["news_analysis_id"]: row for row in sources}
        theme = SelectedThemeCandidate(
            theme_name="High score",
            theme_summary="summary",
            why_now="now",
            impact_direction="positive",
            confidence_score=0.8,
            time_horizon="short_term",
            evidence=[
                ThemeEvidence(news_analysis_id=first.id, relevance_score=0.8, reason="a"),
                ThemeEvidence(news_analysis_id=second.id, relevance_score=0.8, reason="b"),
            ],
        )

        score = calculate_theme_score(theme, source_by_id, now)

        assert 0.0 <= score <= 1.0
        assert score > 0.5


def test_theme_service_insufficient_data_does_not_call_provider(monkeypatch, sqlite_session_local):
    def fail_provider():
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("app.services.theme_analysis_service.OpenAIThemeAnalyzer", fail_provider)
    with sqlite_session_local() as db:
        result = run_theme_analysis(db, provider="openai", max_sources=3)

        assert result["status"] == "insufficient_data"
        assert result["selected_theme_count"] == 0


def test_theme_service_mock_saves_themes_and_links(sqlite_session_local):
    with sqlite_session_local() as db:
        _seed_analysis(db, "mock1")
        _seed_analysis(db, "mock2")

        result = run_theme_analysis(db, provider="mock", max_sources=3)

        assert result["status"] == "completed"
        assert result["selected_theme_count"] == 1
        assert db.query(ThemeAnalysisRun).count() == 1
        assert db.query(MarketTheme).count() == 1
        saved_theme = db.query(MarketTheme).one()
        assert saved_theme.theme_bucket in {
            "investable_opportunity",
            "watchlist",
            "risk_alert",
            "macro_background",
            "low_actionability",
        }
        assert 0.0 <= saved_theme.price_impact_score <= 1.0
        assert 0.0 <= saved_theme.investable_link_score <= 1.0
        assert db.query(ThemeNewsLink).count() >= 2


def test_openai_theme_analyzer_request_shape(monkeypatch):
    class FakeResponses:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def parse(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                id="resp_theme",
                status="completed",
                output_parsed=ThemeSelectionOutput(market_overview="overview", themes=[]),
                usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
                latency_ms=4,
            )

    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: SimpleNamespace(openai_api_key="test", openai_model="gpt-5.4-mini", openai_timeout=30, openai_max_retries=0))
    client = SimpleNamespace(responses=FakeResponses())
    analyzer = OpenAIThemeAnalyzer(client=client)

    import asyncio

    output, meta = asyncio.run(analyzer.analyze([{"news_analysis_id": 1, "title": "한글"}], datetime.now(timezone.utc), datetime.now(timezone.utc)))

    kwargs = client.responses.calls[0]
    assert isinstance(kwargs["input"], list)
    assert all(isinstance(item["content"], str) for item in kwargs["input"])
    assert kwargs["text_format"] == ThemeSelectionOutput
    assert "한글" in kwargs["input"][1]["content"]
    assert output.market_overview == "overview"
    assert meta["tokens"]["total"] == 3


def test_theme_analyzer_uses_news_runtime_factory(monkeypatch):
    class FakeResponses:
        async def parse(self, **kwargs):
            return SimpleNamespace(
                id="resp_theme",
                status="completed",
                output_parsed=ThemeSelectionOutput(market_overview="overview", themes=[]),
                usage=None,
                latency_ms=1,
            )

    monkeypatch.setattr(openai_news_analyzer, "get_secret_value", lambda name: "runtime-key")
    monkeypatch.setattr(openai_news_analyzer, "runtime_openai_model", lambda: "runtime-model")
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: SimpleNamespace(openai_api_key="old-env-key", openai_model="old-model", openai_max_retries=0))
    client = SimpleNamespace(responses=FakeResponses())
    analyzer = OpenAIThemeAnalyzer(client=client)

    assert analyzer._model == "runtime-model"


def test_openai_theme_invalid_request_keeps_safe_diagnostics(monkeypatch):
    from openai import BadRequestError

    class FakeResponses:
        async def parse(self, **kwargs):
            response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.test/responses"))
            raise BadRequestError(
                "invalid schema",
                response=response,
                body={"error": {"code": "invalid_json_schema", "type": "invalid_request_error", "param": "text.format.schema"}},
            )

    monkeypatch.setattr(
        openai_news_analyzer,
        "get_settings",
        lambda: SimpleNamespace(
            openai_api_key="test",
            openai_model="gpt-5.4-mini",
            openai_timeout=30,
            openai_max_retries=0,
            theme_analysis_prompt_version="theme-analysis-v1",
        ),
    )
    client = SimpleNamespace(responses=FakeResponses())
    analyzer = OpenAIThemeAnalyzer(client=client)

    import asyncio

    try:
        asyncio.run(analyzer.analyze([{"news_analysis_id": 1, "title": "제목"}], datetime.now(timezone.utc), datetime.now(timezone.utc)))
    except OpenAIInvalidRequestError as exc:
        context = exc.diagnostic_context
    else:
        raise AssertionError("expected OpenAIInvalidRequestError")

    assert context["schema_name"] == "ThemeSelectionOutput"
    assert context["input_item_count"] == 2
    assert context["prompt_version"] == "theme-analysis-v1"
    assert context["original_error_code"] == "invalid_json_schema"
    assert context["original_error_type"] == "invalid_request_error"
    assert context["original_param"] == "text.format.schema"
    assert "test" not in str(context)


def test_theme_service_auth_error_is_openai_auth_error(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, sources, window_start, window_end):
            raise OpenAIAuthError(
                OpenAIAuthError.user_message,
                http_status_code=401,
                diagnostics={"http_status_code": 401, "original_exception_type": "AuthenticationError", "retryable": False},
            )

    monkeypatch.setattr("app.services.theme_analysis_service.OpenAIThemeAnalyzer", lambda: FakeAnalyzer())
    with sqlite_session_local() as db:
        for index in range(3):
            _seed_analysis(db, f"auth-{index}")

        result = run_theme_analysis(db, provider="openai", max_sources=3)
        run = db.query(ThemeAnalysisRun).one()

    assert result["status"] == "failed"
    assert result["error_code"] == "OPENAI_AUTH_ERROR"
    assert run.error_code == "OPENAI_AUTH_ERROR"
    assert run.error_message == "OpenAI 인증 오류가 발생했습니다."


def test_theme_service_empty_themes_is_not_completed(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, sources, window_start, window_end):
            return ThemeSelectionOutput(market_overview="overview", themes=[]), {"tokens": {}, "latency_ms": 1, "model_name": "mock-model"}

    monkeypatch.setattr("app.services.theme_analysis_service.OpenAIThemeAnalyzer", lambda: FakeAnalyzer())
    with sqlite_session_local() as db:
        for index in range(3):
            _seed_analysis(db, f"empty-{index}")

        result = run_theme_analysis(db, provider="openai", max_sources=3)
        run = db.query(ThemeAnalysisRun).one()

    assert result["status"] == "failed"
    assert result["error_code"] == "THEME_ANALYSIS_EMPTY_THEMES"
    assert run.selected_theme_count == 0


def test_theme_openai_test_endpoint_uses_safe_failure(monkeypatch, client: TestClient, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, sources, window_start, window_end):
            raise OpenAIModelConfigError(OpenAIModelConfigError.user_message)

    monkeypatch.setattr("app.services.theme_analysis_service.OpenAIThemeAnalyzer", lambda: FakeAnalyzer())
    with sqlite_session_local() as db:
        for index in range(3):
            _seed_analysis(db, f"test-openai-{index}")

    response = client.post("/themes/test-openai")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "failed"
    assert body["error_code"] == "OPENAI_MODEL_CONFIG_ERROR"
    assert "diagnostics" in body


def test_themes_api_mock_and_latest(client: TestClient):
    response = client.post("/themes/run", json={"provider": "mock", "max_sources": 3})
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data"

    latest = client.get("/themes/latest")
    assert latest.status_code == 200
    assert latest.json()["run"]["status"] == "insufficient_data"


def test_legacy_theme_run_table_missing_duration_ms_is_upgraded():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE theme_analysis_runs (
                id INTEGER PRIMARY KEY,
                run_id VARCHAR(128) NOT NULL,
                model_name VARCHAR(128) NOT NULL,
                prompt_version VARCHAR(64) NOT NULL,
                window_start DATETIME NOT NULL,
                window_end DATETIME NOT NULL,
                requested_source_count INTEGER NOT NULL,
                selected_source_count INTEGER NOT NULL,
                selected_theme_count INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL,
                market_overview TEXT,
                insufficient_data_reason TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                latency_ms INTEGER,
                error_message TEXT,
                started_at DATETIME NOT NULL,
                completed_at DATETIME,
                created_at DATETIME NOT NULL
            )
            """
        )
    try:
        with Session() as db:
            ensure_theme_tables_schema(db)
        columns = [column["name"] for column in inspect(engine).get_columns("theme_analysis_runs")]
        assert "duration_ms" in columns
    finally:
        engine.dispose()


def test_themes_api_mock_persists_themes_with_seeded_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with Session() as db:
            _seed_analysis(db, "api-mock-1")
            _seed_analysis(db, "api-mock-2")
        with TestClient(app) as test_client:
            response = test_client.post("/themes/run", json={"provider": "mock", "max_sources": 3})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            assert body["selected_theme_count"] == 1
            assert body["theme_ids"]
        with Session() as db:
            assert db.query(ThemeAnalysisRun).count() == 1
            assert db.query(MarketTheme).count() == 1
            assert db.query(ThemeNewsLink).count() >= 2
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
