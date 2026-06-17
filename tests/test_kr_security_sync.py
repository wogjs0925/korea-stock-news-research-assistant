from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import dashboard
from app.models.security import Security
from app.models.security_sync_run import SecuritySyncRun
from app.providers.securities.krx_kr import (
    KRXEmptyResponseError,
    ParsedKRSecuritySnapshot,
    KrxKRProvider,
    analyze_krx_etf_rows,
    analyze_krx_stock_rows,
    build_krx_url,
    classify_kr_stock,
    convert_etf_daily_row,
    convert_kosdaq_basic_row,
    convert_konex_basic_row,
    convert_kospi_basic_row,
    transform_krx_etf_rows,
    transform_krx_stock_rows,
    ticker_diagnostics,
    ETF_CODE_KEYS,
)
from app.repositories.security_repository import upsert_security
from app.schemas.security import SecurityIn
from app.services import security_master_service
from app.services.security_master_service import cleanup_orphan_kr_sync_run, sync_security_master


def _settings(**overrides):
    base = {
        "krx_api_key": "test-key",
        "krx_api_base_url": "https://example.test/krx",
        "krx_kospi_basic_api_id": "stk_isu_base_info",
        "krx_kosdaq_basic_api_id": "ksq_isu_base_info",
        "krx_konex_basic_api_id": "knx_isu_base_info",
        "krx_etf_daily_api_id": "etf_bydd_trd",
        "krx_sync_timeout": 30.0,
        "krx_sync_max_retries": 0,
        "krx_business_day_lookback": 3,
        "kr_security_minimum_expected_count": 1,
        "kr_security_deactivation_max_ratio": 0.10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


KOSPI_ROWS = [
    {
        "ISU_SRT_CD": "005930",
        "ISU_CD": "KR7005930003",
        "ISU_NM": "삼성전자",
        "ISU_ABBRV": "삼성전자",
        "ISU_ENG_NM": "Samsung Electronics",
        "LIST_DD": "19750611",
        "주식종류": "보통주",
    },
    {
        "ISU_SRT_CD": "005935",
        "ISU_CD": "KR7005931001",
        "ISU_NM": "삼성전자우",
        "주식종류": "우선주",
    },
]
KOSDAQ_ROWS = [
    {"ISU_SRT_CD": "091990", "ISU_CD": "KR7091990002", "ISU_NM": "셀트리온헬스케어", "주식종류": "보통주"},
    {"ISU_SRT_CD": "123456", "ISU_NM": "테스트스팩", "주식종류": "스팩"},
]
KONEX_ROWS = [{"ISU_SRT_CD": "900001", "ISU_NM": "코넥스테스트", "주식종류": "보통주"}]
ETF_ROWS = [
    {"ISU_SRT_CD": "069500", "ISU_CD": "KR7069500007", "ISU_NM": "KODEX 200", "운용사": "삼성자산운용"},
    {"ISU_SRT_CD": "122630", "ISU_NM": "KODEX 레버리지", "운용사": "삼성자산운용"},
    {"ISU_SRT_CD": "252670", "ISU_NM": "KODEX 200선물인버스2X", "운용사": "삼성자산운용"},
]

REALISTIC_KOSPI_ROW = {
    "ISU_CD": "KR7005930003",
    "ISU_SRT_CD": "005930",
    "ISU_NM": "삼성전자",
    "ISU_ABBRV": "삼성전자",
    "ISU_ENG_NM": "Samsung Electronics",
    "LIST_DD": "19750611",
    "MKT_TP_NM": "KOSPI",
    "SECUGRP_NM": "주권",
    "KIND_STKCERT_TP_NM": "보통주",
}
REALISTIC_KOSDAQ_ROW = {
    "ISU_CD": "KR7091990002",
    "ISU_SRT_CD": "091990",
    "ISU_NM": "셀트리온헬스케어",
    "MKT_TP_NM": "KOSDAQ",
    "SECUGRP_NM": "주권",
    "KIND_STKCERT_TP_NM": "보통주",
}
REALISTIC_KONEX_ROW = {
    "ISU_CD": "KR7000000001",
    "ISU_SRT_CD": "000001",
    "ISU_NM": "테스트코넥스",
    "MKT_TP_NM": "KONEX",
    "SECUGRP_NM": "주권",
    "KIND_STKCERT_TP_NM": "보통주",
}
REALISTIC_ETF_ROW = {
    "BAS_DD": "20260611",
    "ISU_CD": "069500",
    "ISU_NM": "KODEX 200",
    "TDD_CLSPRC": "38900",
    "ACC_TRDVOL": "123456",
}


def _patch_krx(monkeypatch):
    async def fake_kospi(self):
        return KOSPI_ROWS

    async def fake_kosdaq(self):
        return KOSDAQ_ROWS

    async def fake_konex(self):
        return KONEX_ROWS

    async def fake_etf_daily(self, base_date):
        return ETF_ROWS

    monkeypatch.setattr(security_master_service, "get_settings", lambda: _settings())
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: _settings())
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kospi_basic", fake_kospi)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kosdaq_basic", fake_kosdaq)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_konex_basic", fake_konex)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_etf_daily", fake_etf_daily)


