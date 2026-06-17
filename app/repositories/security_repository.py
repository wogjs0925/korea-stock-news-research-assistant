from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.security import Security
from app.models.security_alias import SecurityAlias
from app.models.etf_holding import ETFHolding
from app.models.security_sync_run import SecuritySyncRun
from app.models.theme_candidate_run import ThemeCandidateRun
from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.schemas.security import SecurityIn
from app.utils.security_names import generate_security_key, normalize_company_name, normalize_ticker


def ensure_security_tables_schema(db: Session) -> None:
    bind = db.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "sqlite":
        return

    column_specs = {
        "securities": {
            "market_segment": "VARCHAR(32)",
            "security_type_detail": "VARCHAR(64)",
            "is_recommendation_eligible": "BOOLEAN NOT NULL DEFAULT 1",
            "is_leveraged": "BOOLEAN NOT NULL DEFAULT 0",
            "is_inverse": "BOOLEAN NOT NULL DEFAULT 0",
            "source_status": "VARCHAR(128)",
        },
        "security_sync_runs": {
            "received_count": "INTEGER NOT NULL DEFAULT 0",
            "valid_count": "INTEGER NOT NULL DEFAULT 0",
            "skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "stock_count": "INTEGER NOT NULL DEFAULT 0",
            "etf_count": "INTEGER NOT NULL DEFAULT 0",
            "excluded_security_count": "INTEGER NOT NULL DEFAULT 0",
            "cik_enriched_count": "INTEGER NOT NULL DEFAULT 0",
            "unknown_exchange_count": "INTEGER NOT NULL DEFAULT 0",
            "kospi_stock_count": "INTEGER NOT NULL DEFAULT 0",
            "kosdaq_stock_count": "INTEGER NOT NULL DEFAULT 0",
            "konex_stock_count": "INTEGER NOT NULL DEFAULT 0",
            "kospi_received_count": "INTEGER NOT NULL DEFAULT 0",
            "kosdaq_received_count": "INTEGER NOT NULL DEFAULT 0",
            "konex_received_count": "INTEGER NOT NULL DEFAULT 0",
            "etf_received_count": "INTEGER NOT NULL DEFAULT 0",
            "kospi_valid_count": "INTEGER NOT NULL DEFAULT 0",
            "kosdaq_valid_count": "INTEGER NOT NULL DEFAULT 0",
            "konex_valid_count": "INTEGER NOT NULL DEFAULT 0",
            "etf_valid_count": "INTEGER NOT NULL DEFAULT 0",
            "kospi_skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "kosdaq_skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "konex_skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "etf_skipped_count": "INTEGER NOT NULL DEFAULT 0",
            "recommendation_eligible_count": "INTEGER NOT NULL DEFAULT 0",
            "recommendation_excluded_count": "INTEGER NOT NULL DEFAULT 0",
            "leveraged_etf_count": "INTEGER NOT NULL DEFAULT 0",
            "inverse_etf_count": "INTEGER NOT NULL DEFAULT 0",
            "unknown_type_count": "INTEGER NOT NULL DEFAULT 0",
            "duplicate_code_count": "INTEGER NOT NULL DEFAULT 0",
            "processed_count": "INTEGER NOT NULL DEFAULT 0",
            "total_count": "INTEGER NOT NULL DEFAULT 0",
            "progress_percent": "INTEGER NOT NULL DEFAULT 0",
            "snapshot_date": "VARCHAR(32)",
            "source_file_created_at": "VARCHAR(128)",
            "current_stage": "VARCHAR(64)",
            "skipped_reason_counts": "TEXT",
            "krx_response_diagnostics": "TEXT",
        },
        "security_aliases": {
            "locale": "VARCHAR(16)",
            "confidence": "FLOAT NOT NULL DEFAULT 1",
            "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            "updated_at": "DATETIME",
        },
        "theme_security_candidates": {
            "source_keyword": "VARCHAR(256)",
            "source_type": "VARCHAR(32) NOT NULL DEFAULT 'company_name'",
            "relevance_score": "FLOAT NOT NULL DEFAULT 0",
            "theme_fit_score": "FLOAT NOT NULL DEFAULT 0",
            "evidence_score": "FLOAT NOT NULL DEFAULT 0",
            "liquidity_proxy_score": "FLOAT NOT NULL DEFAULT 0",
            "risk_penalty_score": "FLOAT NOT NULL DEFAULT 0",
            "final_candidate_score": "FLOAT NOT NULL DEFAULT 0",
            "asset_type": "VARCHAR(16)",
            "reason_summary": "TEXT",
            "matched_evidence_json": "JSON NOT NULL DEFAULT '[]'",
            "risk_flags_json": "JSON NOT NULL DEFAULT '[]'",
            "updated_at": "DATETIME",
        },
    }
    for table, specs in column_specs.items():
        existing = {row[1] for row in db.connection().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        for column, ddl in specs.items():
            if column not in existing:
                db.connection().exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    db.commit()


def create_sync_run(db: Session, run: SecuritySyncRun) -> SecuritySyncRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_sync_run(db: Session, run: SecuritySyncRun) -> SecuritySyncRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_security(db: Session, security_id: int) -> Security | None:
    return db.get(Security, security_id)


def get_security_by_key(db: Session, security_key: str) -> Security | None:
    return db.scalar(select(Security).where(Security.security_key == security_key))


def _security_values(item: SecurityIn, ticker: str, now: datetime) -> dict[str, Any]:
    return {
        "country_code": item.country_code,
        "asset_type": item.asset_type,
        "exchange_code": item.exchange_code,
        "exchange_name": item.exchange_name,
        "ticker": ticker,
        "local_code": item.local_code,
        "name": item.name,
        "english_name": item.english_name,
        "normalized_name": normalize_company_name(item.name),
        "currency": item.currency,
        "cik": item.cik,
        "figi": item.figi,
        "isin": item.isin,
        "sector": item.sector,
        "industry": item.industry,
        "issuer_name": item.issuer_name,
        "market_segment": item.market_segment,
        "security_type_detail": item.security_type_detail,
        "is_recommendation_eligible": item.is_recommendation_eligible,
        "is_leveraged": item.is_leveraged,
        "is_inverse": item.is_inverse,
        "source_status": item.source_status,
        "is_active": True,
        "listed_at": item.listed_at,
        "delisted_at": item.delisted_at,
        "source": item.source,
        "source_updated_at": item.source_updated_at,
        "updated_at": now,
    }


def upsert_security(db: Session, item: SecurityIn) -> tuple[Security, bool]:
    ticker = normalize_ticker(item.ticker)
    security_key = generate_security_key(item.country_code, item.exchange_code, ticker)
    existing = get_security_by_key(db, security_key)
    now = datetime.now(timezone.utc)
    created = existing is None
    security = existing or Security(security_key=security_key, created_at=now)

    for field_name, value in _security_values(item, ticker, now).items():
        setattr(security, field_name, value)

    db.add(security)
    db.flush()
    for alias in item.aliases:
        save_alias(
            db,
            security.id,
            alias=str(alias.get("alias") or ""),
            alias_type=str(alias.get("alias_type") or "legal_name"),
            language=alias.get("language"),
            source=item.source,
        )
    db.commit()
    db.refresh(security)
    return security, created


def upsert_securities_batch(db: Session, items: list[SecurityIn]) -> tuple[int, int, set[str]]:
    now = datetime.now(timezone.utc)
    prepared: list[tuple[str, str, SecurityIn]] = []
    seen_keys: set[str] = set()
    for item in items:
        ticker = normalize_ticker(item.ticker)
        key = generate_security_key(item.country_code, item.exchange_code, ticker)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        prepared.append((key, ticker, item))
    existing_rows = {}
    if seen_keys:
        rows = db.scalars(select(Security).where(Security.security_key.in_(seen_keys))).all()
        existing_rows = {row.security_key: row for row in rows}
    created = updated = 0
    for key, ticker, item in prepared:
        security = existing_rows.get(key)
        if security is None:
            security = Security(security_key=key, created_at=now)
            created += 1
        else:
            updated += 1
        for field_name, value in _security_values(item, ticker, now).items():
            setattr(security, field_name, value)
        db.add(security)
        db.flush()
        for alias in item.aliases:
            save_alias(
                db,
                security.id,
                alias=str(alias.get("alias") or ""),
                alias_type=str(alias.get("alias_type") or "legal_name"),
                language=alias.get("language"),
                source=item.source,
            )
    db.commit()
    return created, updated, seen_keys


def save_alias(
    db: Session,
    security_id: int,
    alias: str,
    alias_type: str,
    language: str | None,
    source: str,
    confidence: float = 1.0,
    locale: str | None = None,
) -> SecurityAlias | None:
    normalized = normalize_ticker(alias) if alias_type == "ticker_alias" else normalize_company_name(alias)
    if not alias or not normalized:
        return None
    existing = db.scalar(
        select(SecurityAlias).where(
            SecurityAlias.security_id == security_id,
            SecurityAlias.normalized_alias == normalized,
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.confidence = max(float(existing.confidence or 0.0), confidence)
        existing.locale = existing.locale or locale or language
        existing.updated_at = datetime.now(timezone.utc)
        return existing
    row = SecurityAlias(
        security_id=security_id,
        alias=alias,
        normalized_alias=normalized,
        alias_type=alias_type,
        language=language,
        locale=locale or language,
        source=source,
        confidence=confidence,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def save_alias_with_created(
    db: Session,
    security_id: int,
    alias: str,
    alias_type: str,
    language: str | None,
    source: str,
    confidence: float = 1.0,
    locale: str | None = None,
) -> tuple[SecurityAlias | None, bool]:
    normalized = normalize_ticker(alias) if alias_type == "ticker_alias" else normalize_company_name(alias)
    if not alias or not normalized:
        return None, False
    existing = db.scalar(
        select(SecurityAlias).where(
            SecurityAlias.security_id == security_id,
            SecurityAlias.normalized_alias == normalized,
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.confidence = max(float(existing.confidence or 0.0), confidence)
        existing.locale = existing.locale or locale or language
        existing.updated_at = datetime.now(timezone.utc)
        return existing, False
    row = SecurityAlias(
        security_id=security_id,
        alias=alias,
        normalized_alias=normalized,
        alias_type=alias_type,
        language=language,
        locale=locale or language,
        source=source,
        confidence=confidence,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row, True


def deactivate_missing(db: Session, source: str, seen_keys: set[str], country_code: str | None = None) -> int:
    query = select(Security).where(Security.source == source, Security.is_active == True)
    if country_code:
        query = query.where(Security.country_code == country_code)
    count = 0
    for security in db.scalars(query).all():
        if security.security_key in seen_keys:
            continue
        security.is_active = False
        security.updated_at = datetime.now(timezone.utc)
        db.add(security)
        count += 1
    db.commit()
    return count


def list_securities(
    db: Session,
    country_code: str | None = None,
    asset_type: str | None = None,
    exchange_code: str | None = None,
    ticker: str | None = None,
    keyword: str | None = None,
    is_active: bool | None = True,
    limit: int = 100,
    offset: int = 0,
) -> list[Security]:
    query = select(Security)
    if country_code:
        query = query.where(Security.country_code == country_code)
    if asset_type:
        query = query.where(Security.asset_type == asset_type)
    if exchange_code:
        query = query.where(Security.exchange_code == exchange_code)
    if ticker:
        query = query.where(Security.ticker == normalize_ticker(ticker))
    if keyword:
        normalized = normalize_company_name(keyword)
        query = query.where(
            or_(
                Security.ticker == normalize_ticker(keyword),
                Security.normalized_name.contains(normalized),
                Security.name.contains(keyword),
                Security.english_name.contains(keyword),
            )
        )
    if is_active is not None:
        query = query.where(Security.is_active == is_active)
    return list(db.scalars(query.order_by(Security.country_code, Security.ticker).offset(offset).limit(limit)).all())


def count_securities(db: Session, country_code: str | None = None, asset_type: str | None = None) -> int:
    query = select(func.count(Security.id))
    if country_code:
        query = query.where(Security.country_code == country_code)
    if asset_type:
        query = query.where(Security.asset_type == asset_type)
    return int(db.scalar(query) or 0)


def count_us_recommendation_eligible(db: Session, eligible: bool) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "US",
                Security.is_recommendation_eligible == eligible,
                Security.is_active == True,
            )
        )
        or 0
    )


def count_recommendation_eligible(db: Session, country_code: str, eligible: bool) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == country_code,
                Security.is_recommendation_eligible == eligible,
                Security.is_active == True,
            )
        )
        or 0
    )


def count_kr_etf_flag(db: Session, field_name: str) -> int:
    field = getattr(Security, field_name)
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "KR",
                Security.asset_type == "etf",
                Security.is_active == True,
                field == True,
            )
        )
        or 0
    )


