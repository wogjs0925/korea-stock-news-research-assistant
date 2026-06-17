from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, SecretStr
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.error import ErrorLogCreate
from app.services import credential_service
from app.services.app_setting_service import (
    delete_runtime_setting,
    get_runtime_setting,
    get_secret_source,
    get_secret_value,
    runtime_openai_model,
    runtime_sec_user_agent,
    set_runtime_setting,
)
from app.services.error_service import create_error_log
from app.providers.securities.krx_kr import (
    KRX_SERVICE_PATHS,
    KRXConfigurationError,
    analyze_krx_etf_rows,
    analyze_krx_stock_rows,
    build_krx_provider_from_runtime_settings,
    build_krx_path,
    build_krx_url,
    ticker_diagnostics,
    ETF_CODE_KEYS,
)

router = APIRouter(prefix="/developer/settings", tags=["Developer Settings"])

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
SEOUL_TZ = ZoneInfo("Asia/Seoul")


class NaverSettingsIn(BaseModel):
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None


class OpenAISettingsIn(BaseModel):
    api_key: SecretStr | None = None
    model: str | None = None


class SECSettingsIn(BaseModel):
    user_agent: SecretStr | None = None


class KRXSettingsIn(BaseModel):
    api_key: SecretStr | None = None
    base_url: str | None = None
    kospi_api_id: str | None = None
    kosdaq_api_id: str | None = None
    konex_api_id: str | None = None
    etf_api_id: str | None = None
    api_key_param: str | None = None
    api_id_param: str | None = None
    base_date_param: str | None = None
    sync_timeout: float | None = None
    business_day_lookback: int | None = None


def require_local_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="developer settings are local only")


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _save_secret(name: str, value: SecretStr | None) -> None:
    raw = _secret(value)
    if raw:
        credential_service.set_secret(name, raw)


def _safe_error(db: Session, provider: str, code: str, error_type: str, message: str, context: dict[str, Any]) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=code,
                severity="ERROR",
                component="developer_settings",
                error_type=error_type,
                message=message,
                context_json=context,
            ),
        )
    except Exception:
        return


def _status(db: Session | None = None) -> dict[str, Any]:
    return {
        "naver": {
            "client_id_configured": get_secret_source("NAVER_CLIENT_ID") != "not_configured",
            "client_secret_configured": get_secret_source("NAVER_CLIENT_SECRET") != "not_configured",
            "source": "keyring"
            if "keyring" in {get_secret_source("NAVER_CLIENT_ID"), get_secret_source("NAVER_CLIENT_SECRET")}
            else (
                "environment"
                if "environment" in {get_secret_source("NAVER_CLIENT_ID"), get_secret_source("NAVER_CLIENT_SECRET")}
                else "not_configured"
            ),
        },
        "openai": {
            "api_key_configured": get_secret_source("OPENAI_API_KEY") != "not_configured",
            "model": runtime_openai_model(db),
            "source": get_secret_source("OPENAI_API_KEY"),
        },
        "sec": {
            "user_agent_configured": bool(runtime_sec_user_agent(db)),
            "source": "app_settings" if get_runtime_setting("SEC_USER_AGENT", None, db=db) else get_secret_source("SEC_USER_AGENT"),
        },
        "krx": {
            "api_key_configured": get_secret_source("KRX_API_KEY") != "not_configured",
            "base_url_configured": bool(get_runtime_setting("KRX_API_BASE_URL", "", db=db)),
            "kospi_api_id_configured": bool(get_runtime_setting("KRX_KOSPI_BASIC_API_ID", "", db=db)),
            "kosdaq_api_id_configured": bool(get_runtime_setting("KRX_KOSDAQ_BASIC_API_ID", "", db=db)),
            "konex_api_id_configured": bool(get_runtime_setting("KRX_KONEX_BASIC_API_ID", "", db=db)),
            "etf_api_id_configured": bool(get_runtime_setting("KRX_ETF_DAILY_API_ID", "", db=db)),
        },
    }


