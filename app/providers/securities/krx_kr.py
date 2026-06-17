from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.providers.securities.base import SecurityMasterProvider
from app.schemas.security import SecurityIn
from app.services.app_setting_service import get_runtime_setting, get_secret_value
from app.utils.security_names import generate_name_aliases, normalize_ticker


class KRXConfigurationError(RuntimeError):
    pass


class KRXInvalidResponseError(RuntimeError):
    pass


class KRXEmptyResponseError(RuntimeError):
    pass


class KRXInvalidConfigurationError(KRXConfigurationError):
    pass


KRX_SERVICE_GROUPS = {
    "kospi": "sto",
    "kosdaq": "sto",
    "konex": "sto",
    "etf": "etp",
}
KRX_DEFAULT_API_IDS = {
    "kospi": "stk_isu_base_info",
    "kosdaq": "ksq_isu_base_info",
    "konex": "knx_isu_base_info",
    "etf": "etf_bydd_trd",
}
KRX_SERVICE_PATHS = {
    service: f"/{KRX_SERVICE_GROUPS[service]}/{api_id}"
    for service, api_id in KRX_DEFAULT_API_IDS.items()
}


STOCK_CODE_KEYS = (
    "ticker",
    "local_code",
    "ISU_SRT_CD",
    "ISU_CD",
    "isuSrtCd",
    "srtnCd",
    "shotnIsuCd",
    "short_code",
    "단축코드",
    "종목코드",
)
STOCK_NAME_KEYS = ("name", "ISU_NM", "ISU_ABBRV", "ISU_ENG_NM", "korSecnNm", "종목명", "한글종목명")
STOCK_SHORT_NAME_KEYS = ("short_name", "ISU_ABBRV", "isuAbbrv", "단축명")
STOCK_ENGLISH_NAME_KEYS = ("english_name", "ISU_ENG_NM", "engSecnNm", "영문명", "영문종목명")
STOCK_ISIN_KEYS = ("isin", "ISIN_CD", "ISU_ISIN_CD", "ISU_CD", "isuCd", "isinCd", "표준코드", "ISIN")
STOCK_LISTED_AT_KEYS = ("listed_at", "LIST_DD", "listDd", "상장일")
STOCK_TYPE_KEYS = ("stock_type", "security_type", "SECUGRP_NM", "KIND_STKCERT_TP_NM", "ISU_KIND", "주식종류", "증권구분")
STOCK_STATUS_KEYS = ("status", "MKT_WARN_TP_NM", "상장경보", "거래정지", "관리종목")
SECTOR_KEYS = ("sector", "IDX_IND_NM", "업종", "업종명")
INDUSTRY_KEYS = ("industry", "industryName", "세부업종")

ETF_CODE_KEYS = (
    "ticker",
    "local_code",
    "ISU_SRT_CD",
    "ISU_CD",
    "isuSrtCd",
    "srtnCd",
    "shotnIsuCd",
    "단축코드",
    "종목코드",
)
ETF_NAME_KEYS = ("name", "ISU_NM", "ISU_ABBRV", "korSecnNm", "ETF명", "종목명")
ETF_ISIN_KEYS = ("isin", "ISIN_CD", "ISU_ISIN_CD", "isuCd", "isinCd", "표준코드", "ISIN")
ETF_ISSUER_KEYS = ("issuer_name", "assetManager", "issuer", "운용사", "운용사명")


def build_krx_path(service_name: str, api_id: str | None = None) -> str:
    service_id = (api_id or KRX_DEFAULT_API_IDS[service_name]).strip().strip("/")
    return f"/{KRX_SERVICE_GROUPS[service_name]}/{service_id}"


def build_krx_url(base_url: str, service_name: str, api_id: str | None = None) -> str:
    path = build_krx_path(service_name, api_id)
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def validate_krx_api_id(service_name: str, api_id: str | None) -> str:
    value = (api_id or "").strip()
    if not value:
        raise KRXInvalidConfigurationError(f"missing API ID for {service_name}")
    if "://" in value or "/" in value or "\\" in value:
        raise KRXInvalidConfigurationError(f"invalid API ID path for {service_name}")
    expected = KRX_DEFAULT_API_IDS[service_name]
    if value != expected:
        raise KRXInvalidConfigurationError(f"unexpected API ID for {service_name}")
    return value


