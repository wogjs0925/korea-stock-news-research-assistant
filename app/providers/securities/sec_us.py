from __future__ import annotations

import re

import httpx

from app.core.config import get_settings
from app.providers.securities.base import SecurityMasterProvider
from app.schemas.security import SecurityIn
from app.services.app_setting_service import runtime_sec_user_agent
from app.utils.security_names import generate_name_aliases


_EXCHANGE_MAP = {
    "Nasdaq": ("XNAS", "NASDAQ"),
    "NASDAQ": ("XNAS", "NASDAQ"),
    "Nasdaq Global Select Market": ("XNAS", "NASDAQ"),
    "Nasdaq Global Market": ("XNAS", "NASDAQ"),
    "Nasdaq Capital Market": ("XNAS", "NASDAQ"),
    "NYSE": ("XNYS", "NYSE"),
    "New York Stock Exchange": ("XNYS", "NYSE"),
    "NYSE American": ("XASE", "NYSE American"),
    "NYSE Arca": ("ARCX", "NYSE Arca"),
    "Cboe BZX": ("BATS", "Cboe BZX"),
}


def normalize_us_ticker_for_match(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().upper()
    value = re.sub(r"\s+", "", value)
    return value.replace(".", "-")


def sec_exchange_to_internal(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    normalized = value.strip()
    if normalized in _EXCHANGE_MAP:
        return _EXCHANGE_MAP[normalized]
    lowered = normalized.lower()
    if "nasdaq" in lowered:
        return "XNAS", "NASDAQ"
    if "new york stock exchange" in lowered or lowered == "nyse":
        return "XNYS", "NYSE"
    if "nyse american" in lowered:
        return "XASE", "NYSE American"
    if "nyse arca" in lowered:
        return "ARCX", "NYSE Arca"
    if "cboe" in lowered or "bats" in lowered:
        return "BATS", "Cboe BZX"
    return None, normalized


def map_sec_payload(payload: dict) -> dict[str, dict]:
    if "fields" in payload and "data" in payload:
        fields = payload.get("fields") or []
        records = payload.get("data") or []
        if not fields or not isinstance(records, list):
            raise ValueError("invalid SEC company ticker payload")
        return {str(index): dict(zip(fields, record)) for index, record in enumerate(records)}
    return payload


def transform_sec_company_tickers(payload: dict) -> list[SecurityIn]:
    rows: list[SecurityIn] = []
    for item in map_sec_payload(payload).values():
        ticker = str(item.get("ticker") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        if not ticker or not title:
            continue
        exchange_code, exchange_name = sec_exchange_to_internal(str(item.get("exchange") or ""))
        exchange_code = exchange_code or "USOTC"
        exchange_name = exchange_name or str(item.get("exchange") or "US")
        cik_value = item.get("cik_str") if item.get("cik_str") is not None else item.get("cik")
        cik = str(cik_value or "").zfill(10) if cik_value is not None else None
        row = SecurityIn(
            country_code="US",
            asset_type="etf" if " ETF" in title.upper() or " ETF " in title.upper() else "stock",
            exchange_code=exchange_code,
            exchange_name=exchange_name,
            ticker=ticker,
            name=title,
            english_name=title,
            currency="USD",
            cik=cik,
            source="sec_us",
        )
        row.aliases.extend(generate_name_aliases(row.name, row.english_name, row.ticker))
        rows.append(row)
    return rows


def sec_enrichment_map(payload: dict) -> dict[tuple[str, str], dict[str, str | None]]:
    result: dict[tuple[str, str], dict[str, str | None]] = {}
    for row in transform_sec_company_tickers(payload):
        result[(normalize_us_ticker_for_match(row.ticker), row.exchange_code)] = {
            "cik": row.cik,
            "name": row.name,
            "ticker": row.ticker,
            "exchange_code": row.exchange_code,
        }
    return result


class SecUSProvider(SecurityMasterProvider):
    name = "sec_us"
    country_code = "US"

    async def fetch_securities(self) -> list[SecurityIn]:
        return transform_sec_company_tickers(await self.fetch_payload())

    async def fetch_payload(self) -> dict:
        settings = get_settings()
        user_agent = runtime_sec_user_agent(default=settings.sec_user_agent)
        if not user_agent:
            raise ValueError("SEC_USER_AGENT is required for SEC requests")
        async with httpx.AsyncClient(timeout=settings.security_sync_timeout) as client:
            response = await client.get(
                settings.sec_company_tickers_url,
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                follow_redirects=True,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError("invalid SEC company ticker JSON") from exc
        return map_sec_payload(data)
