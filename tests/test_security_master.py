from __future__ import annotations

from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.models.market_theme import MarketTheme
from app.models.security import Security
from app.models.security_alias import SecurityAlias
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.providers.securities.krx_kr import transform_krx_rows
from app.providers.securities.sec_us import transform_sec_company_tickers
from app.repositories.security_repository import (
    list_securities,
    upsert_security,
)
from app.schemas.security import SecurityIn
from app.services.security_master_service import sync_security_master
from app.services.security_alias_service import backfill_security_aliases
from app.services.security_match_service import match_security
from app.services.theme_security_matching_service import match_theme_securities
from app.utils.security_names import generate_security_key, normalize_company_name, normalize_ticker


def _security(
    ticker: str,
    name: str,
    country_code: str = "US",
    exchange_code: str = "XNAS",
    asset_type: str = "stock",
    english_name: str | None = None,
) -> SecurityIn:
    return SecurityIn(
        country_code=country_code,
        asset_type=asset_type,
        exchange_code=exchange_code,
        exchange_name="NASDAQ" if exchange_code == "XNAS" else "KOSPI",
        ticker=ticker,
        local_code=ticker if country_code == "KR" else None,
        name=name,
        english_name=english_name or name,
        currency="USD" if country_code == "US" else "KRW",
        source="test",
        aliases=[
            {
                "alias": english_name or name,
                "normalized_alias": normalize_company_name(english_name or name),
                "alias_type": "english_name",
                "language": "en",
            },
            {
                "alias": ticker,
                "normalized_alias": normalize_ticker(ticker),
                "alias_type": "ticker_alias",
                "language": "en",
            },
        ],
    )


def test_security_key_generation_distinguishes_country_and_exchange():
    assert generate_security_key("KR", "XKRX", "005930") == "KR:XKRX:005930"
    assert generate_security_key("US", "XNAS", "nvda") == "US:XNAS:NVDA"
    assert generate_security_key("US", "XNYS", "IBM") != generate_security_key("US", "XNAS", "IBM")


def test_name_and_ticker_normalization():
    assert normalize_ticker(" nvda ") == "NVDA"
    assert normalize_company_name("NVIDIA Corporation") == "nvidia"
    assert normalize_company_name("㈜ 삼성전자") == "삼성전자"


def test_provider_transforms_stock_and_etf_types():
    us_rows = transform_sec_company_tickers(
        {
            "0": {"ticker": "NVDA", "title": "NVIDIA Corporation", "exchange": "Nasdaq", "cik_str": 1045810},
            "1": {"ticker": "SPY", "title": "SPDR S&P 500 ETF Trust", "exchange": "NYSE", "cik_str": 884394},
        }
    )
    kr_rows = transform_krx_rows(
        [
            {"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"},
            {"종목코드": "069500", "종목명": "KODEX 200", "시장구분": "ETF"},
        ]
    )

    assert {row.asset_type for row in us_rows} == {"stock", "etf"}
    assert {row.asset_type for row in kr_rows} == {"stock", "etf"}


def test_sync_creates_updates_and_deactivates(monkeypatch, sqlite_session_local):
    class FirstProvider:
        name = "test"
        country_code = "US"

        async def fetch_securities(self):
            return [_security("AAA", "Alpha Inc"), _security("BBB", "Beta Corp")]

    class SecondProvider:
        name = "test"
        country_code = "US"

        async def fetch_securities(self):
            return [_security("AAA", "Alpha Corporation")]

    with sqlite_session_local() as db:
        monkeypatch.setattr("app.services.security_master_service.get_provider", lambda _name: FirstProvider())
        first = sync_security_master(db, "mock")
        monkeypatch.setattr("app.services.security_master_service.get_provider", lambda _name: SecondProvider())
        second = sync_security_master(db, "mock")

        assert first["created_count"] == 2
        assert second["updated_count"] == 1
        assert second["deactivated_count"] == 1
        rows = list_securities(db, is_active=None)
        assert len(rows) == 2
        assert [row for row in rows if row.ticker == "BBB"][0].is_active is False


def test_ticker_alias_country_hint_and_ambiguous_matching(sqlite_session_local):
    with sqlite_session_local() as db:
        us, _ = upsert_security(db, _security("ABC", "Acme Corporation", country_code="US", exchange_code="XNAS"))
        kr, _ = upsert_security(db, _security("ABC", "Acme Corporation", country_code="KR", exchange_code="XKRX"))

        ticker_match = match_security(db, "ignored", ticker="ABC", country_code="US")
        country_match = match_security(db, "Acme Corporation", country_code="KR")
        ambiguous = match_security(db, "Acme Corporation")

        assert ticker_match[0].security_id == us.id
        assert ticker_match[0].match_method == "ticker_exact"
        assert country_match[0].security_id == kr.id
        assert ambiguous[0].ambiguity_status == "ambiguous"


def test_unmatched_when_score_below_threshold(sqlite_session_local):
    with sqlite_session_local() as db:
        upsert_security(db, _security("NVDA", "NVIDIA Corporation"))
        result = match_security(db, "Completely Different Name")
        assert result[0].ambiguity_status == "unmatched"


