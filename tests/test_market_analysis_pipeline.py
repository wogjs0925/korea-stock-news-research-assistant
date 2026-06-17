from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app import dashboard
from app.services import market_analysis_pipeline_service as pipeline


def _news_result(*, completed: int = 1, failed: int = 0, error_codes: list[str] | None = None) -> dict[str, Any]:
    return {
        "run_id": "ANALYSIS-TEST",
        "requested": completed + failed,
        "completed": completed,
        "failed": failed,
        "skipped": 0,
        "error_codes": error_codes or [],
    }


def _theme_result(*, status: str = "completed", selected_theme_count: int = 1) -> dict[str, Any]:
    return {
        "run_id": "THEME-TEST",
        "status": status,
        "source_count": 3,
        "selected_theme_count": selected_theme_count,
        "theme_ids": [1] if selected_theme_count else [],
    }


def _candidate_result(*, status: str = "completed") -> dict[str, Any]:
    return {
        "run_id": "THEMECAND-TEST",
        "status": status,
        "theme_count": 1,
        "stock_candidate_count": 2,
        "etf_candidate_count": 1,
        "ambiguous_count": 0,
        "unmatched_count": 0,
    }


def test_market_pipeline_runs_news_theme_and_candidates(monkeypatch, sqlite_session_local):
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_news(db, limit, provider, force):
        calls.append(("news", {"limit": limit, "provider": provider, "force": force}))
        return _news_result(completed=3)

    def fake_theme(db, window_hours, max_sources, provider):
        calls.append(("theme", {"window_hours": window_hours, "max_sources": max_sources, "provider": provider}))
        return _theme_result(selected_theme_count=2)

    def fake_candidates(db, **kwargs):
        calls.append(("candidates", kwargs))
        return _candidate_result()

    monkeypatch.setattr(pipeline, "run_analysis", fake_news)
    monkeypatch.setattr(pipeline, "run_theme_analysis", fake_theme)
    monkeypatch.setattr(pipeline, "generate_theme_candidates", fake_candidates)

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=20,
            max_theme_source_count=50,
            force_reanalyze=True,
            run_candidate_generation=True,
            include_weak_industry_candidates=True,
            include_watchlist_themes=True,
            include_leveraged_inverse_etfs=False,
            max_stock_candidates_per_theme=7,
            max_etf_candidates_per_theme=8,
            run_recommendations=False,
        )

    assert result["status"] == "completed"
    assert [name for name, _payload in calls] == ["news", "theme", "candidates"]
    assert calls[0][1] == {"limit": 20, "provider": "openai", "force": True}
    assert calls[1][1] == {"window_hours": 24, "max_sources": 50, "provider": "openai"}
    assert calls[2][1]["include_weak_industry_candidates"] is True
    assert calls[2][1]["include_watchlist_themes"] is True
    assert calls[2][1]["include_leveraged_inverse_etfs"] is False
    assert calls[2][1]["max_stock_candidates_per_theme"] == 7
    assert calls[2][1]["max_etf_candidates_per_theme"] == 8
    assert "news_selection" in result
    assert result["news_selection"]["selection_policy"]["analysis_candidates_only"] is True
    assert "selected_articles" in result["news_selection"]


def test_market_pipeline_continues_after_partial_news_success(monkeypatch, sqlite_session_local):
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: _news_result(completed=2, failed=1))
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: calls.append("theme") or _theme_result())
    monkeypatch.setattr(pipeline, "generate_theme_candidates", lambda *_args, **_kwargs: calls.append("candidates") or _candidate_result())

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=5,
            max_theme_source_count=10,
            run_recommendations=False,
        )

    assert result["status"] == "completed"
    assert calls == ["theme", "candidates"]


def test_market_pipeline_stops_when_news_has_no_completed_items(monkeypatch, sqlite_session_local):
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: _news_result(completed=0, failed=0))
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: calls.append("theme") or _theme_result())

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=5,
            max_theme_source_count=10,
            run_recommendations=False,
        )

    assert result["status"] == "insufficient_data"
    assert result["theme_analysis"] is None
    assert calls == []


def test_market_pipeline_stops_on_fatal_openai_news_error(monkeypatch, sqlite_session_local):
    monkeypatch.setattr(
        pipeline,
        "run_analysis",
        lambda *_args, **_kwargs: _news_result(completed=0, failed=1, error_codes=["OPENAI_AUTH_ERROR"]),
    )

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=5,
            max_theme_source_count=10,
            run_recommendations=False,
        )

    assert result["status"] == "failed"
    assert result["failed_stage"] == "news_analysis"
    assert result["candidate_generation"] is None


