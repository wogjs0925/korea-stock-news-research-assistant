from __future__ import annotations

import asyncio
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any

import httpx

from app.core.config import get_settings
from app.providers.securities.base import SecurityMasterProvider
from app.schemas.security import SecurityIn
from app.utils.security_names import generate_name_aliases, normalize_ticker


_NASDAQ_MARKETS = {
    "Q": "NASDAQ Global Select Market",
    "G": "NASDAQ Global Market",
    "S": "NASDAQ Capital Market",
}

_OTHER_EXCHANGES = {
    "N": ("XNYS", "New York Stock Exchange"),
    "A": ("XASE", "NYSE American"),
    "P": ("ARCX", "NYSE Arca"),
    "Z": ("BATS", "Cboe BZX"),
    "V": ("IEXG", "Investors Exchange"),
}


@dataclass
class ParsedUSSecuritySnapshot:
    securities: list[SecurityIn]
    received_count: int
    valid_count: int
    skipped_count: int
    stock_count: int
    etf_count: int
    excluded_security_count: int
    unknown_exchange_count: int
    source_file_created_at: str | None


def classify_security_name(name: str, is_etf: bool) -> dict[str, Any]:
    lowered = name.lower()
    leveraged = bool(re.search(r"\b(ultra|2x|3x|4x|leveraged)\b", lowered))
    inverse = bool(re.search(r"\b(inverse|short|bear)\b", lowered))
    if is_etf:
        return {
            "security_type_detail": "etf",
            "asset_type": "etf",
            "is_recommendation_eligible": True,
            "is_leveraged": leveraged,
            "is_inverse": inverse,
        }
    excluded_patterns = [
        ("warrant", "warrant"),
        ("right", "right"),
        ("unit", "unit"),
        ("preferred", "preferred_stock"),
        ("preference", "preferred_stock"),
        ("note", "note"),
        ("bond", "bond"),
        ("closed end", "closed_end_fund"),
        ("when issued", "when_issued"),
    ]
    for pattern, detail in excluded_patterns:
        if pattern in lowered:
            return {
                "security_type_detail": detail,
                "asset_type": "stock",
                "is_recommendation_eligible": False,
                "is_leveraged": False,
                "is_inverse": False,
            }
    stock_patterns = ["common stock", "ordinary shares", "class a", "class b", "adr", "ads"]
    if any(pattern in lowered for pattern in stock_patterns):
        detail = "adr" if "adr" in lowered or "ads" in lowered else "common_stock"
        return {
            "security_type_detail": detail,
            "asset_type": "stock",
            "is_recommendation_eligible": True,
            "is_leveraged": False,
            "is_inverse": False,
        }
    return {
        "security_type_detail": "unknown",
        "asset_type": "stock",
        "is_recommendation_eligible": False,
        "is_leveraged": False,
        "is_inverse": False,
    }


def _reader(text: str) -> tuple[list[dict[str, str]], str | None, int]:
    rows: list[dict[str, str]] = []
    source_file_created_at = None
    received = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("File Creation Time"):
            source_file_created_at = clean_file_creation_time(line)
            continue
        received += 1
        rows.append(line)
    if not rows:
        return [], source_file_created_at, received
    parsed = list(csv.DictReader(StringIO("\n".join(rows)), delimiter="|"))
    return parsed, source_file_created_at, received


