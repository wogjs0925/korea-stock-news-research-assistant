from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import dashboard
from app.models.security import Security
from app.providers.securities.nasdaq_trader_us import (
    ParsedUSSecuritySnapshot,
    clean_file_creation_time,
    classify_security_name,
    parse_nasdaq_listed,
    parse_other_listed,
)
from app.providers.securities.sec_us import sec_enrichment_map, transform_sec_company_tickers
from app.repositories.security_repository import upsert_security
from app.schemas.security import SecurityIn
from app.services import security_master_service
from app.services.security_master_service import cleanup_latest_us_source_file_created_at, cleanup_orphan_us_sync_run, sync_security_master


NASDAQ_FIXTURE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
NVDA|NVIDIA Corporation - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust ETF|G|N|N|100|Y|N
TEST|Test Company Common Stock|S|Y|N|100|N|N
|Blank Ticker Common Stock|S|N|N|100|N|N
File Creation Time: 06122026
"""


OTHER_FIXTURE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
IBM|International Business Machines Corporation Common Stock|N|IBM|N|100|N|IBM
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
XYZ.W|Example Corp Warrant|A|XYZ.WS|N|100|N|XYZW
BATZ|Cboe Listed Common Stock|Z|BATZ|N|100|N|BATZ
IEXC|IEX Common Stock|V|IEXC|N|100|N|IEXC
UNK|Unknown Exchange Common Stock|U|UNK|N|100|N|UNK
OTEST|Other Test Common Stock|N|OTEST|N|100|Y|OTEST
File Creation Time: 06122026
"""


def _settings(**overrides):
    base = {
        "security_sync_timeout": 30.0,
        "security_sync_max_retries": 0,
        "security_sync_user_agent": "StockAILab/1.0",
        "nasdaq_listed_url": "https://example.test/nasdaq.txt",
        "nasdaq_other_listed_url": "https://example.test/other.txt",
        "sec_company_tickers_url": "https://example.test/sec.json",
        "sec_user_agent": "StockAILab test contact",
        "us_security_minimum_expected_count": 1,
        "us_security_deactivation_max_ratio": 0.10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_nasdaq_listed_parsing_filters_and_classifies():
    snapshot = parse_nasdaq_listed(NASDAQ_FIXTURE)

    assert snapshot.received_count == 5
    assert snapshot.valid_count == 2
    assert snapshot.skipped_count == 2
    assert snapshot.source_file_created_at == "06122026"
    assert {item.ticker for item in snapshot.securities} == {"NVDA", "QQQ"}
    assert [item for item in snapshot.securities if item.ticker == "NVDA"][0].exchange_code == "XNAS"
    assert [item for item in snapshot.securities if item.ticker == "QQQ"][0].asset_type == "etf"


def test_otherlisted_parsing_exchange_mapping_and_unknown():
    snapshot = parse_other_listed(OTHER_FIXTURE)
    by_ticker = {item.ticker: item for item in snapshot.securities}

    assert by_ticker["IBM"].exchange_code == "XNYS"
    assert by_ticker["XYZ.W"].exchange_code == "XASE"
    assert by_ticker["SPY"].exchange_code == "ARCX"
    assert by_ticker["BATZ"].exchange_code == "BATS"
    assert by_ticker["IEXC"].exchange_code == "IEXG"
    assert by_ticker["UNK"].exchange_code == "UNKNOWN_U"
    assert snapshot.unknown_exchange_count == 1
    assert by_ticker["XYZ.W"].is_recommendation_eligible is False


@pytest.mark.parametrize(
    ("name", "is_etf", "detail", "eligible"),
    [
        ("Example Common Stock", False, "common_stock", True),
        ("Example Ordinary Shares", False, "common_stock", True),
        ("Example ADR", False, "adr", True),
        ("Example ADS", False, "adr", True),
        ("Example ETF", True, "etf", True),
        ("Example Warrant", False, "warrant", False),
        ("Example Right", False, "right", False),
        ("Example Unit", False, "unit", False),
        ("Example Preferred Stock", False, "preferred_stock", False),
        ("Example Senior Note", False, "note", False),
        ("Ambiguous Capital", False, "unknown", False),
    ],
)
def test_security_type_classification(name, is_etf, detail, eligible):
    result = classify_security_name(name, is_etf)
    assert result["security_type_detail"] == detail
    assert result["is_recommendation_eligible"] is eligible


def test_leveraged_and_inverse_etf_flags():
    leveraged = classify_security_name("Ultra 2x Technology ETF", True)
    inverse = classify_security_name("Short Bear 3x ETF", True)

    assert leveraged["is_leveraged"] is True
    assert inverse["is_inverse"] is True


def test_sec_transform_and_enrichment_map_zero_pads_cik():
    payload = {
        "0": {"ticker": "NVDA", "title": "NVIDIA Corporation", "exchange": "Nasdaq", "cik_str": 1045810},
        "1": {"ticker": "IBM", "title": "International Business Machines Corp", "exchange": "NYSE", "cik_str": 51143},
    }
    rows = transform_sec_company_tickers(payload)
    mapping = sec_enrichment_map(payload)

    assert rows[0].cik == "0001045810"
    assert mapping[("IBM", "XNYS")]["cik"] == "0000051143"


def test_sec_ticker_class_share_normalization():
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[1067983, "Berkshire Hathaway Inc", "BRK.B", "New York Stock Exchange"]],
    }
    mapping = sec_enrichment_map(payload)
    assert mapping[("BRK-B", "XNYS")]["cik"] == "0001067983"