def test_theme_company_matching_does_not_create_missing_security(sqlite_session_local):
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        security, _ = upsert_security(db, _security("NVDA", "NVIDIA Corporation"))
        run = ThemeAnalysisRun(
            run_id="theme-run-test",
            model_name="mock",
            prompt_version="theme-analysis-v1",
            window_start=now,
            window_end=now,
            status="completed",
        )
        db.add(run)
        db.commit()
        theme = MarketTheme(
            theme_run_id=run.id,
            rank=1,
            theme_name="AI 반도체",
            normalized_theme_name="ai 반도체",
            theme_summary="summary",
            why_now="why",
            impact_direction="positive",
            confidence_score=0.8,
            calculated_score=0.9,
            time_horizon="short",
            related_industries_json=["Semiconductors"],
            related_companies_json=["NVIDIA Corporation", "Imaginary Private Company"],
            risk_factors_json=[],
            evidence_count=2,
            source_publisher_count=1,
        )
        db.add(theme)
        db.commit()

        result = match_theme_securities(db, theme.id)

        assert result["matched"] == 1
        assert result["unmatched"] == 1
        assert db.query(Security).count() == 1
        assert result["candidates"][0]["security_id"] == security.id


def test_mock_sync_and_search_api(client: TestClient):
    sync_response = client.post("/securities/sync/mock")
    assert sync_response.status_code == 200
    body = sync_response.json()
    assert body["created_count"] >= 4

    search_response = client.get("/securities/search", params={"query": "NVIDIA", "country_code": "US"})
    assert search_response.status_code == 200
    assert search_response.json()[0]["ticker"] == "NVDA"

    list_response = client.get("/securities", params={"asset_type": "etf"})
    assert list_response.status_code == 200
    assert {row["asset_type"] for row in list_response.json()} == {"etf"}


def test_alias_backfill_matches_korean_english_and_ticker_variants(sqlite_session_local):
    with sqlite_session_local() as db:
        naver, _ = upsert_security(
            db,
            _security("035420", "NAVER", country_code="KR", exchange_code="XKRX", english_name="NAVER Corp."),
        )
        samsung, _ = upsert_security(
            db,
            _security("005930", "삼성전자", country_code="KR", exchange_code="XKRX", english_name="Samsung Electronics Co., Ltd."),
        )
        hynix, _ = upsert_security(
            db,
            _security("000660", "SK하이닉스", country_code="KR", exchange_code="XKRX", english_name="SK Hynix Inc."),
        )

        first = backfill_security_aliases(db)
        second = backfill_security_aliases(db)

        assert match_security(db, "네이버", country_code="KR")[0].security_id == naver.id
        assert match_security(db, "NAVER", country_code="KR")[0].security_id == naver.id
        assert match_security(db, "035420", country_code="KR")[0].security_id == naver.id
        assert match_security(db, "삼성전자", country_code="KR")[0].security_id == samsung.id
        assert match_security(db, "Samsung Electronics", country_code="KR")[0].security_id == samsung.id
        assert match_security(db, "005930", country_code="KR")[0].security_id == samsung.id
        assert match_security(db, "SK하이닉스", country_code="KR")[0].security_id == hynix.id
        assert match_security(db, "SK hynix", country_code="KR")[0].security_id == hynix.id
        assert match_security(db, "000660", country_code="KR")[0].security_id == hynix.id
        assert first["created_alias_count"] > 0
        assert second["created_alias_count"] == 0


def test_ambiguous_alias_is_not_auto_confirmed(sqlite_session_local):
    with sqlite_session_local() as db:
        first, _ = upsert_security(db, _security("AAA", "Alpha Holdings", country_code="US"))
        second, _ = upsert_security(db, _security("BBB", "Beta Holdings", country_code="US"))
        db.add_all(
            [
                SecurityAlias(security_id=first.id, alias="Shared Brand", normalized_alias=normalize_company_name("Shared Brand"), alias_type="manual", source="test"),
                SecurityAlias(security_id=second.id, alias="Shared Brand", normalized_alias=normalize_company_name("Shared Brand"), alias_type="manual", source="test"),
            ]
        )
        db.commit()

        result = match_security(db, "Shared Brand")

    assert result[0].ambiguity_status == "ambiguous"
    assert set(result[0].candidate_security_ids) == {first.id, second.id}


def test_alias_backfill_endpoint(client: TestClient):
    client.post("/securities/sync/mock")
    response = client.post("/securities/aliases/backfill")
    assert response.status_code == 200
    data = response.json()
    assert {"scanned_security_count", "created_alias_count", "skipped_alias_count", "ambiguous_alias_count", "duration_ms"} <= set(data)


def test_theme_candidate_api(client: TestClient, sqlite_session_local):
    client.post("/securities/sync/mock")
    now = datetime.now(timezone.utc)

    db = sqlite_session_local()
    try:
        run = ThemeAnalysisRun(
            run_id="theme-api-test",
            model_name="mock",
            prompt_version="theme-analysis-v1",
            window_start=now,
            window_end=now,
            status="completed",
        )
        db.add(run)
        db.commit()
        theme = MarketTheme(
            theme_run_id=run.id,
            rank=1,
            theme_name="AI",
            normalized_theme_name="ai",
            theme_summary="summary",
            why_now="why",
            impact_direction="positive",
            confidence_score=0.8,
            calculated_score=0.9,
            time_horizon="short",
            related_industries_json=[],
            related_companies_json=["NVIDIA Corporation"],
            risk_factors_json=[],
            evidence_count=1,
            source_publisher_count=1,
        )
        db.add(theme)
        db.commit()
        theme_id = theme.id
    finally:
        db.close()

    match_response = client.post(f"/themes/{theme_id}/match-securities")
    assert match_response.status_code == 200
    assert match_response.json()["matched"] == 1

    candidates_response = client.get(f"/themes/{theme_id}/security-candidates")
    assert candidates_response.status_code == 200
    assert candidates_response.json()[0]["security"]["ticker"] == "NVDA"