def test_krx_stock_transform_maps_market_and_classification():
    rows = transform_krx_stock_rows(KOSPI_ROWS, "KOSPI")
    by_ticker = {row.ticker: row for row in rows}

    assert by_ticker["005930"].security_type_detail == "common_stock"
    assert by_ticker["005930"].is_recommendation_eligible is True
    assert by_ticker["005930"].isin == "KR7005930003"
    assert by_ticker["005930"].market_segment == "KOSPI"
    assert by_ticker["005930"].exchange_code == "XKRX"
    assert by_ticker["005935"].security_type_detail == "preferred_stock"
    assert by_ticker["005935"].is_recommendation_eligible is False


def test_krx_provider_builds_fixed_paths_and_auth_header(monkeypatch):
    captured = {}

    class FakeClient:
        async def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params or {}
            captured["headers"] = headers or {}

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"OutBlock_1": []}

            return Response()

    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: _settings())

    import asyncio

    asyncio.run(KrxKRProvider()._fetch_json(FakeClient(), "kospi"))

    assert build_krx_url("https://example.test/base/", "kospi").endswith("/sto/stk_isu_base_info")
    assert captured["url"].endswith("/sto/stk_isu_base_info")
    assert captured["headers"]["AUTH_KEY"] == "test-key"
    assert "apiKey" not in captured["params"]
    assert "serviceId" not in captured["params"]


def test_krx_fetch_methods_send_basdd_to_all_services(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    async def fake_fetch_json(self, client, service_name, params=None):
        calls.append((service_name, params or {}))
        return {"OutBlock_1": []}

    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: _settings())
    monkeypatch.setattr(KrxKRProvider, "_fetch_json", fake_fetch_json)
    provider = KrxKRProvider()

    asyncio.run(provider.fetch_kospi_basic("20260612"))
    asyncio.run(provider.fetch_kosdaq_basic("20260612"))
    asyncio.run(provider.fetch_konex_basic("20260612"))
    asyncio.run(provider.fetch_etf_daily("20260612"))

    assert [service for service, _params in calls] == ["kospi", "kosdaq", "konex", "etf"]
    assert all(params == {"basDd": "20260612"} for _service, params in calls)


def test_krx_snapshot_fallback_requires_kospi_and_kosdaq_same_date(monkeypatch):
    calls: list[tuple[str, str]] = []
    dates_seen: list[str] = []

    async def fake_kospi(self, base_date):
        calls.append(("kospi", base_date))
        dates_seen.append(base_date)
        return [REALISTIC_KOSPI_ROW]

    async def fake_kosdaq(self, base_date):
        calls.append(("kosdaq", base_date))
        return [] if len(dates_seen) == 1 else [REALISTIC_KOSDAQ_ROW]

    async def fake_konex(self, base_date):
        calls.append(("konex", base_date))
        return [REALISTIC_KONEX_ROW]

    async def fake_etf(self, base_date):
        calls.append(("etf", base_date))
        return [REALISTIC_ETF_ROW]

    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: _settings(krx_business_day_lookback=3))
    monkeypatch.setattr(KrxKRProvider, "fetch_kospi_basic", fake_kospi)
    monkeypatch.setattr(KrxKRProvider, "fetch_kosdaq_basic", fake_kosdaq)
    monkeypatch.setattr(KrxKRProvider, "fetch_konex_basic", fake_konex)
    monkeypatch.setattr(KrxKRProvider, "fetch_etf_daily", fake_etf)

    snapshot = asyncio.run(KrxKRProvider().fetch_snapshot())

    assert snapshot.snapshot_date == calls[-1][1]
    selected_date = snapshot.snapshot_date
    assert ("konex", selected_date) in calls
    assert ("etf", selected_date) in calls
    assert all(len(call_date) == 8 for _service, call_date in calls)
    assert calls.count(("kosdaq", selected_date)) == 1


