from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.models.market_theme import MarketTheme
from app.models.security import Security
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.services import market_analysis_pipeline_service as pipeline
from app.services.recommendation_service import calculate_recommendation_score, latest_recommendations, run_recommendations
from app.utils.security_names import generate_security_key, normalize_company_name


def _security(
    *,
    ticker: str,
    name: str,
    country_code: str = "US",
    asset_type: str = "stock",
    exchange_code: str = "XNAS",
    eligible: bool = True,
    active: bool = True,
    leveraged: bool = False,
    inverse: bool = False,
) -> Security:
    return Security(
        security_key=generate_security_key(country_code, exchange_code, ticker),
        country_code=country_code,
        asset_type=asset_type,
        exchange_code=exchange_code,
        exchange_name=exchange_code,
        ticker=ticker,
        name=name,
        normalized_name=normalize_company_name(name),
        currency="USD" if country_code == "US" else "KRW",
        is_active=active,
        is_recommendation_eligible=eligible,
        is_leveraged=leveraged,
        is_inverse=inverse,
        source="test",
    )


def _theme_run() -> ThemeAnalysisRun:
    now = datetime.now(timezone.utc)
    return ThemeAnalysisRun(
        run_id="THEME-REC",
        model_name="mock",
        prompt_version="theme-v1",
        window_start=now - timedelta(hours=1),
        window_end=now,
        requested_source_count=3,
        selected_source_count=3,
        selected_theme_count=1,
        status="completed",
        started_at=now,
        completed_at=now,
    )


def _theme(run: ThemeAnalysisRun) -> MarketTheme:
    return MarketTheme(
        theme_run_id=run.id,
        rank=1,
        theme_name="AI infrastructure",
        normalized_theme_name="ai infrastructure",
        theme_summary="AI semiconductor cloud demand",
        why_now="Investment is rising",
        impact_direction="positive",
        confidence_score=0.8,
        calculated_score=0.9,
        time_horizon="short_term",
        related_industries_json=["semiconductor"],
        related_companies_json=["NVIDIA"],
        risk_factors_json=["valuation risk"],
        evidence_count=3,
        source_publisher_count=2,
    )


def _candidate(theme: MarketTheme, security: Security | None, *, status: str = "matched", score: float = 0.8) -> ThemeSecurityCandidate:
    return ThemeSecurityCandidate(
        market_theme_id=theme.id,
        security_id=security.id if security else None,
        source_company_name=security.name if security else "Unknown",
        source_keyword=security.name if security else "Unknown",
        source_type="company_name",
        match_score=0.9,
        relevance_score=0.8,
        theme_fit_score=0.8,
        evidence_score=0.7,
        liquidity_proxy_score=0.5,
        risk_penalty_score=0.1,
        final_candidate_score=score,
        match_method="normalized_name_exact",
        match_status=status,
        country_code=security.country_code if security else None,
        asset_type=security.asset_type if security else None,
        evidence_count=2,
        reason_summary="candidate evidence",
        matched_evidence_json=[],
        risk_flags_json=[],
    )


def _seed(db):
    run = _theme_run()
    db.add(run)
    db.commit()
    theme = _theme(run)
    db.add(theme)
    db.commit()
    return run, theme


def test_recommendation_insufficient_data(sqlite_session_local):
    with sqlite_session_local() as db:
        result = run_recommendations(db, stock_country_scope="KR_AND_US")

    assert result["status"] == "insufficient_data"
    assert result["error_code"] == "RECOMMENDATION_INSUFFICIENT_DATA"


