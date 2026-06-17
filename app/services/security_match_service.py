from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import get_settings
from app.models.security import Security
from app.repositories.security_repository import (
    find_by_alias,
    find_by_issuer_name,
    find_by_normalized_name,
    find_by_ticker,
    search_security_names,
)
from app.schemas.security import SecurityMatchCandidate
from app.utils.security_names import normalize_company_name, normalize_ticker


def _candidate(security: Security, score: float, method: str, status: str, source_alias: str | None = None) -> SecurityMatchCandidate:
    return SecurityMatchCandidate(
        security_id=security.id,
        matched_security_id=security.id,
        security_key=security.security_key,
        name=security.name,
        ticker=security.ticker,
        exchange_code=security.exchange_code,
        country_code=security.country_code,
        asset_type=security.asset_type,
        match_score=round(score, 4),
        match_method=method,
        match_status=status,
        ambiguity_status=status,
        candidate_security_ids=[security.id],
        source_alias=source_alias,
    )


def _mark_ambiguity(candidates: list[SecurityMatchCandidate]) -> list[SecurityMatchCandidate]:
    settings = get_settings()
    if not candidates:
        return candidates
    candidates.sort(key=lambda item: item.match_score, reverse=True)
    top = candidates[0]
    ambiguous = top.match_score < settings.security_match_min_score
    if len(candidates) > 1 and top.match_score - candidates[1].match_score < settings.security_match_ambiguity_margin:
        ambiguous = True
    countries = {item.country_code for item in candidates if item.match_score == top.match_score}
    if len(countries) > 1:
        ambiguous = True
    status = "ambiguous" if ambiguous else "matched"
    candidate_ids = [int(item.security_id) for item in candidates if item.security_id is not None]
    reason = None
    if ambiguous:
        reason = "score_below_threshold" if top.match_score < settings.security_match_min_score else "multiple_close_matches"
    return [
        item.model_copy(
            update={
                "ambiguity_status": status if index == 0 else "ambiguous",
                "match_status": status if index == 0 else "ambiguous",
                "candidate_security_ids": candidate_ids,
                "ambiguity_reason": reason if index == 0 else "lower_ranked_alternative",
            }
        )
        for index, item in enumerate(candidates)
    ]


def match_security(
    db: Session,
    company_name: str,
    country_code: str | None = None,
    ticker: str | None = None,
    industries: list[str] | None = None,
    asset_type: str | None = None,
    limit: int = 5,
) -> list[SecurityMatchCandidate]:
    del industries
    if ticker:
        ticker_matches = find_by_ticker(db, normalize_ticker(ticker), country_code=country_code, asset_type=asset_type)
        if ticker_matches:
            return _mark_ambiguity([_candidate(item, 1.0, "ticker_exact", "matched") for item in ticker_matches[:limit]])

    normalized = normalize_company_name(company_name)
    if not normalized:
        return [
            SecurityMatchCandidate(
                name=company_name,
                match_score=0.0,
                match_method="empty_query",
                match_status="unmatched",
                ambiguity_status="unmatched",
            )
        ]

    exact = find_by_normalized_name(db, normalized, country_code=country_code)
    if exact:
        return _mark_ambiguity([_candidate(item, 0.98, "normalized_name_exact", "matched") for item in exact[:limit]])

    alias = find_by_alias(db, normalized, country_code=country_code)
    if alias:
        return _mark_ambiguity([_candidate(item, 0.95, "alias_exact", "matched", source_alias=company_name) for item in alias[:limit]])

    issuer = find_by_issuer_name(db, company_name, country_code=country_code)
    if issuer:
        return _mark_ambiguity([_candidate(item, 0.92, "sec_issuer_name_exact", "matched") for item in issuer[:limit]])

    english_exact_query = select(Security).where(
        Security.is_active == True,
        Security.is_recommendation_eligible == True,
        Security.english_name.is_not(None),
    )
    if country_code:
        english_exact_query = english_exact_query.where(Security.country_code == country_code)
    if asset_type:
        english_exact_query = english_exact_query.where(Security.asset_type == asset_type)
    english_exact = [
        row
        for row in db.scalars(english_exact_query.limit(200)).all()
        if normalize_company_name(row.english_name or "") == normalized
    ]
    if english_exact:
        return _mark_ambiguity([_candidate(item, 0.91, "english_name_exact", "matched") for item in english_exact[:limit]])

    fuzzy: list[SecurityMatchCandidate] = []
    for security in search_security_names(db, normalized, country_code=country_code, limit=50):
        score = SequenceMatcher(None, normalized, security.normalized_name).ratio()
        if normalized in security.normalized_name or security.normalized_name in normalized:
            score = max(score, 0.80)
        fuzzy.append(_candidate(security, score, "limited_fuzzy", "matched"))
    fuzzy.sort(key=lambda item: item.match_score, reverse=True)
    if not fuzzy or fuzzy[0].match_score < get_settings().security_match_min_score:
        return [
            SecurityMatchCandidate(
                name=company_name,
                match_score=fuzzy[0].match_score if fuzzy else 0.0,
                match_method="limited_fuzzy",
                match_status="unmatched",
                ambiguity_status="unmatched",
                candidate_security_ids=[int(item.security_id) for item in fuzzy[:limit] if item.security_id is not None],
                ambiguity_reason="score_below_threshold",
            )
        ]
    return _mark_ambiguity(fuzzy[:limit])


def match_security_dict(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return [item.model_dump() for item in match_security(*args, **kwargs)]