def test_krx_snapshot_does_not_select_date_when_only_kospi_has_rows(monkeypatch):
    async def fake_kospi(self, base_date):
        return [REALISTIC_KOSPI_ROW]

    async def fake_kosdaq(self, base_date):
        return []

    async def fake_konex(self, base_date):
        return [REALISTIC_KONEX_ROW]

    async def fake_etf(self, base_date):
        return [REALISTIC_ETF_ROW]

    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: _settings(krx_business_day_lookback=1))
    monkeypatch.setattr(KrxKRProvider, "fetch_kospi_basic", fake_kospi)
    monkeypatch.setattr(KrxKRProvider, "fetch_kosdaq_basic", fake_kosdaq)
    monkeypatch.setattr(KrxKRProvider, "fetch_konex_basic", fake_konex)
    monkeypatch.setattr(KrxKRProvider, "fetch_etf_daily", fake_etf)

    with pytest.raises(KRXEmptyResponseError) as exc_info:
        asyncio.run(KrxKRProvider().fetch_snapshot())

    diagnostics = getattr(exc_info.value, "diagnostics", {})
    assert "fetch" in diagnostics


def test_krx_etf_transform_detects_leveraged_and_inverse():
    rows = transform_krx_etf_rows(ETF_ROWS, "20260612")
    by_ticker = {row.ticker: row for row in rows}

    assert by_ticker["069500"].asset_type == "etf"
    assert by_ticker["069500"].is_leveraged is False
    assert by_ticker["122630"].is_leveraged is True
    assert by_ticker["252670"].is_inverse is True
    assert by_ticker["252670"].source_status == "ETF:snapshot_date=20260612"


def test_krx_realistic_stock_field_names_convert():
    kospi, reason = convert_kospi_basic_row(REALISTIC_KOSPI_ROW)
    kosdaq, reason2 = convert_kosdaq_basic_row(REALISTIC_KOSDAQ_ROW)
    konex, reason3 = convert_konex_basic_row(REALISTIC_KONEX_ROW)

    assert reason is None
    assert reason2 is None
    assert reason3 is None
    assert kospi.ticker == "005930"
    assert kospi.isin == "KR7005930003"
    assert kosdaq.market_segment == "KOSDAQ"
    assert konex.ticker == "000001"


def test_krx_realistic_etf_daily_field_names_convert_minimal_fields():
    etf, reason = convert_etf_daily_row(REALISTIC_ETF_ROW, "20260611")

    assert reason is None
    assert etf.ticker == "069500"
    assert etf.name == "KODEX 200"
    assert etf.asset_type == "etf"
    assert etf.currency == "KRW"
    assert etf.source_status == "ETF:snapshot_date=20260611"


def test_krx_etf_a_prefix_code_normalizes_and_preserves_alias():
    row = {**REALISTIC_ETF_ROW, "ISU_CD": "A069500"}
    etf, reason = convert_etf_daily_row(row, "20260611")

    assert reason is None
    assert etf.ticker == "069500"
    assert any(alias["alias"] == "A069500" for alias in etf.aliases)


def test_krx_etf_isin_is_not_sliced_into_ticker():
    row = {**REALISTIC_ETF_ROW, "ISU_CD": "KR7069500007"}
    etf, reason = convert_etf_daily_row(row, "20260611")

    assert etf is None
    assert reason == "missing_short_code"