def test_recommendation_selects_only_matched_active_eligible(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        good = _security(ticker="NVDA", name="NVIDIA")
        ambiguous = _security(ticker="AMB", name="Ambiguous")
        inactive = _security(ticker="OLD", name="Inactive", active=False)
        blocked = _security(ticker="BLK", name="Blocked", eligible=False)
        db.add_all([good, ambiguous, inactive, blocked])
        db.commit()
        db.add_all(
            [
                _candidate(theme, good),
                _candidate(theme, ambiguous, status="ambiguous"),
                _candidate(theme, inactive),
                _candidate(theme, blocked),
            ]
        )
        db.commit()

        result = run_recommendations(db, stock_country_scope="KR_AND_US")
        latest = latest_recommendations(db)

    assert result["status"] == "completed"
    assert result["recommended_stock_count"] == 1
    items = latest["themes"][0]["stocks"]
    assert [item["ticker"] for item in items] == ["NVDA"]
    excluded_flags = {flag for item in latest["themes"][0]["excluded"] for flag in item["exclusion_flags"]}
    assert "ambiguous_match" in excluded_flags
    assert "inactive_security" in excluded_flags
    assert "recommendation_excluded_security" in excluded_flags


def test_recommendation_limits_stocks_and_etfs(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        securities = [
            _security(ticker=f"S{i}", name=f"Stock {i}", asset_type="stock")
            for i in range(5)
        ] + [
            _security(ticker=f"E{i}", name=f"ETF {i}", asset_type="etf")
            for i in range(4)
        ]
        db.add_all(securities)
        db.commit()
        db.add_all([_candidate(theme, sec, score=0.9 - idx * 0.01) for idx, sec in enumerate(securities)])
        db.commit()

        result = run_recommendations(db, max_stocks_per_theme=3, max_etfs_per_theme=2, stock_country_scope="KR_AND_US")

    assert result["recommended_stock_count"] == 3
    assert result["recommended_etf_count"] == 2
    assert result["excluded_count"] > 0


def test_leveraged_and_inverse_etfs_default_excluded_and_optional(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        leveraged = _security(ticker="LEV", name="Leveraged ETF", asset_type="etf", leveraged=True)
        inverse = _security(ticker="INV", name="Inverse ETF", asset_type="etf", inverse=True)
        db.add_all([leveraged, inverse])
        db.commit()
        db.add_all([_candidate(theme, leveraged), _candidate(theme, inverse)])
        db.commit()

        default_result = run_recommendations(db)
        included_result = run_recommendations(db, include_leveraged_inverse_etfs=True)
        latest = latest_recommendations(db)

    assert default_result["recommended_etf_count"] == 0
    assert included_result["recommended_etf_count"] == 2
    risk_flags = {flag for item in latest["themes"][0]["etfs"] for flag in item["risk_flags"]}
    assert {"leveraged_etf", "inverse_etf"} <= risk_flags


def test_low_score_and_missing_evidence_are_excluded(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        low = _security(ticker="LOW", name="Low Score")
        db.add(low)
        db.commit()
        candidate = _candidate(theme, low, score=0.1)
        candidate.evidence_score = 0.0
        candidate.evidence_count = 0
        db.add(candidate)
        db.commit()

        result = run_recommendations(db, min_candidate_score=0.35, min_evidence_score=0.1, stock_country_scope="KR_AND_US")
        latest = latest_recommendations(db)

    assert result["status"] == "insufficient_candidates"
    flags = latest["themes"][0]["excluded"][0]["exclusion_flags"]
    assert "low_candidate_score" in flags
    assert "insufficient_evidence" in flags


def test_recommendation_default_stock_scope_is_kr_only(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        kr = _security(ticker="005930", name="Samsung Electronics", country_code="KR", exchange_code="XKRX")
        us = _security(ticker="NVDA", name="NVIDIA")
        db.add_all([kr, us])
        db.commit()
        db.add_all([_candidate(theme, kr, score=0.8), _candidate(theme, us, score=0.9)])
        db.commit()

        result = run_recommendations(db)
        latest = latest_recommendations(db)

    assert result["recommended_stock_count"] == 1
    assert latest["themes"][0]["stocks"][0]["ticker"] == "005930"
    excluded_flags = {flag for item in latest["themes"][0]["overseas_reference"] for flag in item["exclusion_flags"]}
    assert "overseas_reference_stock" in excluded_flags
    diagnostics = latest["themes"][0]["candidate_diagnostics"]
    assert diagnostics["domestic_stock_candidate_count"] == 1
    assert diagnostics["us_stock_candidate_count"] == 1
    assert diagnostics["selected_domestic_stock_count"] == 1
    assert "only_us_candidates" not in diagnostics["candidate_exclusion_reasons"]


def test_recommendation_diagnostics_explain_only_us_candidates(sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        us = _security(ticker="NVDA", name="NVIDIA")
        db.add(us)
        db.commit()
        db.add(_candidate(theme, us, score=0.9))
        db.commit()

        result = run_recommendations(db)
        latest = latest_recommendations(db)

    assert result["status"] == "insufficient_candidates"
    diagnostics = latest["themes"][0]["candidate_diagnostics"]
    assert diagnostics["domestic_stock_candidate_count"] == 0
    assert diagnostics["us_stock_candidate_count"] == 1
    assert "only_us_candidates" in diagnostics["candidate_exclusion_reasons"]
    assert "no_kr_stock_candidate" in diagnostics["candidate_exclusion_reasons"]
    assert latest["themes"][0]["overseas_reference"]


def test_recommendation_score_is_clamped(sqlite_session_local):
    with sqlite_session_local() as db:
        run, theme = _seed(db)
        security = _security(ticker="CLMP", name="Clamp")
        db.add(security)
        db.commit()
        candidate = _candidate(theme, security, score=2.0)
        assert 0.0 <= calculate_recommendation_score(candidate, theme, diversification_score=1.0) <= 1.0


def test_recommendation_api_and_latest(client: TestClient, sqlite_session_local):
    with sqlite_session_local() as db:
        _run, theme = _seed(db)
        security = _security(ticker="API", name="API Corp")
        db.add(security)
        db.commit()
        db.add(_candidate(theme, security))
        db.commit()

    response = client.post(
        "/recommendations/run",
        json={"max_stocks_per_theme": 1, "max_etfs_per_theme": 1, "stock_country_scope": "KR_AND_US"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    latest = client.get("/recommendations/latest")
    assert latest.status_code == 200
    assert latest.json()["themes"][0]["stocks"][0]["ticker"] == "API"


def test_market_analysis_pipeline_runs_recommendations(monkeypatch, sqlite_session_local):
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: {"completed": 1, "failed": 0, "error_codes": []})
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: {"status": "completed", "selected_theme_count": 1})
    monkeypatch.setattr(pipeline, "generate_theme_candidates", lambda *_args, **_kwargs: {"status": "completed"})
    monkeypatch.setattr(
        pipeline,
        "run_recommendation_engine",
        lambda *_args, **_kwargs: calls.append("recommend") or {"status": "completed", "recommended_stock_count": 1, "recommended_etf_count": 0},
    )

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=1,
            max_theme_source_count=3,
            run_recommendations=True,
        )

    assert result["status"] == "completed"
    assert calls == ["recommend"]
    assert result["recommendations"]["recommended_stock_count"] == 1


def test_market_analysis_pipeline_skips_recommendations_when_candidates_fail(monkeypatch, sqlite_session_local):
    called = {"recommend": False}
    monkeypatch.setattr(pipeline, "run_analysis", lambda *_args, **_kwargs: {"completed": 1, "failed": 0, "error_codes": []})
    monkeypatch.setattr(pipeline, "run_theme_analysis", lambda *_args, **_kwargs: {"status": "completed", "selected_theme_count": 1})
    monkeypatch.setattr(pipeline, "generate_theme_candidates", lambda *_args, **_kwargs: {"status": "failed"})
    monkeypatch.setattr(pipeline, "run_recommendation_engine", lambda *_args, **_kwargs: called.__setitem__("recommend", True))

    with sqlite_session_local() as db:
        result = pipeline.run_market_analysis_pipeline(
            db,
            analysis_window_hours=24,
            max_news_analysis_count=1,
            max_theme_source_count=3,
            run_recommendations=True,
        )

    assert result["status"] == "partial"
    assert result["recommendations"] is None
    assert called["recommend"] is False
