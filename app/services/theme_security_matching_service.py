from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.theme_security_candidate import ThemeSecurityCandidate
from app.repositories.security_repository import list_theme_candidates, save_theme_candidate
from app.repositories.theme_analysis_repository import get_market_theme
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log
from app.services.security_match_service import match_security

logger = logging.getLogger(__name__)


def _log_match_error(db: Session, error_type: str, context: dict[str, Any]) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code="SECURITY_MATCH_ERROR",
                severity="ERROR",
                component="security_matcher",
                error_type=error_type,
                message="테마 회사명과 종목 기준정보 매칭 중 오류가 발생했습니다.",
                context_json=context,
            ),
        )
    except Exception as exc:
        logger.warning("failed to write security match error: %s", type(exc).__name__)


def match_theme_securities(db: Session, theme_id: int) -> dict[str, Any]:
    theme = get_market_theme(db, theme_id)
    if theme is None:
        return {"theme_id": theme_id, "matched": 0, "ambiguous": 0, "unmatched": 0, "candidates": []}

    companies = [name for name in (theme.related_companies_json or []) if isinstance(name, str) and name.strip()]
    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for company in companies:
        try:
            candidates = match_security(db, company, limit=3)
            top = candidates[0] if candidates else None
            if top is None or top.ambiguity_status == "unmatched" or top.security_id is None:
                counts["unmatched"] += 1
                save_theme_candidate(
                    db,
                    ThemeSecurityCandidate(
                        market_theme_id=theme.id,
                        security_id=None,
                        source_company_name=company,
                        match_score=top.match_score if top else 0.0,
                        match_method=top.match_method if top else "none",
                        match_status="unmatched",
                        country_code=top.country_code if top else None,
                        evidence_count=theme.evidence_count or 0,
                    ),
                )
                continue
            status = "ambiguous" if top.ambiguity_status == "ambiguous" else "matched"
            counts[status] += 1
            save_theme_candidate(
                db,
                ThemeSecurityCandidate(
                    market_theme_id=theme.id,
                    security_id=top.security_id,
                    source_company_name=company,
                    match_score=top.match_score,
                    match_method=top.match_method,
                    match_status=status,
                    country_code=top.country_code,
                    evidence_count=theme.evidence_count or 0,
                ),
            )
        except Exception as exc:
            counts["unmatched"] += 1
            _log_match_error(db, type(exc).__name__, {"theme_id": theme_id, "company_name_length": len(company)})

    return {
        "theme_id": theme_id,
        **counts,
        "candidates": theme_security_candidates(db, theme_id),
    }


def theme_security_candidates(db: Session, theme_id: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate, security in list_theme_candidates(db, theme_id):
        data = {
            "id": candidate.id,
            "market_theme_id": candidate.market_theme_id,
            "security_id": candidate.security_id,
            "source_company_name": candidate.source_company_name,
            "match_score": candidate.match_score,
            "match_method": candidate.match_method,
            "match_status": candidate.match_status,
            "country_code": candidate.country_code,
            "evidence_count": candidate.evidence_count,
            "created_at": candidate.created_at,
            "security": None,
        }
        if security is not None:
            data["security"] = {
                "security_key": security.security_key,
                "name": security.name,
                "english_name": security.english_name,
                "ticker": security.ticker,
                "exchange_code": security.exchange_code,
                "country_code": security.country_code,
                "asset_type": security.asset_type,
            }
        result.append(data)
    return result