def test_krx_ticker_length_distribution_masks_values():
    diagnostics = ticker_diagnostics(
        [
            {"ISU_CD": "069500"},
            {"ISU_CD": "A069500"},
            {"ISU_CD": "KR7069500007"},
            {"ISU_CD": "ABC12345"},
        ],
        ETF_CODE_KEYS,
    )

    assert diagnostics["six_character_count"] == 1
    assert diagnostics["seven_character_count"] == 1
    assert diagnostics["twelve_character_count"] == 1
    assert diagnostics["masked_patterns"]["######"] == 1
    assert diagnostics["masked_patterns"]["A######"] == 1
    assert "069500" not in str(diagnostics)


def test_krx_code_normalization_preserves_leading_zero_and_a_prefix_alias():
    row = {**REALISTIC_KOSPI_ROW, "ISU_SRT_CD": "A005930"}
    security, reason = convert_kospi_basic_row(row)

    assert reason is None
    assert security.ticker == "005930"
    assert any(alias["alias"] == "A005930" for alias in security.aliases)


def test_krx_skipped_reason_counts_are_specific():
    result = analyze_krx_stock_rows(
        [
            {"ISU_SRT_CD": "", "ISU_NM": "이름없음"},
            {"ISU_SRT_CD": "123", "ISU_NM": "짧은코드"},
            {"ISU_SRT_CD": "123456"},
        ],
        "KOSPI",
    )

    assert result.valid_count == 0
    assert result.skipped_reason_counts == {
        "missing_ticker": 1,
        "invalid_ticker_length": 1,
        "missing_name": 1,
    }


def test_krx_transform_diagnostics_include_field_names_only():
    result = analyze_krx_etf_rows([REALISTIC_ETF_ROW], "20260611")
    diagnostics = result.safe_diagnostics()

    assert diagnostics["first_row_field_names"] == sorted(REALISTIC_ETF_ROW)
    assert "KODEX 200" not in str(diagnostics)


@pytest.mark.parametrize(
    ("row", "expected_detail", "eligible"),
    [
        ({"주식종류": "보통주"}, "common_stock", True),
        ({"주식종류": "우선주"}, "preferred_stock", False),
        ({"주식종류": "리츠"}, "real_estate_investment_trust", True),
        ({"주식종류": "스팩"}, "spac", False),
        ({"주식종류": "외국주"}, "foreign_share", False),
        ({"주식종류": "DR"}, "depositary_receipt", False),
    ],
)
def test_kr_stock_classification(row, expected_detail, eligible):
    result = classify_kr_stock(row, "테스트")
    assert result["security_type_detail"] == expected_detail
    assert result["is_recommendation_eligible"] is eligible


def test_kr_sync_creates_and_rerun_is_idempotent(monkeypatch, sqlite_session_local):
    _patch_krx(monkeypatch)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)

    first = sync_security_master(None, "kr")
    second = sync_security_master(None, "kr")

    assert first["status"] == "completed"
    assert first["received_count"] == 8
    assert first["kospi_received_count"] == 2
    assert first["kosdaq_received_count"] == 2
    assert first["konex_received_count"] == 1
    assert first["etf_received_count"] == 3
    assert first["kospi_valid_count"] == 2
    assert first["kosdaq_valid_count"] == 2
    assert first["konex_valid_count"] == 1
    assert first["etf_valid_count"] == 3
    assert first["skipped_count"] == 0
    assert first["created_count"] == first["valid_count"]
    assert first["kospi_stock_count"] == 2
    assert first["kosdaq_stock_count"] == 2
    assert first["konex_stock_count"] == 1
    assert first["etf_count"] == 3
    assert first["leveraged_etf_count"] == 1
    assert first["inverse_etf_count"] == 1
    assert first["snapshot_date"] == "20260612"
    assert second["created_count"] == 0
    assert second["updated_count"] == first["valid_count"]
    with sqlite_session_local() as db:
        samsung = db.query(Security).filter(Security.ticker == "005930").one()
        assert samsung.security_key == "KR:XKRX:005930"
        assert samsung.market_segment == "KOSPI"


def test_kr_sync_market_counts_are_not_overwritten_by_last_response(monkeypatch, sqlite_session_local):
    _patch_krx(monkeypatch)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)

    result = sync_security_master(None, "kr")

    assert result["received_count"] == (
        result["kospi_received_count"]
        + result["kosdaq_received_count"]
        + result["konex_received_count"]
        + result["etf_received_count"]
    )
    assert result["received_count"] != result["etf_received_count"]