def build_krx_provider_from_runtime_settings(db: Session | None = None) -> "KrxKRProvider":
    settings = get_settings()
    runtime = KRXRuntimeSettings(
        api_key=str(get_secret_value("KRX_API_KEY") or settings.krx_api_key or ""),
        base_url=str(get_runtime_setting("KRX_API_BASE_URL", settings.krx_api_base_url, db=db) or ""),
        kospi_api_id=str(get_runtime_setting("KRX_KOSPI_BASIC_API_ID", settings.krx_kospi_basic_api_id, db=db) or ""),
        kosdaq_api_id=str(get_runtime_setting("KRX_KOSDAQ_BASIC_API_ID", settings.krx_kosdaq_basic_api_id, db=db) or ""),
        konex_api_id=str(get_runtime_setting("KRX_KONEX_BASIC_API_ID", settings.krx_konex_basic_api_id, db=db) or ""),
        etf_api_id=str(get_runtime_setting("KRX_ETF_DAILY_API_ID", settings.krx_etf_daily_api_id, db=db) or ""),
        base_date_param=str(get_runtime_setting("KRX_BASE_DATE_PARAM", getattr(settings, "krx_base_date_param", "basDd"), db=db) or "basDd"),
        sync_timeout=float(get_runtime_setting("KRX_SYNC_TIMEOUT", settings.krx_sync_timeout, db=db)),
        sync_max_retries=int(getattr(settings, "krx_sync_max_retries", 2)),
        business_day_lookback=int(get_runtime_setting("KRX_BUSINESS_DAY_LOOKBACK", settings.krx_business_day_lookback, db=db)),
    )
    required = [
        runtime.api_key,
        runtime.base_url,
        runtime.kospi_api_id,
        runtime.kosdaq_api_id,
        runtime.konex_api_id,
        runtime.etf_api_id,
    ]
    if any(not value for value in required):
        raise KRXConfigurationError("KRX API key, base URL, or service API ID is not configured")
    for service_name, api_id in {
        "kospi": runtime.kospi_api_id,
        "kosdaq": runtime.kosdaq_api_id,
        "konex": runtime.konex_api_id,
        "etf": runtime.etf_api_id,
    }.items():
        validate_krx_api_id(service_name, api_id)
    return KrxKRProvider(runtime)


@dataclass
class KRXRuntimeSettings:
    api_key: str
    base_url: str
    kospi_api_id: str
    kosdaq_api_id: str
    konex_api_id: str
    etf_api_id: str
    base_date_param: str
    sync_timeout: float
    sync_max_retries: int
    business_day_lookback: int

    def api_id_for(self, service_name: str) -> str:
        return {
            "kospi": self.kospi_api_id,
            "kosdaq": self.kosdaq_api_id,
            "konex": self.konex_api_id,
            "etf": self.etf_api_id,
        }[service_name]


@dataclass
class KRXFetchDiagnostic:
    service: str
    request_was_executed: bool = False
    configured_api_id_present: bool = False
    request_path: str | None = None
    upstream_status_code: int | None = None
    krx_response_code: str | None = None
    row_count: int | None = None
    error_type: str | None = None
    requested_base_date: str | None = None
    base_date_parameter_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "request_was_executed": self.request_was_executed,
            "configured_api_id_present": self.configured_api_id_present,
            "request_path": self.request_path,
            "upstream_status_code": self.upstream_status_code,
            "krx_response_code": self.krx_response_code,
            "row_count": self.row_count,
            "error_type": self.error_type,
            "requested_base_date": self.requested_base_date,
            "base_date_parameter_present": self.base_date_parameter_present,
        }


@dataclass
class KRXTransformResult:
    service: str
    securities: list[SecurityIn]
    received_count: int
    valid_count: int
    skipped_count: int
    skipped_reason_counts: dict[str, int]
    first_row_field_names: list[str]
    response_container_keys: list[str] = field(default_factory=list)
    ticker_diagnostics: dict[str, Any] = field(default_factory=dict)

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "row_count": self.received_count,
            "first_row_field_names": self.first_row_field_names,
            "converted_count": self.valid_count,
            "skipped_reason_counts": self.skipped_reason_counts,
            "ticker_diagnostics": self.ticker_diagnostics,
        }