def test_footer_creation_time_is_cleaned():
    assert clean_file_creation_time("File Creation Time: 0612202610:01|||||||") == "2026-06-12 10:01"
    assert clean_file_creation_time("File Creation Time: bad||||") == "bad"


def _snapshot_from_fixtures() -> ParsedUSSecuritySnapshot:
    listed = parse_nasdaq_listed(NASDAQ_FIXTURE)
    other = parse_other_listed(OTHER_FIXTURE)
    securities = listed.securities + other.securities
    return ParsedUSSecuritySnapshot(
        securities=securities,
        received_count=listed.received_count + other.received_count,
        valid_count=len(securities),
        skipped_count=listed.skipped_count + other.skipped_count,
        stock_count=sum(1 for item in securities if item.asset_type == "stock"),
        etf_count=sum(1 for item in securities if item.asset_type == "etf"),
        excluded_security_count=sum(1 for item in securities if not item.is_recommendation_eligible),
        unknown_exchange_count=listed.unknown_exchange_count + other.unknown_exchange_count,
        source_file_created_at="06122026",
    )


def test_us_sync_idempotent_and_sec_enrichment(monkeypatch, sqlite_session_local):
    async def fake_snapshot(self):
        return _snapshot_from_fixtures()

    async def fake_sec_payload(self):
        return {
            "0": {"ticker": "NVDA", "title": "NVIDIA Corporation", "exchange": "Nasdaq", "cik_str": 1045810},
            "1": {"ticker": "IBM", "title": "International Business Machines Corp", "exchange": "NYSE", "cik_str": 51143},
        }

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings())
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", fake_snapshot)
    monkeypatch.setattr("app.providers.securities.sec_us.SecUSProvider.fetch_payload", fake_sec_payload)

    with sqlite_session_local() as db:
        first = sync_security_master(db, "us")
        second = sync_security_master(db, "us")
        nvda = db.query(Security).filter(Security.ticker == "NVDA").one()

        assert first["created_count"] == first["valid_count"]
        assert second["created_count"] == 0
        assert second["updated_count"] == first["valid_count"]
        assert second["cik_enriched_count"] == 2
        assert nvda.cik == "0001045810"
        assert db.query(Security).count() == first["valid_count"]


def test_us_sync_sec_user_agent_missing_is_partial(monkeypatch, sqlite_session_local):
    async def fake_snapshot(self):
        return _snapshot_from_fixtures()

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(sec_user_agent=None))
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", fake_snapshot)

    with sqlite_session_local() as db:
        result = sync_security_master(db, "us")

        assert result["status"] == "partial"
        assert result["cik_enriched_count"] == 0
        assert "SEC_USER_AGENT" in result["error_message"]
        assert db.query(Security).count() == result["valid_count"]


def test_us_sync_minimum_count_protects_existing_data(monkeypatch, sqlite_session_local):
    async def tiny_snapshot(self):
        return ParsedUSSecuritySnapshot(
            securities=[],
            received_count=0,
            valid_count=0,
            skipped_count=0,
            stock_count=0,
            etf_count=0,
            excluded_security_count=0,
            unknown_exchange_count=0,
            source_file_created_at=None,
        )

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(us_security_minimum_expected_count=10))
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", tiny_snapshot)

    with sqlite_session_local() as db:
        upsert_security(
            db,
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNAS",
                exchange_name="NASDAQ",
                ticker="KEEP",
                name="Keep Common Stock",
                english_name="Keep Common Stock",
                currency="USD",
                source="nasdaq_trader_us",
            ),
        )
        result = sync_security_master(db, "us")
        kept = db.query(Security).filter(Security.ticker == "KEEP").one()

        assert result["status"] == "failed"
        assert kept.is_active is True