def test_kr_sync_zero_valid_does_not_save_rows(monkeypatch, sqlite_session_local):
    async def invalid_kospi(self):
        return [{"ISU_CD": "KR7005930003", "ISU_NM": "삼성전자"}]

    async def invalid_kosdaq(self):
        return [{"ISU_CD": "KR7091990002", "ISU_NM": "셀트리온헬스케어"}]

    async def empty_konex(self):
        return []

    async def invalid_etf(self, base_date):
        return [{"ISU_CD": "069500", "TDD_CLSPRC": "1000"}]

    settings = _settings(kr_security_minimum_expected_count=1)
    monkeypatch.setattr(security_master_service, "get_settings", lambda: settings)
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: settings)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kospi_basic", invalid_kospi)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kosdaq_basic", invalid_kosdaq)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_konex_basic", empty_konex)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_etf_daily", invalid_etf)

    result = sync_security_master(None, "kr")

    assert result["status"] == "failed"
    assert result["valid_count"] == 0
    assert result["created_count"] == 0
    assert result["received_count"] == 3
    assert "missing_short_code" in result["skipped_reason_counts"]
    assert "missing_name" in result["skipped_reason_counts"]
    with sqlite_session_local() as db:
        assert db.query(Security).filter(Security.country_code == "KR").count() == 0


def test_kr_sync_partial_when_optional_etf_missing(monkeypatch, sqlite_session_local):
    async def fake_kospi(self):
        return [REALISTIC_KOSPI_ROW]

    async def fake_kosdaq(self):
        return [REALISTIC_KOSDAQ_ROW]

    async def fake_konex(self):
        return []

    async def missing_etf(self, base_date):
        raise KRXEmptyResponseError("empty")

    settings = _settings(kr_security_minimum_expected_count=2)
    monkeypatch.setattr(security_master_service, "get_settings", lambda: settings)
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: settings)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kospi_basic", fake_kospi)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kosdaq_basic", fake_kosdaq)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_konex_basic", fake_konex)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_etf_daily", missing_etf)

    result = sync_security_master(None, "kr")

    assert result["status"] == "partial"
    assert result["valid_count"] == 2
    assert result["etf_received_count"] == 0


def test_kr_sync_fails_and_does_not_save_when_only_etf_has_rows(monkeypatch, sqlite_session_local):
    async def empty_kospi(self):
        return []

    async def empty_kosdaq(self):
        return []

    async def empty_konex(self):
        return []

    async def etf_only(self, base_date):
        return [REALISTIC_ETF_ROW]

    settings = _settings(kr_security_minimum_expected_count=1)
    monkeypatch.setattr(security_master_service, "get_settings", lambda: settings)
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: settings)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kospi_basic", empty_kospi)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kosdaq_basic", empty_kosdaq)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_konex_basic", empty_konex)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_etf_daily", etf_only)

    result = sync_security_master(None, "kr")

    assert result["status"] == "failed"
    assert result["valid_count"] == 0
    assert result["etf_valid_count"] == 0
    assert result["created_count"] == 0
    with sqlite_session_local() as db:
        assert db.query(Security).filter(Security.country_code == "KR").count() == 0


def test_kr_sync_minimum_count_protects_existing_data(monkeypatch, sqlite_session_local):
    async def tiny_kospi(self):
        return []

    async def tiny_kosdaq(self):
        return []

    async def empty_konex(self):
        return []

    async def empty_etf(self, base_date):
        raise KRXEmptyResponseError("empty")

    settings = _settings(kr_security_minimum_expected_count=10)
    monkeypatch.setattr(security_master_service, "get_settings", lambda: settings)
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: settings)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kospi_basic", tiny_kospi)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_kosdaq_basic", tiny_kosdaq)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_konex_basic", empty_konex)
    monkeypatch.setattr("app.providers.securities.krx_kr.KrxKRProvider.fetch_etf_daily", empty_etf)

    with sqlite_session_local() as db:
        upsert_security(
            db,
            SecurityIn(
                country_code="KR",
                asset_type="stock",
                exchange_code="XKRX",
                exchange_name="KOSPI",
                ticker="000001",
                name="보존",
                currency="KRW",
                source="krx_open_api",
                market_segment="KOSPI",
            ),
        )

    result = sync_security_master(None, "kr")

    assert result["status"] == "failed"
    with sqlite_session_local() as db:
        kept = db.query(Security).filter(Security.ticker == "000001").one()
        assert kept.is_active is True