def clean_file_creation_time(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("File Creation Time:", "").replace("File Creation Time", "")
    cleaned = cleaned.strip().rstrip("|").strip()
    for fmt in ("%m%d%Y%H:%M", "%m%d%Y %H:%M", "%Y%m%d%H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return cleaned or None


def parse_nasdaq_listed(text: str) -> ParsedUSSecuritySnapshot:
    rows, created_at, received = _reader(text)
    securities: list[SecurityIn] = []
    skipped = unknown_exchange = 0
    for row in rows:
        ticker = normalize_ticker(row.get("Symbol"))
        if not ticker or row.get("Test Issue") == "Y":
            skipped += 1
            continue
        name = (row.get("Security Name") or "").strip()
        market_category = (row.get("Market Category") or "").strip()
        exchange_name = _NASDAQ_MARKETS.get(market_category, f"NASDAQ {market_category}".strip())
        is_etf = row.get("ETF") == "Y"
        classification = classify_security_name(name, is_etf=is_etf)
        financial_status = row.get("Financial Status") or None
        eligible = bool(classification["is_recommendation_eligible"]) and financial_status in (None, "", "N")
        item = SecurityIn(
            country_code="US",
            asset_type=classification["asset_type"],
            exchange_code="XNAS",
            exchange_name=exchange_name,
            ticker=ticker,
            name=name,
            english_name=name,
            currency="USD",
            source="nasdaq_trader_us",
            security_type_detail=classification["security_type_detail"],
            is_recommendation_eligible=eligible,
            is_leveraged=classification["is_leveraged"],
            is_inverse=classification["is_inverse"],
            source_status=financial_status,
        )
        item.aliases.extend(generate_name_aliases(item.name, item.english_name, item.ticker))
        securities.append(item)
    return _snapshot(securities, received, skipped, unknown_exchange, created_at)


def parse_other_listed(text: str) -> ParsedUSSecuritySnapshot:
    rows, created_at, received = _reader(text)
    securities: list[SecurityIn] = []
    skipped = unknown_exchange = 0
    for row in rows:
        ticker = normalize_ticker(row.get("ACT Symbol"))
        if not ticker or row.get("Test Issue") == "Y":
            skipped += 1
            continue
        exchange_raw = (row.get("Exchange") or "").strip()
        exchange_code, exchange_name = _OTHER_EXCHANGES.get(exchange_raw, (f"UNKNOWN_{exchange_raw or 'US'}", exchange_raw or "Unknown US Exchange"))
        if exchange_code.startswith("UNKNOWN_"):
            unknown_exchange += 1
        name = (row.get("Security Name") or "").strip()
        is_etf = row.get("ETF") == "Y"
        classification = classify_security_name(name, is_etf=is_etf)
        item = SecurityIn(
            country_code="US",
            asset_type=classification["asset_type"],
            exchange_code=exchange_code,
            exchange_name=exchange_name,
            ticker=ticker,
            name=name,
            english_name=name,
            currency="USD",
            source="nasdaq_trader_us",
            security_type_detail=classification["security_type_detail"],
            is_recommendation_eligible=classification["is_recommendation_eligible"],
            is_leveraged=classification["is_leveraged"],
            is_inverse=classification["is_inverse"],
            source_status=exchange_raw if exchange_code.startswith("UNKNOWN_") else None,
        )
        item.aliases.extend(generate_name_aliases(item.name, item.english_name, item.ticker))
        for alias_value in (row.get("CQS Symbol"), row.get("NASDAQ Symbol")):
            if alias_value and normalize_ticker(alias_value) != ticker:
                item.aliases.append(
                    {
                        "alias": alias_value,
                        "normalized_alias": normalize_ticker(alias_value),
                        "alias_type": "ticker_alias",
                        "language": "en",
                    }
                )
        securities.append(item)
    return _snapshot(securities, received, skipped, unknown_exchange, created_at)


def _snapshot(
    securities: list[SecurityIn],
    received: int,
    skipped: int,
    unknown_exchange: int,
    source_file_created_at: str | None,
) -> ParsedUSSecuritySnapshot:
    return ParsedUSSecuritySnapshot(
        securities=securities,
        received_count=received,
        valid_count=len(securities),
        skipped_count=skipped,
        stock_count=sum(1 for item in securities if item.asset_type == "stock"),
        etf_count=sum(1 for item in securities if item.asset_type == "etf"),
        excluded_security_count=sum(1 for item in securities if not item.is_recommendation_eligible),
        unknown_exchange_count=unknown_exchange,
        source_file_created_at=source_file_created_at,
    )


def merge_snapshots(*snapshots: ParsedUSSecuritySnapshot) -> ParsedUSSecuritySnapshot:
    securities: list[SecurityIn] = []
    for snapshot in snapshots:
        securities.extend(snapshot.securities)
    return ParsedUSSecuritySnapshot(
        securities=securities,
        received_count=sum(item.received_count for item in snapshots),
        valid_count=sum(item.valid_count for item in snapshots),
        skipped_count=sum(item.skipped_count for item in snapshots),
        stock_count=sum(item.stock_count for item in snapshots),
        etf_count=sum(item.etf_count for item in snapshots),
        excluded_security_count=sum(item.excluded_security_count for item in snapshots),
        unknown_exchange_count=sum(item.unknown_exchange_count for item in snapshots),
        source_file_created_at="; ".join(
            [
                f"{label}={item.source_file_created_at}"
                for label, item in zip(("nasdaqlisted", "otherlisted"), snapshots)
                if item.source_file_created_at
            ]
        )
        or None,
    )


class NasdaqTraderUSProvider(SecurityMasterProvider):
    name = "nasdaq_trader_us"
    country_code = "US"

    async def _fetch_text(self, client: httpx.AsyncClient, url: str) -> str:
        settings = get_settings()
        last_exc: Exception | None = None
        for attempt in range(settings.security_sync_max_retries + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt >= settings.security_sync_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 4))
        raise RuntimeError(f"unreachable fetch state: {type(last_exc).__name__ if last_exc else 'none'}")

    async def fetch_snapshot(self) -> ParsedUSSecuritySnapshot:
        settings = get_settings()
        async with httpx.AsyncClient(
            timeout=settings.security_sync_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.security_sync_user_agent},
        ) as client:
            nasdaq_text, other_text = await asyncio.gather(
                self._fetch_text(client, settings.nasdaq_listed_url),
                self._fetch_text(client, settings.nasdaq_other_listed_url),
            )
        return merge_snapshots(parse_nasdaq_listed(nasdaq_text), parse_other_listed(other_text))

    async def fetch_securities(self) -> list[SecurityIn]:
        return (await self.fetch_snapshot()).securities
