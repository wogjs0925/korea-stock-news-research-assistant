from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.news_article import NewsArticle
from app.repositories.news_repository import ensure_news_schema
from app.utils.news_quality import (
    ANALYSIS_CANDIDATE_THRESHOLD,
    canonicalize_url,
    content_fingerprint,
    duplicate_group_id,
    market_relevance,
    normalize_title_for_dedupe,
    title_similarity,
)


@dataclass(frozen=True)
class DuplicateMatch:
    article: NewsArticle
    reason: str


def _find_existing_duplicate(
    *,
    article_id: int,
    canonical_url: str,
    normalized_title: str,
    fingerprint: str,
    publisher: str | None,
    seen_by_url: dict[str, NewsArticle],
    seen_by_fingerprint: dict[str, NewsArticle],
    seen_by_title: dict[tuple[str | None, str], NewsArticle],
    recent_by_publisher: dict[str | None, list[NewsArticle]],
) -> DuplicateMatch | None:
    if canonical_url and canonical_url in seen_by_url:
        return DuplicateMatch(seen_by_url[canonical_url], "canonical_url")
    if fingerprint and fingerprint in seen_by_fingerprint:
        return DuplicateMatch(seen_by_fingerprint[fingerprint], "content_fingerprint")
    title_key = (publisher, normalized_title)
    if normalized_title and title_key in seen_by_title:
        return DuplicateMatch(seen_by_title[title_key], "normalized_title")

    if normalized_title:
        for candidate in recent_by_publisher.get(publisher, [])[-50:]:
            if candidate.id == article_id:
                continue
            candidate_title = candidate.normalized_title or normalize_title_for_dedupe(candidate.title)
            if title_similarity(normalized_title, candidate_title) >= 0.86:
                return DuplicateMatch(candidate, "similar_title_same_publisher")
    return None


def run_news_dedupe(db: Session) -> dict[str, int]:
    ensure_news_schema(db)
    rows = list(db.scalars(select(NewsArticle).order_by(NewsArticle.published_at.asc(), NewsArticle.id.asc())).all())
    seen_by_url: dict[str, NewsArticle] = {}
    seen_by_fingerprint: dict[str, NewsArticle] = {}
    seen_by_title: dict[tuple[str | None, str], NewsArticle] = {}
    recent_by_publisher: dict[str | None, list[NewsArticle]] = {}

    duplicate_count = 0
    noise_count = 0
    analysis_candidate_count = 0
    updated_count = 0

    for article in rows:
        canonical_url = canonicalize_url(article.original_link or article.link)
        normalized_title = normalize_title_for_dedupe(article.title)
        fingerprint = content_fingerprint(article.title, article.description)
        group_id = duplicate_group_id(fingerprint)
        relevance_score, is_market_relevant, is_analysis_candidate, _reason = market_relevance(
            article.title,
            article.description,
            article.query,
        )

        match = _find_existing_duplicate(
            article_id=article.id,
            canonical_url=canonical_url,
            normalized_title=normalized_title,
            fingerprint=fingerprint,
            publisher=article.publisher,
            seen_by_url=seen_by_url,
            seen_by_fingerprint=seen_by_fingerprint,
            seen_by_title=seen_by_title,
            recent_by_publisher=recent_by_publisher,
        )
        duplicate_source = match.article if match else None
        duplicate_reason = match.reason if match else None
        final_is_duplicate = duplicate_source is not None
        final_is_candidate = is_analysis_candidate and not final_is_duplicate

        before = (
            article.canonical_url,
            article.normalized_title,
            article.content_fingerprint,
            article.duplicate_group_id,
            article.duplicate_of_id,
            article.duplicate_of_article_id,
            article.duplicate_reason,
            article.is_duplicate,
            article.market_relevance_score,
            article.is_market_relevant,
            article.is_analysis_candidate,
        )

        article.canonical_url = canonical_url
        article.normalized_title = normalized_title
        article.content_fingerprint = fingerprint
        article.duplicate_group_id = getattr(duplicate_source, "duplicate_group_id", None) or group_id
        article.duplicate_of_id = getattr(duplicate_source, "id", None)
        article.duplicate_of_article_id = getattr(duplicate_source, "id", None)
        article.duplicate_reason = duplicate_reason
        article.is_duplicate = final_is_duplicate
        article.market_relevance_score = relevance_score
        article.is_market_relevant = is_market_relevant
        article.is_analysis_candidate = final_is_candidate
        article.updated_at = datetime.now(timezone.utc)

        after = (
            article.canonical_url,
            article.normalized_title,
            article.content_fingerprint,
            article.duplicate_group_id,
            article.duplicate_of_id,
            article.duplicate_of_article_id,
            article.duplicate_reason,
            article.is_duplicate,
            article.market_relevance_score,
            article.is_market_relevant,
            article.is_analysis_candidate,
        )
        if before != after:
            updated_count += 1

        if final_is_duplicate:
            duplicate_count += 1
        if not final_is_candidate or relevance_score < ANALYSIS_CANDIDATE_THRESHOLD:
            noise_count += 1
        if final_is_candidate:
            analysis_candidate_count += 1

        if not final_is_duplicate:
            if canonical_url:
                seen_by_url.setdefault(canonical_url, article)
            if fingerprint:
                seen_by_fingerprint.setdefault(fingerprint, article)
            if normalized_title:
                seen_by_title.setdefault((article.publisher, normalized_title), article)
            recent_by_publisher.setdefault(article.publisher, []).append(article)

    db.commit()
    return {
        "scanned_count": len(rows),
        "duplicate_count": duplicate_count,
        "noise_count": noise_count,
        "analysis_candidate_count": analysis_candidate_count,
        "updated_count": updated_count,
    }