def test_kr_sync_api_config_missing_and_no_request_session(monkeypatch, client):
    calls = []

    def fake_sync(db, provider_name):
        calls.append((db, provider_name))
        return {
            "run_id": "",
            "country_code": "KR",
            "provider": "krx_open_api",
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
            "status": "configuration_error",
            "current_stage": "configuration",
            "duration_ms": 0,
            "source_file_created_at": None,
            "error_message": "KRX API 인증키 또는 승인된 서비스 설정이 없습니다.",
        }

    monkeypatch.setattr("app.backend.routes.securities.sync_security_master", fake_sync)
    response = client.post("/securities/sync/kr")

    assert response.status_code == 503
    assert calls == [(None, "kr")]


def test_kr_sync_missing_api_id_blocks_sync(monkeypatch):
    settings = _settings(krx_kospi_basic_api_id=None)
    monkeypatch.setattr(
        security_master_service,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr("app.providers.securities.krx_kr.get_settings", lambda: settings)

    result = security_master_service.sync_kr_security_master()

    assert result["status"] == "configuration_error"
    assert result["configuration_missing"] == ["KRX_KOSPI_BASIC_API_ID"]


def test_kr_sync_api_success_and_data_quality(monkeypatch, client, sqlite_session_local):
    _patch_krx(monkeypatch)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)

    response = client.post("/securities/sync/kr")
    assert response.status_code == 200
    body = response.json()
    assert body["valid_count"] == 8

    detail = client.get(f"/securities/sync-runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["snapshot_date"] == "20260612"

    quality = client.get("/securities/data-quality")
    assert quality.status_code == 200
    assert quality.json()["kr_total"] == 8
    assert quality.json()["last_kr_snapshot_date"] == "20260612"


def test_kr_sync_running_conflict(monkeypatch, client):
    monkeypatch.setattr("app.backend.routes.securities.sync_security_master", lambda _db, _provider: (_ for _ in ()).throw(security_master_service.KRXSyncRunningError()))
    response = client.post("/securities/sync/kr")
    assert response.status_code == 409


def test_cleanup_orphan_kr_sync_run(sqlite_session_local):
    with sqlite_session_local() as db:
        db.add(
            SecuritySyncRun(
                run_id="KRSECURITY-orphan",
                country_code="KR",
                provider="krx_open_api",
                status="running",
                current_stage="saving_securities",
            )
        )
        db.commit()

        changed = cleanup_orphan_kr_sync_run(db, "KRSECURITY-orphan")
        run = security_master_service.security_sync_runs(db, country_code="KR")[0]

        assert changed is True
        assert run.status == "failed"


def test_us_data_is_preserved_during_kr_sync(monkeypatch, sqlite_session_local):
    _patch_krx(monkeypatch)
    monkeypatch.setattr(security_master_service, "SessionLocal", sqlite_session_local)

    with sqlite_session_local() as db:
        upsert_security(
            db,
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNAS",
                exchange_name="NASDAQ",
                ticker="NVDA",
                name="NVIDIA Corporation",
                currency="USD",
                source="nasdaq_trader_us",
            ),
        )

    sync_security_master(None, "kr")

    with sqlite_session_local() as db:
        assert db.query(Security).filter(Security.country_code == "US", Security.ticker == "NVDA").one().is_active is True


def test_dashboard_kr_sync_surfaces_backend_detail(monkeypatch):
    request = dashboard.httpx.Request("POST", "http://test")
    response = dashboard.httpx.Response(503, json={"detail": "KRX API 인증키 또는 승인된 서비스 설정이 없습니다."}, request=request)

    def raise_http(*args, **kwargs):
        raise dashboard.httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(dashboard.httpx, "post", raise_http)

    _data, error = dashboard.sync_kr_securities()

    assert error == "KRX API 인증키 또는 승인된 서비스 설정이 없습니다."