def test_market_pipeline_skips_candidates_when_theme_fails(monkeypatch, sqlite_session_local):
    called = {"candidates": False}
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: _news_result(completed=1))
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: _theme_result(status="failed", selected_theme_count=0))
    monkeypatch.setattr(pipeline, "generate_theme_candidates", lambda *_args, **_kwargs: called.__setitem__("candidates", True))

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=5,
            max_theme_source_count=10,
            run_recommendations=False,
        )

    assert result["status"] == "partial"
    assert result["failed_stage"] == "theme_analysis"
    assert called["candidates"] is False


def test_market_pipeline_reports_candidate_partial(monkeypatch, sqlite_session_local):
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: _news_result(completed=1))
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: _theme_result())
    monkeypatch.setattr(pipeline, "generate_theme_candidates", lambda *_args, **_kwargs: _candidate_result(status="failed"))

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=5,
            max_theme_source_count=10,
        )

    assert result["status"] == "partial"
    assert result["failed_stage"] == "candidate_generation"


def test_market_analysis_api_passes_validated_payload(monkeypatch, client: TestClient):
    from app.backend.routes import market_analysis as route

    captured: dict[str, Any] = {}

    def fake_pipeline(db, **kwargs):
        captured.update(kwargs)
        return {
            "run_id": "MARKET-TEST",
            "status": "completed",
            "failed_stage": None,
            "news_analysis": _news_result(completed=1),
            "theme_analysis": _theme_result(),
            "candidate_generation": _candidate_result(),
            "duration_ms": 1,
        }

    monkeypatch.setattr(route, "run_market_analysis_pipeline", fake_pipeline)

    response = client.post(
        "/market-analysis/run",
        json={
            "analysis_window_hours": 48,
            "max_news_analysis_count": 30,
            "max_theme_source_count": 60,
            "force_reanalyze": True,
            "run_candidate_generation": False,
            "include_weak_industry_candidates": True,
            "include_watchlist_themes": True,
            "include_leveraged_inverse_etfs": False,
            "max_stock_candidates_per_theme": 12,
            "max_etf_candidates_per_theme": 9,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert captured["analysis_window_hours"] == 48
    assert captured["max_news_analysis_count"] == 30
    assert captured["max_theme_source_count"] == 60
    assert captured["force_reanalyze"] is True
    assert captured["run_candidate_generation"] is False
    assert captured["include_weak_industry_candidates"] is True
    assert captured["include_watchlist_themes"] is True
    assert captured["include_leveraged_inverse_etfs"] is False
    assert captured["max_stock_candidates_per_theme"] == 12
    assert captured["max_etf_candidates_per_theme"] == 9


def test_dashboard_market_analysis_request_posts_payload(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"run_id": "MARKET-TEST", "status": "completed"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    payload = {
        "analysis_window_hours": 24,
        "max_news_analysis_count": 20,
        "max_theme_source_count": 50,
        "force_reanalyze": False,
        "run_candidate_generation": True,
        "include_weak_industry_candidates": False,
        "include_watchlist_themes": False,
        "include_leveraged_inverse_etfs": True,
        "max_stock_candidates_per_theme": 15,
        "max_etf_candidates_per_theme": 20,
    }
    monkeypatch.setattr(dashboard.httpx, "post", fake_post)

    result, error = dashboard.run_market_analysis_request(payload)

    assert error is None
    assert result == {"run_id": "MARKET-TEST", "status": "completed"}
    assert captured["url"].endswith("/market-analysis/run")
    assert captured["json"] == payload


def test_theme_page_uses_number_inputs_for_integrated_counts():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")

    assert "st.number_input(" in source
    assert 'key="theme_window_hours"' in source
    assert 'key="theme_news_analysis_count"' in source
    assert 'key="theme_max_sources"' in source
    assert 'key="market_max_stock_candidates"' in source
    assert 'key="market_max_etf_candidates"' in source


def test_market_analysis_ui_exposes_news_and_candidate_diagnostics():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")

    assert "news_selection" in source
    assert "selected_articles" in source
    assert "domestic_stocks" in source
    assert "domestic_etfs" in source
    assert "overseas_reference" in source
    assert "candidate_diagnostics" in source
    assert "price_impact_score" in source
    assert "investable_link_score" in source
    assert "관찰 테마" in source
    assert "위험 알림" in source