def test_us_sync_api_and_data_quality(monkeypatch, client, sqlite_session_local):
    async def fake_snapshot(self):
        return _snapshot_from_fixtures()

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(sec_user_agent=None))
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", fake_snapshot)

    sync_response = client.post("/securities/sync/us")
    assert sync_response.status_code == 200
    assert sync_response.json()["valid_count"] > 0

    quality = client.get("/securities/data-quality")
    assert quality.status_code == 200
    assert quality.json()["us_total"] == sync_response.json()["valid_count"]
    assert quality.json()["unknown_exchange_count"] == 1

    runs = client.get("/securities/sync-runs", params={"country_code": "US"})
    assert runs.status_code == 200
    assert runs.json()[0]["provider"] == "nasdaq_trader_us"


def test_sync_us_route_does_not_pass_request_session(monkeypatch, client):
    calls = []

    def fake_sync(db, provider_name):
        calls.append((db, provider_name))
        return {
            "run_id": "route-test",
            "country_code": "US",
            "provider": "nasdaq_trader_us",
            "requested_count": 0,
            "received_count": 0,
            "valid_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "deactivated_count": 0,
            "failed_count": 0,
            "stock_count": 0,
            "etf_count": 0,
            "excluded_security_count": 0,
            "cik_enriched_count": 0,
            "unknown_exchange_count": 0,
            "status": "completed",
            "current_stage": "finalizing",
            "duration_ms": 1,
            "source_file_created_at": None,
            "error_message": None,
        }

    monkeypatch.setattr("app.backend.routes.securities.sync_security_master", fake_sync)
    response = client.post("/securities/sync/us")

    assert response.status_code == 200
    assert calls == [(None, "us")]


def test_final_status_failure_is_recorded_with_new_session(monkeypatch, sqlite_session_local):
    async def fake_snapshot(self):
        return _snapshot_from_fixtures()

    def failing_update(db, run):
        if run.current_stage == "finalizing":
            from sqlalchemy.exc import ResourceClosedError

            raise ResourceClosedError("closed result")
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(sec_user_agent=None))
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr(security_master_service, "update_sync_run", failing_update)
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", fake_snapshot)

    with sqlite_session_local() as db:
        with pytest.raises(Exception):
            sync_security_master(db, "us")
        db.expire_all()
        runs = security_master_service.security_sync_runs(db, country_code="US")
        latest = runs[0]
        assert latest.status == "failed"
        assert latest.current_stage == "finalizing"
        assert latest.valid_count == _snapshot_from_fixtures().valid_count
        assert latest.created_count == _snapshot_from_fixtures().valid_count


def test_cleanup_orphan_running_sync(sqlite_session_local):
    from app.models.security_sync_run import SecuritySyncRun

    with sqlite_session_local() as db:
        db.add(
            SecuritySyncRun(
                run_id="USSECURITY-orphan",
                country_code="US",
                provider="nasdaq_trader_us",
                status="running",
                current_stage="saving_securities",
            )
        )
        db.commit()

        changed = cleanup_orphan_us_sync_run(db, "USSECURITY-orphan", "서버 종료로 중단된 실행입니다.")
        run = security_master_service.security_sync_runs(db, country_code="US")[0]

        assert changed is True
        assert run.status == "failed"
        assert run.error_message == "서버 종료로 중단된 실행입니다."


def test_cleanup_latest_source_file_created_at(sqlite_session_local):
    from app.models.security_sync_run import SecuritySyncRun

    with sqlite_session_local() as db:
        db.add(
            SecuritySyncRun(
                run_id="USSECURITY-footer",
                country_code="US",
                provider="nasdaq_trader_us",
                status="completed",
                source_file_created_at="0612202610:01|||||||; 0612202610:01||||||",
            )
        )
        db.commit()

        changed = cleanup_latest_us_source_file_created_at(db)
        run = security_master_service.security_sync_runs(db, country_code="US")[0]

        assert changed is True
        assert run.source_file_created_at == "nasdaqlisted=2026-06-12 10:01; otherlisted=2026-06-12 10:01"


def test_independent_session_sync_after_request_session_closed(monkeypatch, sqlite_session_local):
    async def fake_snapshot(self):
        return _snapshot_from_fixtures()

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(sec_user_agent=None))
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.nasdaq_trader_us.NasdaqTraderUSProvider.fetch_snapshot", fake_snapshot)

    request_db = sqlite_session_local()
    request_db.close()
    result = security_master_service.sync_us_security_master()

    assert result["valid_count"] == _snapshot_from_fixtures().valid_count
    with sqlite_session_local() as db:
        assert db.query(Security).count() == result["valid_count"]