def _test_result(provider: str, success: bool, status_text: str, message: str, start: float, details: dict[str, Any] | None = None):
    return {
        "provider": provider,
        "success": success,
        "status": status_text,
        "message": message,
        "latency_ms": int((time.time() - start) * 1000),
        "details": details or {},
    }


@router.get("/status")
def status_view(request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    return _status(db)


@router.put("/naver")
def save_naver(payload: NaverSettingsIn, request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    try:
        _save_secret("NAVER_CLIENT_ID", payload.client_id)
        _save_secret("NAVER_CLIENT_SECRET", payload.client_secret)
    except Exception as exc:
        _safe_error(db, "naver", "CREDENTIAL_SAVE_ERROR", type(exc).__name__, "Naver credential save failed", {"provider": "naver"})
        raise HTTPException(status_code=500, detail="Naver API 설정 저장에 실패했습니다.") from None
    return _status(db)["naver"]


@router.put("/openai")
def save_openai(payload: OpenAISettingsIn, request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    try:
        _save_secret("OPENAI_API_KEY", payload.api_key)
        if payload.model:
            set_runtime_setting(db, "OPENAI_MODEL", payload.model)
    except Exception as exc:
        _safe_error(db, "openai", "CREDENTIAL_SAVE_ERROR", type(exc).__name__, "OpenAI settings save failed", {"provider": "openai"})
        raise HTTPException(status_code=500, detail="OpenAI API 설정 저장에 실패했습니다.") from None
    return _status(db)["openai"]


@router.put("/sec")
def save_sec(payload: SECSettingsIn, request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    value = _secret(payload.user_agent)
    if value:
        set_runtime_setting(db, "SEC_USER_AGENT", value)
    return _status(db)["sec"]


@router.put("/krx")
def save_krx(payload: KRXSettingsIn, request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    try:
        _save_secret("KRX_API_KEY", payload.api_key)
        mapping = {
            "KRX_API_BASE_URL": payload.base_url,
            "KRX_KOSPI_BASIC_API_ID": payload.kospi_api_id,
            "KRX_KOSDAQ_BASIC_API_ID": payload.kosdaq_api_id,
            "KRX_KONEX_BASIC_API_ID": payload.konex_api_id,
            "KRX_ETF_DAILY_API_ID": payload.etf_api_id,
            "KRX_API_KEY_PARAM": payload.api_key_param,
            "KRX_API_ID_PARAM": payload.api_id_param,
            "KRX_BASE_DATE_PARAM": payload.base_date_param,
            "KRX_SYNC_TIMEOUT": str(payload.sync_timeout) if payload.sync_timeout is not None else None,
            "KRX_BUSINESS_DAY_LOOKBACK": str(payload.business_day_lookback) if payload.business_day_lookback is not None else None,
        }
        for key, value in mapping.items():
            if value not in (None, ""):
                set_runtime_setting(db, key, str(value))
    except Exception as exc:
        _safe_error(db, "krx", "CREDENTIAL_SAVE_ERROR", type(exc).__name__, "KRX settings save failed", {"provider": "krx"})
        raise HTTPException(status_code=500, detail="KRX API 설정 저장에 실패했습니다.") from None
    return _status(db)["krx"]


@router.delete("/{provider}")
def delete_provider(provider: str, request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    try:
        if provider == "naver":
            credential_service.delete_secret("NAVER_CLIENT_ID")
            credential_service.delete_secret("NAVER_CLIENT_SECRET")
        elif provider == "openai":
            credential_service.delete_secret("OPENAI_API_KEY")
            delete_runtime_setting(db, "OPENAI_MODEL")
        elif provider == "sec":
            delete_runtime_setting(db, "SEC_USER_AGENT")
        elif provider == "krx":
            credential_service.delete_secret("KRX_API_KEY")
            for key in (
                "KRX_API_BASE_URL",
                "KRX_KOSPI_BASIC_API_ID",
                "KRX_KOSDAQ_BASIC_API_ID",
                "KRX_KONEX_BASIC_API_ID",
                "KRX_ETF_DAILY_API_ID",
                "KRX_API_KEY_PARAM",
                "KRX_API_ID_PARAM",
                "KRX_BASE_DATE_PARAM",
                "KRX_SYNC_TIMEOUT",
                "KRX_BUSINESS_DAY_LOOKBACK",
            ):
                delete_runtime_setting(db, key)
        else:
            raise HTTPException(status_code=404, detail="unknown provider")
    except HTTPException:
        raise
    except Exception as exc:
        _safe_error(db, provider, "CREDENTIAL_DELETE_ERROR", type(exc).__name__, "Credential delete failed", {"provider": provider})
        raise HTTPException(status_code=500, detail="API 설정 삭제에 실패했습니다.") from None
    return {"status": "deleted", "provider": provider}


async def _http_get_json(url: str, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, timeout: float = 10.0):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


async def _http_get_response(url: str, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, timeout: float = 10.0):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        return await client.get(url, headers=headers, params=params)


def _krx_response_json(response: httpx.Response) -> dict[str, Any] | None:
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _krx_resp_value(payload: dict[str, Any] | None, *keys: str) -> str | None:
    if not payload:
        return None
    candidates = [payload]
    for value in payload.values():
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in keys:
            value = candidate.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _krx_detail(
    response: httpx.Response,
    service_name: str,
    request_path: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "service": service_name,
        "request_path": request_path,
        "auth_header_present": True,
        "upstream_status_code": response.status_code,
        "krx_resp_code": _krx_resp_value(payload, "respCode", "rsp_cd", "code"),
        "krx_resp_message": _krx_resp_value(payload, "respMsg", "rsp_msg", "message"),
        "response_content_type": response.headers.get("content-type"),
        "body_length": len(response.content or b""),
    }


def _krx_http_status(status_code: int) -> tuple[str, str]:
    if status_code == 401:
        return "authentication_failed", "인증키가 거부되었습니다."
    if status_code == 403:
        return "service_not_approved", "해당 KRX 서비스의 활용 승인이 필요합니다."
    if status_code == 404:
        return "invalid_endpoint", "API URL 또는 API ID가 올바르지 않습니다."
    if status_code == 400:
        return "invalid_parameter", "요청 파라미터가 올바르지 않습니다."
    if status_code == 429:
        return "rate_limit", "KRX API 요청 한도를 초과했습니다."
    return "http_error", "KRX upstream HTTP 오류가 발생했습니다."


def _krx_has_outblock(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    value = payload.get("OutBlock_1")
    return isinstance(value, list)


def _krx_extract_rows_for_test(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    value = payload.get("OutBlock_1")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    for nested in payload.values():
        if isinstance(nested, dict):
            rows = nested.get("OutBlock_1")
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


def _krx_api_id_setting_name(service_name: str) -> str:
    return {
        "kospi": "KRX_KOSPI_BASIC_API_ID",
        "kosdaq": "KRX_KOSDAQ_BASIC_API_ID",
        "konex": "KRX_KONEX_BASIC_API_ID",
        "etf": "KRX_ETF_DAILY_API_ID",
    }[service_name]


def _krx_default_base_date() -> str:
    return (datetime.now(SEOUL_TZ).date() - timedelta(days=1)).strftime("%Y%m%d")


def _krx_analyze_rows(service_name: str, rows: list[dict[str, Any]], snapshot_date: str | None = None):
    if service_name == "etf":
        return analyze_krx_etf_rows(rows, snapshot_date)
    return analyze_krx_stock_rows(rows, service_name.upper())


@router.post("/test/naver")
async def test_naver(request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    start = time.time()
    client_id = get_secret_value("NAVER_CLIENT_ID")
    client_secret = get_secret_value("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return _test_result("naver", False, "not_configured", "Naver API 설정이 없습니다.", start)
    try:
        payload = await _http_get_json(
            "https://openapi.naver.com/v1/search/news.json",
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
            params={"query": "증시", "display": "1", "start": "1"},
        )
        return _test_result("naver", True, "connected", "Naver API 연결에 성공했습니다.", start, {"item_count": len(payload.get("items") or [])})
    except httpx.HTTPStatusError as exc:
        code = "authentication_failed" if exc.response.status_code in {401, 403} else "http_error"
        return _test_result("naver", False, code, "Naver API 연결을 확인하세요.", start)
    except httpx.TimeoutException:
        return _test_result("naver", False, "timeout", "Naver API 응답 시간이 초과되었습니다.", start)


@router.post("/test/openai")
async def test_openai(request: Request, db: Session = Depends(get_db), _local: None = Depends(require_local_request)):
    start = time.time()
    api_key = get_secret_value("OPENAI_API_KEY")
    if not api_key:
        return _test_result("openai", False, "not_configured", "OpenAI API Key가 없습니다.", start)
    try:
        payload = await _http_get_json(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        return _test_result("openai", True, "connected", "OpenAI API 연결에 성공했습니다.", start, {"model": runtime_openai_model(), "model_count": len(payload.get("data") or [])})
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        code = "authentication_failed" if status_code in {401, 403} else ("rate_limit" if status_code == 429 else "http_error")
        return _test_result("openai", False, code, "OpenAI API 상태를 확인하세요.", start)
    except httpx.TimeoutException:
        return _test_result("openai", False, "timeout", "OpenAI API 응답 시간이 초과되었습니다.", start)


@router.post("/test/sec")
async def test_sec(request: Request, _local: None = Depends(require_local_request)):
    start = time.time()
    user_agent = runtime_sec_user_agent()
    if not user_agent:
        return _test_result("sec", False, "not_configured", "SEC User-Agent가 없습니다.", start)
    try:
        payload = await _http_get_json(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        ok = isinstance(payload, dict) and ("data" in payload or "fields" in payload)
        return _test_result("sec", ok, "connected" if ok else "invalid_response", "SEC 연결 테스트가 완료되었습니다.", start)
    except httpx.TimeoutException:
        return _test_result("sec", False, "timeout", "SEC 응답 시간이 초과되었습니다.", start)
    except httpx.HTTPStatusError:
        return _test_result("sec", False, "http_error", "SEC 요청 상태를 확인하세요.", start)


@router.post("/test/krx/{service_name}")
async def test_krx_with_safe_diagnostics(
    service_name: str,
    request: Request,
    db: Session = Depends(get_db),
    _local: None = Depends(require_local_request),
):
    start = time.time()
    if service_name not in KRX_SERVICE_PATHS:
        raise HTTPException(status_code=404, detail="unknown KRX service")
    try:
        provider = build_krx_provider_from_runtime_settings(db)
        runtime_settings = provider._settings()
        api_id = runtime_settings.api_id_for(service_name)
        base_url = runtime_settings.base_url
        api_key = runtime_settings.api_key
    except KRXConfigurationError:
        api_id_setting = _krx_api_id_setting_name(service_name)
        api_id = get_runtime_setting(api_id_setting, "", db=db)
        missing = [
            name
            for name, value in {
                "KRX_API_KEY": get_secret_value("KRX_API_KEY"),
                "KRX_API_BASE_URL": get_runtime_setting("KRX_API_BASE_URL", "", db=db),
                api_id_setting: api_id,
            }.items()
            if not value
        ]
        details = {"service": service_name, "configuration_missing": missing, "configured_api_id": bool(api_id)}
        return _test_result("krx", False, "not_configured", "KRX API 설정이 없습니다.", start, details)

    request_path = build_krx_path(service_name, str(api_id))
    url = build_krx_url(str(base_url), service_name, str(api_id))
    base_date_param = str(get_runtime_setting("KRX_BASE_DATE_PARAM", "basDd", db=db) or "basDd")
    requested_base_date = _krx_default_base_date()
    params: dict[str, str] = {base_date_param: requested_base_date}

    try:
        response = await _http_get_response(
            str(url),
            headers={"AUTH_KEY": api_key},
            params=params,
            timeout=float(get_runtime_setting("KRX_SYNC_TIMEOUT", 30.0, db=db)),
        )
    except (httpx.TimeoutException, httpx.RequestError):
        return _test_result(
            "krx",
            False,
            "timeout",
            "KRX API 요청에 실패했습니다.",
            start,
            {"service": service_name, "request_path": request_path, "auth_header_present": True},
        )

    payload = _krx_response_json(response)
    details = _krx_detail(response, service_name, request_path, payload)
    details["requested_base_date"] = requested_base_date
    details["base_date_parameter_present"] = base_date_param in params
    if response.status_code >= 400:
        status_text, message = _krx_http_status(response.status_code)
        return _test_result("krx", False, status_text, message, start, details)
    if response.status_code == 200 and _krx_has_outblock(payload):
        rows = _krx_extract_rows_for_test(payload)
        transform_result = _krx_analyze_rows(service_name, rows, requested_base_date)
        details["row_count"] = len(rows)
        details["converted_count"] = transform_result.valid_count
        details["skipped_reason_counts"] = transform_result.skipped_reason_counts
        details["first_row_field_names"] = transform_result.first_row_field_names
        if service_name == "etf":
            details["ticker_diagnostics"] = ticker_diagnostics(rows, ETF_CODE_KEYS)
        if not rows:
            return _test_result("krx", False, "empty_response", "해당 기준일에 데이터가 없습니다.", start, details)
        return _test_result("krx", True, "connected", "KRX API 연결 테스트가 완료되었습니다.", start, details)
    if response.status_code == 200 and details.get("krx_resp_code"):
        return _test_result("krx", False, "krx_business_error", "KRX 업무 오류가 반환되었습니다.", start, details)
    return _test_result("krx", False, "invalid_response", "KRX 응답 형식을 확인하세요.", start, details)


async def _test_krx_legacy(service_name: str, request: Request, _local: None = Depends(require_local_request)):
    start = time.time()
    if service_name not in KRX_SERVICE_PATHS:
        raise HTTPException(status_code=404, detail="unknown KRX service")
    api_key = get_secret_value("KRX_API_KEY")
    base_url = get_runtime_setting("KRX_API_BASE_URL", "")
    if not api_key or not base_url:
        return _test_result("krx", False, "not_configured", "KRX API 설정이 없습니다.", start, {"service": service_name})
    base_date_param = str(get_runtime_setting("KRX_BASE_DATE_PARAM", "basDd") or "basDd")
    requested_base_date = _krx_default_base_date()
    params: dict[str, str] = {base_date_param: requested_base_date}
    try:
        payload = await _http_get_json(
            str(build_krx_url(str(base_url), service_name)),
            headers={"AUTH_KEY": api_key},
            params=params,
            timeout=float(get_runtime_setting("KRX_SYNC_TIMEOUT", 30.0)),
        )
        count = len(payload) if isinstance(payload, list) else len(payload.get("OutBlock_1") or payload.get("data") or [])
        if count == 0:
            return _test_result(
                "krx",
                False,
                "empty_response",
                "해당 기준일에 데이터가 없습니다.",
                start,
                {
                    "service": service_name,
                    "row_count": count,
                    "requested_base_date": requested_base_date,
                    "base_date_parameter_present": base_date_param in params,
                },
            )
        return _test_result("krx", True, "connected", "KRX API 연결 테스트가 완료되었습니다.", start, {"service": service_name, "row_count": count})
    except httpx.HTTPStatusError as exc:
        code = "authentication_failed" if exc.response.status_code in {401, 403} else "http_error"
        return _test_result("krx", False, code, "KRX 인증 또는 서비스 승인 상태를 확인하세요.", start, {"service": service_name})
    except (httpx.TimeoutException, httpx.RequestError):
        return _test_result("krx", False, "timeout", "KRX API 요청에 실패했습니다.", start, {"service": service_name})