def count_kr_unknown_type(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "KR",
                Security.is_active == True,
                or_(Security.security_type_detail.is_(None), Security.security_type_detail == "other"),
            )
        )
        or 0
    )


def count_us_stock_without_cik(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "US",
                Security.asset_type == "stock",
                Security.is_active == True,
                or_(Security.cik.is_(None), Security.cik == ""),
            )
        )
        or 0
    )


def count_us_stock_with_cik(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "US",
                Security.asset_type == "stock",
                Security.is_active == True,
                Security.cik.is_not(None),
                Security.cik != "",
            )
        )
        or 0
    )


def count_us_stock_with_issuer_name(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.country_code == "US",
                Security.asset_type == "stock",
                Security.is_active == True,
                Security.issuer_name.is_not(None),
                Security.issuer_name != "",
            )
        )
        or 0
    )


def count_sec_aliases_for_us_stocks(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(SecurityAlias.id))
            .join(Security, Security.id == SecurityAlias.security_id)
            .where(
                Security.country_code == "US",
                Security.asset_type == "stock",
                Security.is_active == True,
                SecurityAlias.source == "sec_us",
            )
        )
        or 0
    )


def count_by_exchange(db: Session, country_code: str = "US") -> dict[str, int]:
    rows = db.execute(
        select(Security.exchange_code, func.count(Security.id))
        .where(Security.country_code == country_code, Security.is_active == True)
        .group_by(Security.exchange_code)
    ).all()
    return {str(exchange): int(count) for exchange, count in rows}


