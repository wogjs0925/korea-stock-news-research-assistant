from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SecurityIn(BaseModel):
    country_code: str
    asset_type: str
    exchange_code: str
    exchange_name: str
    ticker: str
    local_code: str | None = None
    name: str
    english_name: str | None = None
    currency: str
    cik: str | None = None
    figi: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    issuer_name: str | None = None
    market_segment: str | None = None
    security_type_detail: str | None = None
    is_recommendation_eligible: bool = True
    is_leveraged: bool = False
    is_inverse: bool = False
    source_status: str | None = None
    listed_at: date | None = None
    delisted_at: date | None = None
    source: str
    source_updated_at: datetime | None = None
    aliases: list[dict[str, str | None]] = Field(default_factory=list)


class SecurityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    security_key: str
    country_code: str
    asset_type: str
    exchange_code: str
    exchange_name: str
    ticker: str
    local_code: str | None = None
    name: str
    english_name: str | None = None
    normalized_name: str
    currency: str
    cik: str | None = None
    figi: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    issuer_name: str | None = None
    market_segment: str | None = None
    security_type_detail: str | None = None
    is_recommendation_eligible: bool = True
    is_leveraged: bool = False
    is_inverse: bool = False
    source_status: str | None = None
    is_active: bool
    listed_at: date | None = None
    delisted_at: date | None = None
    source: str
    source_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SecurityMatchCandidate(BaseModel):
    security_id: int | None = None
    matched_security_id: int | None = None
    security_key: str | None = None
    name: str
    ticker: str | None = None
    exchange_code: str | None = None
    country_code: str | None = None
    asset_type: str | None = None
    match_score: float
    match_method: str
    match_status: str | None = None
    ambiguity_status: str
    candidate_security_ids: list[int] = Field(default_factory=list)
    ambiguity_reason: str | None = None
    source_alias: str | None = None


class SecuritySyncResponse(BaseModel):
    run_id: str
    country_code: str
    provider: str
    requested_count: int
    received_count: int = 0
    valid_count: int = 0
    created_count: int
    updated_count: int
    skipped_count: int = 0
    deactivated_count: int
    failed_count: int
    stock_count: int = 0
    etf_count: int = 0
    excluded_security_count: int = 0
    cik_enriched_count: int = 0
    unknown_exchange_count: int = 0
    kospi_stock_count: int = 0
    kosdaq_stock_count: int = 0
    konex_stock_count: int = 0
    kospi_received_count: int = 0
    kosdaq_received_count: int = 0
    konex_received_count: int = 0
    etf_received_count: int = 0
    kospi_valid_count: int = 0
    kosdaq_valid_count: int = 0
    konex_valid_count: int = 0
    etf_valid_count: int = 0
    kospi_skipped_count: int = 0
    kosdaq_skipped_count: int = 0
    konex_skipped_count: int = 0
    etf_skipped_count: int = 0
    recommendation_eligible_count: int = 0
    recommendation_excluded_count: int = 0
    leveraged_etf_count: int = 0
    inverse_etf_count: int = 0
    unknown_type_count: int = 0
    duplicate_code_count: int = 0
    processed_count: int = 0
    total_count: int = 0
    progress_percent: int = 0
    snapshot_date: str | None = None
    status: str
    current_stage: str | None = None
    duration_ms: int | None = None
    source_file_created_at: str | None = None
    error_message: str | None = None
    skipped_reason_counts: str | None = None
    krx_response_diagnostics: str | None = None
    configuration_missing: list[str] | None = None


class SecuritySummaryResponse(BaseModel):
    total: int
    kr_stock: int
    kr_etf: int
    us_stock: int
    us_etf: int
    last_sync_at: datetime | None = None


class ThemeSecurityCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market_theme_id: int
    security_id: int | None
    source_company_name: str
    match_score: float
    match_method: str
    match_status: str
    country_code: str | None
    evidence_count: int
    created_at: datetime
    security: dict[str, Any] | None = None
