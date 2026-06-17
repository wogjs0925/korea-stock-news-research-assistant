from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.security import Security
from app.models.security_alias import SecurityAlias
from app.repositories.security_repository import ensure_security_tables_schema, save_alias_with_created
from app.utils.security_names import generate_name_aliases, normalize_company_name, normalize_ticker

COMMON_ALIAS_STOPWORDS = {
    "holdings",
    "holding",
    "group",
    "corp",
    "corporation",
    "company",
    "co",
    "inc",
    "ltd",
    "limited",
    "technology",
    "technologies",
    "energy",
    "common stock",
    "ordinary shares",
    "etf",
}

CURATED_ALIAS_SEEDS: dict[str, list[str]] = {
    "035420": ["네이버", "네이버주식회사", "NAVER Corp.", "Naver"],
    "naver": ["네이버", "네이버주식회사", "NAVER Corp.", "Naver"],
    "005930": ["삼성전자", "Samsung Electronics", "Samsung Electronics Co., Ltd.", "삼성"],
    "samsung electronics": ["삼성전자", "Samsung Electronics Co., Ltd.", "삼성"],
    "000660": ["SK하이닉스", "SK hynix", "SK Hynix Inc.", "하이닉스"],
    "sk hynix": ["SK하이닉스", "SK Hynix Inc.", "하이닉스"],
}


@dataclass(frozen=True)
class AliasCandidate:
    alias: str
    alias_type: str
    locale: str | None
    source: str = "rule"
    confidence: float = 0.9


def _locale_for(value: str) -> str:
    return "ko" if re.search(r"[가-힣]", value) else "en"


def _is_too_generic(alias: str) -> bool:
    normalized = normalize_company_name(alias)
    if not normalized:
        return True
    if normalized in COMMON_ALIAS_STOPWORDS:
        return True
    return len(normalized) < 2 and not normalized.isdigit()


def _clean_display_alias(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    cleaned = re.sub(r"\b(Common Stock|Ordinary Shares|Class [A-Z])\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned


def _short_aliases(value: str) -> list[str]:
    cleaned = _clean_display_alias(value)
    normalized = normalize_company_name(cleaned)
    aliases: list[str] = []
    if cleaned and cleaned != value:
        aliases.append(cleaned)
    if normalized and normalized != normalize_company_name(value):
        aliases.append(normalized)
    return aliases


def generated_alias_candidates(security: Security) -> list[AliasCandidate]:
    raw: list[AliasCandidate] = []
    for item in generate_name_aliases(security.name, security.english_name, security.ticker, security.issuer_name):
        alias = str(item.get("alias") or "").strip()
        alias_type = str(item.get("alias_type") or "rule_generated")
        if alias:
            raw.append(AliasCandidate(alias, alias_type, item.get("language"), "rule", 0.95))

    if security.local_code:
        raw.append(AliasCandidate(security.local_code, "ticker", "en", "rule", 1.0))
    for source_value, alias_type in (
        (security.name, "short_name"),
        (security.english_name, "short_name"),
        (security.issuer_name, "short_name"),
    ):
        if not source_value:
            continue
        for alias in _short_aliases(source_value):
            raw.append(AliasCandidate(alias, alias_type, _locale_for(alias), "rule", 0.85))

    seed_keys = {
        normalize_ticker(security.ticker),
        normalize_company_name(security.name),
        normalize_company_name(security.english_name),
        normalize_company_name(security.issuer_name),
    }
    for key in seed_keys:
        for alias in CURATED_ALIAS_SEEDS.get(key, []):
            raw.append(AliasCandidate(alias, "manual", _locale_for(alias), "manual", 0.98))

    seen: set[str] = set()
    result: list[AliasCandidate] = []
    for candidate in raw:
        if _is_too_generic(candidate.alias):
            continue
        normalized = normalize_ticker(candidate.alias) if candidate.alias_type in {"ticker", "ticker_alias"} else normalize_company_name(candidate.alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(candidate)
    return result


def _ambiguous_normalized_aliases(db: Session, candidates: Iterable[tuple[int, AliasCandidate]]) -> set[str]:
    normalized_by_security: dict[str, set[int]] = {}
    for security_id, candidate in candidates:
        normalized = normalize_ticker(candidate.alias) if candidate.alias_type in {"ticker", "ticker_alias"} else normalize_company_name(candidate.alias)
        if not normalized:
            continue
        normalized_by_security.setdefault(normalized, set()).add(security_id)

    existing = db.execute(
        select(SecurityAlias.normalized_alias, SecurityAlias.security_id)
        .where(SecurityAlias.normalized_alias.in_(list(normalized_by_security)))
    ).all()
    for normalized, security_id in existing:
        normalized_by_security.setdefault(str(normalized), set()).add(int(security_id))
    return {alias for alias, security_ids in normalized_by_security.items() if len(security_ids) > 1}


def backfill_security_aliases(db: Session) -> dict[str, int | float]:
    ensure_security_tables_schema(db)
    started = time.time()
    securities = list(db.scalars(select(Security).where(Security.is_active == True)).all())
    prepared = [(security.id, candidate) for security in securities for candidate in generated_alias_candidates(security)]
    ambiguous_aliases = _ambiguous_normalized_aliases(db, prepared)

    created = skipped = ambiguous = 0
    for security_id, candidate in prepared:
        normalized = normalize_ticker(candidate.alias) if candidate.alias_type in {"ticker", "ticker_alias"} else normalize_company_name(candidate.alias)
        if normalized in ambiguous_aliases and candidate.alias_type not in {"ticker", "ticker_alias"}:
            ambiguous += 1
            continue
        _row, was_created = save_alias_with_created(
            db,
            security_id,
            candidate.alias,
            candidate.alias_type,
            candidate.locale,
            candidate.source,
            confidence=candidate.confidence,
            locale=candidate.locale,
        )
        if was_created:
            created += 1
        else:
            skipped += 1

    db.commit()
    return {
        "scanned_security_count": len(securities),
        "created_alias_count": created,
        "skipped_alias_count": skipped,
        "ambiguous_alias_count": ambiguous,
        "total_alias_count": int(db.scalar(select(func.count(SecurityAlias.id))) or 0),
        "duration_ms": int((time.time() - started) * 1000),
    }