@dataclass
class ParsedKRSecuritySnapshot:
    securities: list[SecurityIn]
    received_count: int
    valid_count: int
    skipped_count: int
    stock_count: int
    etf_count: int
    kospi_stock_count: int
    kosdaq_stock_count: int
    konex_stock_count: int
    recommendation_eligible_count: int
    recommendation_excluded_count: int
    leveraged_etf_count: int
    inverse_etf_count: int
    unknown_type_count: int
    duplicate_code_count: int
    snapshot_date: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _first(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _payload_error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    for value in payload.values():
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in ("respCode", "rsp_cd", "code"):
            value = candidate.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _payload_is_success_code(code: str | None) -> bool:
    return code is None or code in {"0", "00", "000", "0000", "OK", "ok", "SUCCESS"}


def _six_digit_code(value: str | None) -> str | None:
    if not value:
        return None
    code = str(value).strip().upper()
    if code.startswith("A") and len(code) == 7:
        code = code[1:]
    return code if re.fullmatch(r"[0-9A-Z]{6}", code) else None


def _pick_short_code(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, str | None, str | None]:
    saw_isin = False
    saw_same_length_bad_format = False
    saw_other_length = False
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip().upper()
        if not text:
            continue
        code = _six_digit_code(text)
        if code:
            return code, text, None
        if _is_isin(text):
            saw_isin = True
        elif len(text) == 6:
            saw_same_length_bad_format = True
        else:
            saw_other_length = True
    if saw_isin:
        return None, None, "missing_short_code"
    if saw_same_length_bad_format:
        return None, None, "invalid_ticker_format"
    if saw_other_length:
        return None, None, "invalid_ticker_length"
    return None, None, "missing_ticker"


def _parse_date(value: str | None):
    if not value:
        return None
    cleaned = str(value).strip().replace("/", "").replace("-", "")
    try:
        return datetime.strptime(cleaned, "%Y%m%d").date()
    except ValueError:
        return None


def _is_isin(value: str | None) -> str | None:
    if value and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", value.strip().upper()):
        return value.strip().upper()
    return None


def _row_field_names(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    return sorted(str(key) for key in rows[0].keys())


def _masked_ticker_pattern(value: str) -> str:
    text = value.strip().upper()
    if re.fullmatch(r"A\d{6}", text):
        return "A######"
    if re.fullmatch(r"\d{6}", text):
        return "######"
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", text):
        return "KR##########" if text.startswith("KR") else "AA##########"
    return "".join("#" if char.isdigit() else "A" if char.isalpha() else char for char in text)


def ticker_diagnostics(rows: list[dict[str, Any]], code_keys: tuple[str, ...]) -> dict[str, Any]:
    length_distribution: Counter[str] = Counter()
    masked_patterns: Counter[str] = Counter()
    starts_with_a_count = 0
    six_character_count = 0
    seven_character_count = 0
    twelve_character_count = 0
    other_length_count = 0
    alphanumeric_count = 0
    numeric_only_count = 0
    for row in rows:
        value = _first(row, *code_keys)
        if not value:
            length_distribution["missing"] += 1
            continue
        text = value.strip().upper()
        length = len(text)
        length_distribution[str(length)] += 1
        masked_patterns[_masked_ticker_pattern(text)] += 1
        starts_with_a_count += int(text.startswith("A"))
        six_character_count += int(length == 6)
        seven_character_count += int(length == 7)
        twelve_character_count += int(length == 12)
        other_length_count += int(length not in {6, 7, 12})
        alphanumeric_count += int(bool(re.fullmatch(r"[A-Z0-9]+", text)) and not text.isdigit())
        numeric_only_count += int(text.isdigit())
    return {
        "ticker_length_distribution": dict(length_distribution),
        "starts_with_a_count": starts_with_a_count,
        "six_character_count": six_character_count,
        "seven_character_count": seven_character_count,
        "twelve_character_count": twelve_character_count,
        "other_length_count": other_length_count,
        "alphanumeric_count": alphanumeric_count,
        "numeric_only_count": numeric_only_count,
        "masked_patterns": dict(masked_patterns.most_common(5)),
    }


def classify_kr_stock(row: dict[str, Any], name: str) -> dict[str, Any]:
    type_text = " ".join([_first(row, *STOCK_TYPE_KEYS) or "", name]).lower()
    status_text = (_first(row, *STOCK_STATUS_KEYS) or "").lower()
    if any(token in type_text for token in ("우선주", "preferred", "1우", "2우", "3우")):
        detail = "preferred_stock"
    elif any(token in type_text for token in ("스팩", "spac", "spaq")):
        detail = "spac"
    elif any(token in type_text for token in ("리츠", "reit")):
        detail = "real_estate_investment_trust"
    elif any(token in type_text for token in ("외국", "foreign")):
        detail = "foreign_share"
    elif any(token in type_text for token in ("dr", "depositary", "예탁")):
        detail = "depositary_receipt"
    elif any(token in type_text for token in ("보통주", "common", "ordinary")) or not type_text.strip():
        detail = "common_stock"
    else:
        detail = "common_stock"
    blocked_status = any(token in status_text for token in ("관리", "정지", "suspend", "halt", "delist"))
    eligible = detail in {"common_stock", "real_estate_investment_trust"} and not blocked_status
    return {
        "security_type_detail": detail,
        "is_recommendation_eligible": eligible,
        "source_status": status_text or None,
    }


def classify_kr_etf(name: str) -> dict[str, bool | str]:
    lowered = name.lower()
    leveraged = bool(re.search(r"(레버리지|leveraged|\b2x\b|\b3x\b)", lowered))
    inverse = bool(re.search(r"(인버스|inverse|short|bear|곱버스)", lowered))
    return {
        "security_type_detail": "etf",
        "is_recommendation_eligible": not (leveraged or inverse),
        "is_leveraged": leveraged,
        "is_inverse": inverse,
    }


def _source_status(row: dict[str, Any], market_segment: str, fallback: str | None = None) -> str:
    status = _first(row, *STOCK_STATUS_KEYS) or fallback
    return f"{market_segment}:{status}" if status else market_segment


def convert_krx_stock_basic_row(item: dict[str, Any], market_segment: str) -> tuple[SecurityIn | None, str | None]:
    if not isinstance(item, dict):
        return None, "invalid_row_type"
    code, code_source, code_error = _pick_short_code(item, STOCK_CODE_KEYS)
    if not code:
        return None, code_error
    name = _first(item, *STOCK_NAME_KEYS)
    short_name = _first(item, *STOCK_SHORT_NAME_KEYS)
    if not (name or short_name):
        return None, "missing_name"
    try:
        display_name = name or short_name or code
        english_name = _first(item, *STOCK_ENGLISH_NAME_KEYS)
        classification = classify_kr_stock(item, display_name)
        row = SecurityIn(
            country_code="KR",
            asset_type="stock",
            exchange_code="XKRX",
            exchange_name=market_segment,
            ticker=code,
            local_code=code,
            name=display_name,
            english_name=english_name,
            currency="KRW",
            isin=_is_isin(_first(item, *STOCK_ISIN_KEYS)),
            sector=_first(item, *SECTOR_KEYS),
            industry=_first(item, *INDUSTRY_KEYS),
            market_segment=market_segment,
            security_type_detail=str(classification["security_type_detail"]),
            is_recommendation_eligible=bool(classification["is_recommendation_eligible"]),
            source_status=_source_status(item, market_segment, classification.get("source_status")),
            listed_at=_parse_date(_first(item, *STOCK_LISTED_AT_KEYS)),
            source="krx_open_api",
        )
        row.aliases.extend(generate_name_aliases(row.name, row.english_name, row.ticker))
        if short_name and short_name != row.name:
            row.aliases.extend(generate_name_aliases(short_name, None, None))
        if code_source and str(code_source).strip().upper().startswith("A"):
            prefixed = str(code_source).strip().upper()
            row.aliases.append(
                {
                    "alias": prefixed,
                    "normalized_alias": normalize_ticker(prefixed),
                    "alias_type": "ticker_alias",
                    "language": None,
                }
            )
        return row, None
    except Exception:
        return None, "malformed_row"


def convert_kospi_basic_row(item: dict[str, Any]) -> tuple[SecurityIn | None, str | None]:
    return convert_krx_stock_basic_row(item, "KOSPI")


def convert_kosdaq_basic_row(item: dict[str, Any]) -> tuple[SecurityIn | None, str | None]:
    return convert_krx_stock_basic_row(item, "KOSDAQ")


def convert_konex_basic_row(item: dict[str, Any]) -> tuple[SecurityIn | None, str | None]:
    return convert_krx_stock_basic_row(item, "KONEX")


def convert_etf_daily_row(item: dict[str, Any], snapshot_date: str | None = None) -> tuple[SecurityIn | None, str | None]:
    if not isinstance(item, dict):
        return None, "invalid_row_type"
    code, code_source, code_error = _pick_short_code(item, ETF_CODE_KEYS)
    if not code:
        return None, code_error
    name = _first(item, *ETF_NAME_KEYS)
    if not name:
        return None, "missing_name"
    try:
        classification = classify_kr_etf(name)
        row = SecurityIn(
            country_code="KR",
            asset_type="etf",
            exchange_code="XKRX",
            exchange_name="ETF",
            ticker=code,
            local_code=code,
            name=name,
            english_name=_first(item, *STOCK_ENGLISH_NAME_KEYS),
            currency="KRW",
            isin=_is_isin(_first(item, *ETF_ISIN_KEYS)),
            issuer_name=_first(item, *ETF_ISSUER_KEYS),
            market_segment="ETF",
            security_type_detail=str(classification["security_type_detail"]),
            is_recommendation_eligible=bool(classification["is_recommendation_eligible"]),
            is_leveraged=bool(classification["is_leveraged"]),
            is_inverse=bool(classification["is_inverse"]),
            source_status=f"ETF:snapshot_date={snapshot_date}" if snapshot_date else "ETF",
            listed_at=_parse_date(_first(item, *STOCK_LISTED_AT_KEYS)),
            source="krx_open_api",
        )
        row.aliases.extend(generate_name_aliases(row.name, row.english_name, row.ticker))
        if code_source and str(code_source).strip().upper().startswith("A"):
            prefixed = str(code_source).strip().upper()
            row.aliases.append(
                {
                    "alias": prefixed,
                    "normalized_alias": normalize_ticker(prefixed),
                    "alias_type": "ticker_alias",
                    "language": None,
                }
            )
        if row.issuer_name:
            normalized_name = row.name.replace(row.issuer_name, "").strip()
            if normalized_name and normalized_name != row.name:
                row.aliases.extend(generate_name_aliases(normalized_name, None, None))
        return row, None
    except Exception:
        return None, "malformed_row"


def _analyze_rows(
    service: str,
    rows: list[dict[str, Any]],
    converter: Callable[[dict[str, Any]], tuple[SecurityIn | None, str | None]],
    code_keys: tuple[str, ...],
) -> KRXTransformResult:
    securities: list[SecurityIn] = []
    reasons: Counter[str] = Counter()
    seen: set[str] = set()
    for item in rows:
        security, reason = converter(item)
        if security is None:
            reasons[reason or "malformed_row"] += 1
            continue
        key = f"{security.country_code}:{security.exchange_code}:{security.ticker}"
        if key in seen:
            reasons["duplicate_security_key"] += 1
            continue
        seen.add(key)
        securities.append(security)
    return KRXTransformResult(
        service=service,
        securities=securities,
        received_count=len(rows),
        valid_count=len(securities),
        skipped_count=sum(reasons.values()),
        skipped_reason_counts=dict(reasons),
        first_row_field_names=_row_field_names(rows),
        ticker_diagnostics=ticker_diagnostics(rows, code_keys),
    )


def analyze_krx_stock_rows(rows: list[dict[str, Any]], market_segment: str) -> KRXTransformResult:
    converter = {
        "KOSPI": convert_kospi_basic_row,
        "KOSDAQ": convert_kosdaq_basic_row,
        "KONEX": convert_konex_basic_row,
    }[market_segment]
    return _analyze_rows(market_segment.lower(), rows, converter, STOCK_CODE_KEYS)


def analyze_krx_etf_rows(rows: list[dict[str, Any]], snapshot_date: str | None = None) -> KRXTransformResult:
    return _analyze_rows("etf", rows, lambda item: convert_etf_daily_row(item, snapshot_date), ETF_CODE_KEYS)


def transform_krx_stock_rows(rows: list[dict[str, Any]], market_segment: str) -> list[SecurityIn]:
    return analyze_krx_stock_rows(rows, market_segment).securities


def transform_krx_etf_rows(rows: list[dict[str, Any]], snapshot_date: str | None = None) -> list[SecurityIn]:
    return analyze_krx_etf_rows(rows, snapshot_date).securities


def transform_krx_rows(rows: list[dict[str, Any]]) -> list[SecurityIn]:
    stocks: list[dict[str, Any]] = []
    etfs: list[dict[str, Any]] = []
    for row in rows:
        market = (_first(row, "market", "MKT_TP_NM", "market_segment", "시장구분") or "KOSPI").upper()
        if market == "ETF" or str(row.get("asset_type") or "").lower() == "etf":
            etfs.append(row)
        else:
            stocks.append({**row, "market": market})
    securities: list[SecurityIn] = []
    for market in ("KOSPI", "KOSDAQ", "KONEX"):
        securities.extend(transform_krx_stock_rows([row for row in stocks if (row.get("market") or "KOSPI") == market], market))
    securities.extend(transform_krx_etf_rows(etfs))
    return securities


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise KRXInvalidResponseError("KRX response was not a JSON object")
    candidates: list[Any] = []
    for key in ("OutBlock_1", "output", "data", "items", "list", "response", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates = value
            break
        if isinstance(value, dict):
            for nested_key in ("OutBlock_1", "output", "data", "items", "list"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    candidates = nested
                    break
        if candidates:
            break
    return [item for item in candidates if isinstance(item, dict)]


def _snapshot(
    securities: list[SecurityIn],
    received_count: int,
    skipped_count: int,
    snapshot_date: str | None,
    diagnostics: dict[str, Any] | None = None,
) -> ParsedKRSecuritySnapshot:
    keys = [f"{item.country_code}:{item.exchange_code}:{item.ticker}" for item in securities]
    duplicate_code_count = len(keys) - len(set(keys))
    return ParsedKRSecuritySnapshot(
        securities=securities,
        received_count=received_count,
        valid_count=len(securities),
        skipped_count=skipped_count,
        stock_count=sum(1 for item in securities if item.asset_type == "stock"),
        etf_count=sum(1 for item in securities if item.asset_type == "etf"),
        kospi_stock_count=sum(1 for item in securities if item.asset_type == "stock" and item.market_segment == "KOSPI"),
        kosdaq_stock_count=sum(1 for item in securities if item.asset_type == "stock" and item.market_segment == "KOSDAQ"),
        konex_stock_count=sum(1 for item in securities if item.asset_type == "stock" and item.market_segment == "KONEX"),
        recommendation_eligible_count=sum(1 for item in securities if item.is_recommendation_eligible),
        recommendation_excluded_count=sum(1 for item in securities if not item.is_recommendation_eligible),
        leveraged_etf_count=sum(1 for item in securities if item.asset_type == "etf" and item.is_leveraged),
        inverse_etf_count=sum(1 for item in securities if item.asset_type == "etf" and item.is_inverse),
        unknown_type_count=sum(1 for item in securities if item.security_type_detail in (None, "other")),
        duplicate_code_count=duplicate_code_count,
        snapshot_date=snapshot_date,
        diagnostics=diagnostics or {},
    )


class KrxKRProvider(SecurityMasterProvider):
    name = "krx_open_api"
    country_code = "KR"

    def __init__(self, runtime_settings: KRXRuntimeSettings | None = None):
        self.runtime_settings = runtime_settings
        self.fetch_diagnostics: list[dict[str, Any]] = []

    def _settings(self):
        if self.runtime_settings is not None:
            return self.runtime_settings
        settings = get_settings()
        settings.krx_api_key = get_secret_value("KRX_API_KEY") or settings.krx_api_key
        settings.krx_api_base_url = get_runtime_setting("KRX_API_BASE_URL", settings.krx_api_base_url)
        settings.krx_kospi_basic_api_id = get_runtime_setting("KRX_KOSPI_BASIC_API_ID", settings.krx_kospi_basic_api_id)
        settings.krx_kosdaq_basic_api_id = get_runtime_setting("KRX_KOSDAQ_BASIC_API_ID", settings.krx_kosdaq_basic_api_id)
        settings.krx_konex_basic_api_id = get_runtime_setting("KRX_KONEX_BASIC_API_ID", settings.krx_konex_basic_api_id)
        settings.krx_etf_daily_api_id = get_runtime_setting("KRX_ETF_DAILY_API_ID", settings.krx_etf_daily_api_id)
        settings.krx_base_date_param = get_runtime_setting("KRX_BASE_DATE_PARAM", getattr(settings, "krx_base_date_param", "basDd"))
        settings.krx_sync_timeout = get_runtime_setting("KRX_SYNC_TIMEOUT", settings.krx_sync_timeout)
        settings.krx_business_day_lookback = get_runtime_setting("KRX_BUSINESS_DAY_LOOKBACK", settings.krx_business_day_lookback)
        required = [
            settings.krx_api_key,
            settings.krx_api_base_url,
            settings.krx_kospi_basic_api_id,
            settings.krx_kosdaq_basic_api_id,
            settings.krx_konex_basic_api_id,
            settings.krx_etf_daily_api_id,
        ]
        if any(not value for value in required):
            raise KRXConfigurationError("KRX API key, base URL, or service API ID is not configured")
        return KRXRuntimeSettings(
            api_key=str(settings.krx_api_key),
            base_url=str(settings.krx_api_base_url),
            kospi_api_id=validate_krx_api_id("kospi", str(settings.krx_kospi_basic_api_id)),
            kosdaq_api_id=validate_krx_api_id("kosdaq", str(settings.krx_kosdaq_basic_api_id)),
            konex_api_id=validate_krx_api_id("konex", str(settings.krx_konex_basic_api_id)),
            etf_api_id=validate_krx_api_id("etf", str(settings.krx_etf_daily_api_id)),
            base_date_param=str(settings.krx_base_date_param),
            sync_timeout=float(settings.krx_sync_timeout),
            sync_max_retries=int(settings.krx_sync_max_retries),
            business_day_lookback=int(settings.krx_business_day_lookback),
        )

    def _api_id(self, service_name: str) -> str:
        return self._settings().api_id_for(service_name)

    async def _fetch_json(self, client: httpx.AsyncClient, service_name: str, params: dict[str, str] | None = None) -> Any:
        settings = self._settings()
        request_params = params or {}
        api_id = self._api_id(service_name)
        headers = {"AUTH_KEY": settings.api_key or ""}
        request_path = build_krx_path(service_name, api_id)
        url = build_krx_url(str(settings.base_url), service_name, api_id)
        requested_base_date = str(request_params.get(settings.base_date_param) or "") or None
        diagnostic = KRXFetchDiagnostic(
            service=service_name,
            configured_api_id_present=bool(api_id),
            request_path=request_path,
            requested_base_date=requested_base_date,
            base_date_parameter_present=settings.base_date_param in request_params,
        )
        last_exc: Exception | None = None
        for attempt in range(settings.sync_max_retries + 1):
            try:
                diagnostic.request_was_executed = True
                response = await client.get(url, params=request_params, headers=headers)
                diagnostic.upstream_status_code = getattr(response, "status_code", 200)
                response.raise_for_status()
                payload = response.json()
                error_code = _payload_error_code(payload)
                diagnostic.krx_response_code = error_code
                if not _payload_is_success_code(error_code):
                    diagnostic.error_type = "KRXBusinessError"
                    raise KRXInvalidResponseError(f"KRX business error: {error_code}")
                diagnostic.row_count = len(_extract_rows(payload))
                self.fetch_diagnostics.append(diagnostic.as_dict())
                return payload
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
                last_exc = exc
                diagnostic.error_type = type(exc).__name__
                if attempt >= settings.sync_max_retries:
                    self.fetch_diagnostics.append(diagnostic.as_dict())
                    raise
                await asyncio.sleep(min(2**attempt, 4))
            except KRXInvalidResponseError as exc:
                last_exc = exc
                self.fetch_diagnostics.append(diagnostic.as_dict())
                raise
        raise RuntimeError(f"unreachable KRX fetch state: {type(last_exc).__name__ if last_exc else 'none'}")

    async def fetch_kospi_basic(self, base_date: str | None = None) -> list[dict[str, Any]]:
        settings = self._settings()
        async with httpx.AsyncClient(timeout=settings.sync_timeout, follow_redirects=True) as client:
            params = {settings.base_date_param: base_date} if base_date else {}
            return _extract_rows(await self._fetch_json(client, "kospi", params))

    async def fetch_kosdaq_basic(self, base_date: str | None = None) -> list[dict[str, Any]]:
        settings = self._settings()
        async with httpx.AsyncClient(timeout=settings.sync_timeout, follow_redirects=True) as client:
            params = {settings.base_date_param: base_date} if base_date else {}
            return _extract_rows(await self._fetch_json(client, "kosdaq", params))

    async def fetch_konex_basic(self, base_date: str | None = None) -> list[dict[str, Any]]:
        settings = self._settings()
        async with httpx.AsyncClient(timeout=settings.sync_timeout, follow_redirects=True) as client:
            params = {settings.base_date_param: base_date} if base_date else {}
            return _extract_rows(await self._fetch_json(client, "konex", params))

    async def fetch_etf_daily(self, base_date: str) -> list[dict[str, Any]]:
        settings = self._settings()
        async with httpx.AsyncClient(timeout=settings.sync_timeout, follow_redirects=True) as client:
            return _extract_rows(
                await self._fetch_json(
                    client,
                    "etf",
                    {settings.base_date_param: base_date},
                )
            )

    async def fetch_latest_etf_snapshot(self) -> tuple[list[dict[str, Any]], str | None]:
        settings = self._settings()
        today = datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=1)
        for offset in range(settings.business_day_lookback):
            base_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            rows = await self.fetch_etf_daily(base_date)
            if rows:
                return rows, base_date
        raise KRXEmptyResponseError("No valid KRX ETF snapshot found")

    async def fetch_snapshot(self) -> ParsedKRSecuritySnapshot:
        settings = self._settings()
        today = datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=1)
        selected: tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None = None
        for offset in range(settings.business_day_lookback):
            snapshot_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            kospi_rows, kosdaq_rows = await asyncio.gather(
                self._fetch_with_optional_base_date("kospi", snapshot_date),
                self._fetch_with_optional_base_date("kosdaq", snapshot_date),
            )
            if not kospi_rows or not kosdaq_rows:
                continue
            konex_rows = await self._fetch_optional_market("konex", snapshot_date)
            etf_rows = await self._fetch_optional_market("etf", snapshot_date)
            selected = (snapshot_date, kospi_rows, kosdaq_rows, konex_rows, etf_rows)
            break
        if selected is None:
            exc = KRXEmptyResponseError("No valid KRX stock snapshot found")
            exc.diagnostics = {"fetch": list(self.fetch_diagnostics)}  # type: ignore[attr-defined]
            raise exc
        snapshot_date, kospi_rows, kosdaq_rows, konex_rows, etf_rows = selected
        results = [
            analyze_krx_stock_rows(kospi_rows, "KOSPI"),
            analyze_krx_stock_rows(kosdaq_rows, "KOSDAQ"),
            analyze_krx_stock_rows(konex_rows, "KONEX"),
            analyze_krx_etf_rows(etf_rows, snapshot_date),
        ]
        securities = [security for result in results for security in result.securities]
        received_count = sum(result.received_count for result in results)
        skipped_count = sum(result.skipped_count for result in results)
        diagnostics = {result.service: result.safe_diagnostics() for result in results}
        diagnostics["skipped_reason_counts"] = dict(sum((Counter(result.skipped_reason_counts) for result in results), Counter()))
        diagnostics["fetch"] = list(self.fetch_diagnostics)
        return _snapshot(securities, received_count, skipped_count, snapshot_date, diagnostics)

    async def fetch_securities(self) -> list[SecurityIn]:
        return (await self.fetch_snapshot()).securities

    async def _fetch_with_optional_base_date(self, service_name: str, base_date: str) -> list[dict[str, Any]]:
        fetcher = {
            "kospi": self.fetch_kospi_basic,
            "kosdaq": self.fetch_kosdaq_basic,
            "konex": self.fetch_konex_basic,
            "etf": self.fetch_etf_daily,
        }[service_name]
        try:
            return await fetcher(base_date)
        except TypeError:
            return await fetcher()

    async def _fetch_optional_market(self, service_name: str, base_date: str) -> list[dict[str, Any]]:
        try:
            return await self._fetch_with_optional_base_date(service_name, base_date)
        except Exception as exc:
            self.fetch_diagnostics.append(
                KRXFetchDiagnostic(
                    service=service_name,
                    request_was_executed=False,
                    configured_api_id_present=True,
                    request_path=build_krx_path(service_name, self._api_id(service_name)),
                    requested_base_date=base_date,
                    base_date_parameter_present=True,
                    row_count=0,
                    error_type=type(exc).__name__,
                ).as_dict()
            )
            return []