def last_sync_at(db: Session) -> datetime | None:
    return db.scalar(select(func.max(SecuritySyncRun.completed_at)).where(SecuritySyncRun.status.in_(["completed", "partial"])))


def last_us_sync_run(db: Session) -> SecuritySyncRun | None:
    return db.scalar(
        select(SecuritySyncRun)
        .where(SecuritySyncRun.country_code == "US", SecuritySyncRun.status.in_(["completed", "partial"]))
        .order_by(desc(SecuritySyncRun.completed_at), desc(SecuritySyncRun.id))
        .limit(1)
    )


def last_kr_sync_run(db: Session) -> SecuritySyncRun | None:
    return db.scalar(
        select(SecuritySyncRun)
        .where(SecuritySyncRun.country_code == "KR", SecuritySyncRun.status.in_(["completed", "partial"]))
        .order_by(desc(SecuritySyncRun.completed_at), desc(SecuritySyncRun.id))
        .limit(1)
    )


def get_sync_run_by_run_id(db: Session, run_id: str) -> SecuritySyncRun | None:
    return db.scalar(select(SecuritySyncRun).where(SecuritySyncRun.run_id == run_id))


def running_sync_run(db: Session, country_code: str) -> SecuritySyncRun | None:
    return db.scalar(
        select(SecuritySyncRun)
        .where(SecuritySyncRun.country_code == country_code, SecuritySyncRun.status == "running")
        .order_by(desc(SecuritySyncRun.created_at), desc(SecuritySyncRun.id))
        .limit(1)
    )


