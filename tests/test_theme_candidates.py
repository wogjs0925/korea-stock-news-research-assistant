from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.market_theme import MarketTheme
from app.models.etf_holding import ETFHolding
from app.models.security import Security
from app.models.security_alias import SecurityAlias
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.services.theme_candidate_service import calculate_candidate_scores, generate_theme_candidates, theme_candidates_for_api
from app.utils.security_names import generate_security_key, normalize_company_name


def _security(
    *,
    ticker: str,
    name: str,
    country_code: str = "US",
    asset_type: str = "stock",
    exchange_code: str = "XNAS",
    english_name: str | None = None,
    issuer_name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    is_active: bool = True,
    is_recommendation_eligible: bool = True,
    is_leveraged: bool = False,
    is_inverse: bool = False,
) -> Security:
    return Security(
        security_key=generate_security_key(country_code, exchange_code, ticker),
        country_code=country_code,
        asset_type=asset_type,
        exchange_code=exchange_code,
        exchange_name=exchange_code,
        ticker=ticker,
        name=name,
        english_name=english_name,
        normalized_name=normalize_company_name(name),
        currency="USD" if country_code == "US" else "KRW",
        issuer_name=issuer_name,
        sector=sector,
        industry=industry,
        is_active=is_active,
        is_recommendation_eligible=is_recommendation_eligible,
        is_leveraged=is_leveraged,
        is_inverse=is_inverse,
        source="test",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _theme_run() -> ThemeAnalysisRun:
    now = datetime.now(timezone.utc)
    return ThemeAnalysisRun(
        run_id="THEME-TEST",
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


def _theme(run: ThemeAnalysisRun, companies: list[str] | None = None, industries: list[str] | None = None) -> MarketTheme:
    return MarketTheme(
        theme_run_id=run.id,
        rank=1,
        theme_name="AI infrastructure",
        normalized_theme_name="ai infrastructure",
        theme_summary="AI semiconductor cloud datacenter demand",
        why_now="AI cloud investment is rising",
        impact_direction="positive",
        confidence_score=0.8,
        calculated_score=0.9,
        actionability_score=0.9,
        price_impact_score=0.85,
        investable_link_score=0.85,
        is_investable_theme=True,
        theme_bucket="investable_opportunity",
        time_horizon="short_term",
        related_industries_json=industries if industries is not None else ["semiconductor", "cloud"],
        related_companies_json=companies if companies is not None else ["NVIDIA", "Apple"],
        risk_factors_json=[],
        evidence_count=3,
        source_publisher_count=2,
    )


def _seed_theme(db, companies: list[str] | None = None, industries: list[str] | None = None) -> MarketTheme:
    run = _theme_run()
    db.add(run)
    db.commit()
    theme = _theme(run, companies, industries)
    db.add(theme)
    db.commit()
    db.refresh(theme)
    return theme


def test_company_exact_alias_issuer_and_unmatched_candidates(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, ["NVIDIA", "Apple Alias", "IBM Corp", "QIMC"])
        nvda = _security(ticker="NVDA", name="NVIDIA", issuer_name="NVIDIA Corporation", sector="Technology")
        apple = _security(ticker="AAPL", name="Apple", issuer_name="Apple Inc", sector="Technology")
        ibm = _security(ticker="IBM", name="International Business Machines", issuer_name="IBM Corp", sector="Technology")
        db.add_all([nvda, apple, ibm])
        db.commit()
        db.add(SecurityAlias(security_id=apple.id, alias="Apple Alias", normalized_alias=normalize_company_name("Apple Alias"), alias_type="legal_name", source="test"))
        db.commit()

        result = generate_theme_candidates(db, theme_id=theme.id)
        rows = theme_candidates_for_api(db, theme.id)

    assert result["status"] == "completed"
    methods = {row["name"]: row["match_method"] for row in rows}
    assert methods["NVIDIA"] == "normalized_name_exact"
    assert methods["Apple"] == "alias_exact"
    assert methods["International Business Machines"] == "sec_issuer_name_exact"
    assert any(row["name"] == "QIMC" and row["match_status"] == "unmatched" for row in rows)


def test_ambiguous_inactive_and_ineligible_handling(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, ["Acme", "InactiveCo", "BlockedCo"])
        db.add_all(
            [
                _security(ticker="ACM1", name="Acme", country_code="US"),
                _security(ticker="ACM2", name="Acme", country_code="KR", exchange_code="XKRX"),
                _security(ticker="INAC", name="InactiveCo", is_active=False),
                _security(ticker="BLCK", name="BlockedCo", is_recommendation_eligible=False),
            ]
        )
        db.commit()

        generate_theme_candidates(db, theme_id=theme.id)
        rows = theme_candidates_for_api(db, theme.id)

    assert any(row["name"] == "Acme" and row["match_status"] == "ambiguous" for row in rows)
    assert not any(row["ticker"] in {"INAC", "BLCK"} for row in rows)


def test_etf_keyword_search_and_risk_flags(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, companies=[], industries=["semiconductor"])
        db.add_all(
            [
                _security(ticker="SMH", name="VanEck Semiconductor ETF", asset_type="etf", industry="semiconductor"),
                _security(ticker="SOXL", name="Direxion Semiconductor Bull 3X ETF", asset_type="etf", industry="semiconductor", is_leveraged=True),
                _security(ticker="SOXS", name="Direxion Semiconductor Bear 3X ETF", asset_type="etf", industry="semiconductor", is_inverse=True),
            ]
        )
        db.commit()

        generate_theme_candidates(db, theme_id=theme.id, include_leveraged_inverse_etfs=True)
        rows = theme_candidates_for_api(db, theme.id, asset_type="etf")

    assert any(row["ticker"] == "SMH" for row in rows)
    assert any(row["ticker"] == "SOXL" and "leveraged_etf" in row["risk_flags"] for row in rows)
    assert any(row["ticker"] == "SOXS" and "inverse_etf" in row["risk_flags"] for row in rows)


def test_etf_holdings_exposure_boosts_theme_candidate(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, companies=["삼성전자", "SK하이닉스"], industries=["반도체"])
        samsung = _security(ticker="005930", name="삼성전자", country_code="KR", exchange_code="XKRX")
        hynix = _security(ticker="000660", name="SK하이닉스", country_code="KR", exchange_code="XKRX")
        etf = _security(ticker="091160", name="KODEX 반도체", country_code="KR", exchange_code="XKRX", asset_type="etf", industry="반도체")
        broad = _security(ticker="069500", name="KODEX 200", country_code="KR", exchange_code="XKRX", asset_type="etf", industry="시장대표")
        db.add_all([samsung, hynix, etf, broad])
        db.commit()
        db.add_all(
            [
                ETFHolding(etf_security_id=etf.id, holding_security_id=samsung.id, holding_name="삼성전자", holding_ticker="005930", country_code="KR", weight=22.0, source="test"),
                ETFHolding(etf_security_id=etf.id, holding_security_id=hynix.id, holding_name="SK하이닉스", holding_ticker="000660", country_code="KR", weight=18.0, source="test"),
            ]
        )
        db.commit()

        result = generate_theme_candidates(db, theme_id=theme.id)
        rows = theme_candidates_for_api(db, theme.id, asset_type="etf")

    assert result["etf_candidate_count"] >= 1
    holding_row = next(row for row in rows if row["ticker"] == "091160")
    assert holding_row["match_method"] == "holding_exposure_etf"
    assert holding_row["relevance_score"] >= 0.9


def test_etf_tag_fallback_when_holdings_are_missing(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, companies=["삼성전자"], industries=["반도체"])
        db.add(_security(ticker="SMH", name="VanEck Semiconductor ETF", asset_type="etf", industry="semiconductor"))
        db.commit()

        result = generate_theme_candidates(db, theme_id=theme.id)
        rows = theme_candidates_for_api(db, theme.id, asset_type="etf")

    assert result["etf_candidate_count"] >= 1
    assert any(row["match_method"] == "keyword_etf" for row in rows)


def test_weak_industry_candidates_are_opt_in_and_no_duplicate_on_rerun(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, companies=[], industries=["cloud"])
        db.add(_security(ticker="CLOUD", name="Cloud Infra", sector="cloud"))
        db.commit()

        generate_theme_candidates(db, theme_id=theme.id)
        assert theme_candidates_for_api(db, theme.id, asset_type="stock") == []

        generate_theme_candidates(db, theme_id=theme.id, include_weak_industry_candidates=True)
        generate_theme_candidates(db, theme_id=theme.id, include_weak_industry_candidates=True)
        rows = theme_candidates_for_api(db, theme.id, asset_type="stock")
        stored = db.query(ThemeSecurityCandidate).filter(ThemeSecurityCandidate.market_theme_id == theme.id).all()

    assert len(rows) == 1
    assert rows[0]["match_method"] == "weak_industry_keyword"
    assert len(stored) == 1


def test_candidate_generation_respects_stock_and_etf_limits(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, ["NVIDIA", "Apple"], ["semiconductor"])
        db.add_all(
            [
                _security(ticker="NVDA", name="NVIDIA", sector="semiconductor"),
                _security(ticker="AAPL", name="Apple", sector="semiconductor"),
                _security(ticker="SMH", name="VanEck Semiconductor ETF", asset_type="etf", industry="semiconductor"),
                _security(ticker="SOXX", name="iShares Semiconductor ETF", asset_type="etf", industry="semiconductor"),
            ]
        )
        db.commit()

        result = generate_theme_candidates(
            db,
            theme_id=theme.id,
            max_stock_candidates_per_theme=1,
            max_etf_candidates_per_theme=1,
        )

    assert result["status"] == "completed"
    assert result["stock_candidate_count"] == 1
    assert result["etf_candidate_count"] == 1


def test_scores_are_clamped():
    theme = _theme(_theme_run())
    scores = calculate_candidate_scores(
        theme=theme,
        match_score=2.0,
        evidence_count=99,
        relevance_score=2.0,
        risk_penalty_score=2.0,
    )
    assert 0.0 <= scores.final_candidate_score <= 1.0
    assert scores.match_score == 1.0
    assert scores.risk_penalty_score == 1.0


def test_insufficient_data_and_api(client):
    body = client.post("/themes/candidates/run", json={}).json()
    assert body["status"] == "insufficient_data"


def test_risk_alert_theme_is_excluded_from_candidate_generation(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, ["NVIDIA"], ["semiconductor"])
        theme.theme_bucket = "risk_alert"
        theme.is_investable_theme = False
        theme.actionability_score = 0.2
        theme.price_impact_score = 0.2
        theme.investable_link_score = 0.2
        db.add(_security(ticker="NVDA", name="NVIDIA", sector="semiconductor"))
        db.commit()

        result = generate_theme_candidates(db, theme_id=theme.id)
        rows = theme_candidates_for_api(db, theme.id)

    assert result["status"] == "completed"
    assert result["stock_candidate_count"] == 0
    assert rows == []


def test_watchlist_theme_requires_opt_in(sqlite_session_local):
    with sqlite_session_local() as db:
        theme = _seed_theme(db, ["NVIDIA"], ["semiconductor"])
        theme.theme_bucket = "watchlist"
        theme.is_investable_theme = False
        theme.actionability_score = 0.5
        theme.price_impact_score = 0.5
        theme.investable_link_score = 0.45
        db.add(_security(ticker="NVDA", name="NVIDIA", sector="semiconductor"))
        db.commit()

        default_result = generate_theme_candidates(db, theme_id=theme.id)
        opt_in_result = generate_theme_candidates(db, theme_id=theme.id, include_watchlist_themes=True)
        rows = theme_candidates_for_api(db, theme.id)

    assert default_result["stock_candidate_count"] == 0
    assert opt_in_result["stock_candidate_count"] == 1
    assert any(row["ticker"] == "NVDA" for row in rows)