def test_sec_enrichment_first_run_and_rerun_verified_counts(monkeypatch, sqlite_session_local):
    async def fake_sec_payload(self):
        return {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [
                [1045810, "NVIDIA Corporation", "NVDA", "Nasdaq Global Select Market"],
                [320193, "Apple Inc.", "AAPL", "Nasdaq Global Select Market"],
                [789019, "Microsoft Corporation", "MSFT", "Nasdaq Global Select Market"],
                [111111, "First Test Inc.", "ONE", "Nasdaq Global Select Market"],
                [222222, "Second Test Inc.", "TWO", "Nasdaq Global Select Market"],
            ],
        }

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.sec_us.SecUSProvider.fetch_payload", fake_sec_payload)

    with sqlite_session_local() as db:
        upsert_security(
            db,
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNAS",
                exchange_name="NASDAQ",
                ticker="NVDA",
                name="NVIDIA Corporation Common Stock",
                english_name="NVIDIA Corporation Common Stock",
                currency="USD",
                source="nasdaq_trader_us",
            ),
        )
        upsert_security(
            db,
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNAS",
                exchange_name="NASDAQ",
                ticker="AAPL",
                name="Apple Inc. Common Stock",
                english_name="Apple Inc. Common Stock",
                currency="USD",
                source="nasdaq_trader_us",
            ),
        )
        for ticker, name in [
            ("MSFT", "Microsoft Corporation Common Stock"),
            ("ONE", "First Test Inc. Common Stock"),
            ("TWO", "Second Test Inc. Common Stock"),
        ]:
            upsert_security(
                db,
                SecurityIn(
                    country_code="US",
                    asset_type="stock",
                    exchange_code="XNAS",
                    exchange_name="NASDAQ",
                    ticker=ticker,
                    name=name,
                    english_name=name,
                    currency="USD",
                    source="nasdaq_trader_us",
                ),
            )

    first = security_master_service.enrich_us_securities_from_sec()
    second = security_master_service.enrich_us_securities_from_sec()

    assert first["status"] == "completed"
    assert first["persisted_cik_count_before"] == 0
    assert first["persisted_cik_count_after"] == 5
    assert first["cik_updated_count"] == 5
    assert first["verified_cik_delta"] == 5
    assert first["matched_sec_record_count"] == 5
    assert first["unique_matched_security_count"] == 5
    assert second["persisted_cik_count_before"] == 5
    assert second["persisted_cik_count_after"] == 5
    assert second["cik_updated_count"] == 0
    assert second["already_had_cik_count"] == 5
    with sqlite_session_local() as db:
        assert db.query(Security).filter(Security.ticker == "NVDA").one().cik == "0001045810"
        assert db.query(Security).filter(Security.ticker == "AAPL").one().cik == "0000320193"
        assert db.query(Security).filter(Security.ticker == "MSFT").one().cik == "0000789019"


def test_sec_enrichment_api_user_agent_missing(monkeypatch, client):
    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings(sec_user_agent=None))
    response = client.post("/securities/enrich/us/sec")
    assert response.status_code == 503
    assert response.json()["detail"] == "SEC_USER_AGENT가 설정되지 않아 SEC CIK 보강을 실행할 수 없습니다."


def test_sec_enrichment_api_running_conflict(monkeypatch, client):
    monkeypatch.setattr("app.backend.routes.securities.enrich_us_securities_from_sec", lambda: {"status": "running"})
    response = client.post("/securities/enrich/us/sec")
    assert response.status_code == 409


def test_security_dashboard_clears_previous_error_on_success(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"total": 1}

    monkeypatch.setitem(dashboard.st.session_state, "security_error_summary", "old error")
    monkeypatch.setattr(dashboard.httpx, "get", lambda *args, **kwargs: FakeResponse())

    data, error = dashboard.fetch_security_summary()

    assert data == {"total": 1}
    assert error is None
    assert "security_error_summary" not in dashboard.st.session_state


def test_security_dashboard_error_messages(monkeypatch):
    def raise_connect(*args, **kwargs):
        raise dashboard.httpx.ConnectError("connect failed")

    monkeypatch.setattr(dashboard.httpx, "get", raise_connect)
    _data, error = dashboard.fetch_security_summary()
    assert error == "FastAPI 서버에 연결할 수 없습니다."

    request = dashboard.httpx.Request("GET", "http://test")
    response = dashboard.httpx.Response(500, request=request)

    def raise_http(*args, **kwargs):
        raise dashboard.httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(dashboard.httpx, "get", raise_http)
    _data, error = dashboard.fetch_security_data_quality()
    assert error == "데이터 품질 조회 중 백엔드 오류 500이 발생했습니다."