def list_sync_runs(
    db: Session,
    country_code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[SecuritySyncRun]:
    query = select(SecuritySyncRun)
    if country_code:
        query = query.where(SecuritySyncRun.country_code == country_code)
    if provider:
        query = query.where(SecuritySyncRun.provider == provider)
    if status:
        query = query.where(SecuritySyncRun.status == status)
    return list(db.scalars(query.order_by(desc(SecuritySyncRun.created_at), desc(SecuritySyncRun.id)).limit(limit)).all())


def count_existing_by_source(db: Session, source: str, country_code: str) -> int:
    return int(
        db.scalar(
            select(func.count(Security.id)).where(
                Security.source == source,
                Security.country_code == country_code,
                Security.is_active == True,
            )
        )
        or 0
    )


def list_active_us_stocks(db: Session) -> list[Security]:
    return list(
        db.scalars(
            select(Security).where(
                Security.country_code == "US",
                Security.asset_type == "stock",
                Security.is_active == True,
            )
        ).all()
    )


def find_by_ticker(db: Session, ticker: str, country_code: str | None = None, asset_type: str | None = None) -> list[Security]:
    query = select(Security).where(
        Security.ticker == normalize_ticker(ticker),
        Security.is_active == True,
        Security.is_recommendation_eligible == True,
    )
    if country_code:
        query = query.where(Security.country_code == country_code)
    if asset_type:
        query = query.where(Security.asset_type == asset_type)
    return list(db.scalars(query).all())


def find_by_normalized_name(db: Session, normalized_name: str, country_code: str | None = None) -> list[Security]:
    query = select(Security).where(
        Security.normalized_name == normalized_name,
        Security.is_active == True,
        Security.is_recommendation_eligible == True,
    )
    if country_code:
        query = query.where(Security.country_code == country_code)
    return list(db.scalars(query).all())


def find_by_alias(db: Session, normalized_alias: str, country_code: str | None = None) -> list[Security]:
    query = (
        select(Security)
        .join(SecurityAlias, SecurityAlias.security_id == Security.id)
        .where(
            SecurityAlias.normalized_alias == normalized_alias,
            SecurityAlias.is_active == True,
            Security.is_active == True,
            Security.is_recommendation_eligible == True,
        )
    )
    if country_code:
        query = query.where(Security.country_code == country_code)
    return list(db.scalars(query).all())


def find_by_issuer_name(db: Session, issuer_name: str, country_code: str | None = None) -> list[Security]:
    normalized = normalize_company_name(issuer_name)
    query = select(Security).where(
        Security.is_active == True,
        Security.is_recommendation_eligible == True,
        Security.issuer_name.is_not(None),
    )
    if country_code:
        query = query.where(Security.country_code == country_code)
    return [row for row in db.scalars(query.limit(100)).all() if normalize_company_name(row.issuer_name or "") == normalized]


def search_security_names(db: Session, normalized_query: str, country_code: str | None = None, limit: int = 50) -> list[Security]:
    query = select(Security).where(Security.is_active == True, Security.is_recommendation_eligible == True)
    if country_code:
        query = query.where(Security.country_code == country_code)
    if normalized_query:
        query = query.where(Security.normalized_name.contains(normalized_query[:80]))
    return list(db.scalars(query.limit(limit)).all())


def save_theme_candidate(db: Session, candidate: ThemeSecurityCandidate) -> ThemeSecurityCandidate:
    existing = None
    if candidate.security_id is not None:
        existing = db.scalar(
            select(ThemeSecurityCandidate).where(
                ThemeSecurityCandidate.market_theme_id == candidate.market_theme_id,
                ThemeSecurityCandidate.security_id == candidate.security_id,
            )
        )
    if existing is not None:
        for field_name in (
            "source_company_name",
            "source_keyword",
            "source_type",
            "match_score",
            "relevance_score",
            "theme_fit_score",
            "evidence_score",
            "liquidity_proxy_score",
            "risk_penalty_score",
            "final_candidate_score",
            "match_method",
            "match_status",
            "country_code",
            "asset_type",
            "evidence_count",
            "reason_summary",
            "matched_evidence_json",
            "risk_flags_json",
            "updated_at",
        ):
            setattr(existing, field_name, getattr(candidate, field_name))
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def list_theme_candidates(
    db: Session,
    theme_id: int,
    asset_type: str | None = None,
    match_status: str | None = None,
    limit: int = 100,
) -> list[tuple[ThemeSecurityCandidate, Security | None]]:
    query = (
        select(ThemeSecurityCandidate, Security)
        .outerjoin(Security, Security.id == ThemeSecurityCandidate.security_id)
        .where(ThemeSecurityCandidate.market_theme_id == theme_id)
        .order_by(ThemeSecurityCandidate.match_status, ThemeSecurityCandidate.final_candidate_score.desc())
    )
    if asset_type:
        query = query.where(ThemeSecurityCandidate.asset_type == asset_type)
    if match_status:
        query = query.where(ThemeSecurityCandidate.match_status == match_status)
    query = query.limit(limit)
    return list(db.execute(query).all())


def create_theme_candidate_run(db: Session, run: ThemeCandidateRun) -> ThemeCandidateRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_theme_candidate_run(db: Session, run: ThemeCandidateRun) -> ThemeCandidateRun:
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
