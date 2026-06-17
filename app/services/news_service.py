import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.repositories import news_repository
from app.utils.text import normalize_news_title, build_news_hash, strip_html
from app.models.news_article import NewsArticle
from app.models.news_collection_run import NewsCollectionRun
from app.providers.news.mock import MockNewsProvider
from app.providers.news.naver import NaverNewsProvider
from app.repositories.news_repository import (
    create_article,
    get_article_by_hash,
    create_collection_run,
    update_collection_run,
    find_duplicate_candidate,
)
from app.services.error_service import create_error_log
from app.schemas.news import ProviderNewsItem, NewsCollectionRequest
from app.utils.news_quality import (
    canonicalize_url,
    content_fingerprint,
    duplicate_group_id,
    market_relevance,
    normalize_title_for_dedupe,
)


settings = get_settings()


async def _call_provider(provider_name: str, query: str, display: int, sort: str):
    if provider_name == "mock":
        provider = MockNewsProvider()
        return await provider.search(query, display, sort)
    if provider_name == "naver":
        provider = NaverNewsProvider()
        try:
            return await provider.search(query, display, sort)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
    raise ValueError("unsupported provider")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _unique_duplicate_hash(base_hash: str, link: str, external_id: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(f"{base_hash}|{link}|{external_id or ''}|duplicate|{uuid.uuid4().hex}".encode("utf-8"))
    return digest.hexdigest()


def collect_news(db: Any, request: NewsCollectionRequest) -> dict[str, Any]:
    provider = request.provider or settings.news_provider
    run_id = f"NEWS-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run = NewsCollectionRun(
        run_id=run_id,
        provider=provider,
        query=request.query,
        requested_count=request.display,
    )
    run = create_collection_run(db, run)

    start = time.time()
    received = 0
    saved = 0
    dup = 0
    failed = 0

    try:
        items = asyncio.run(_call_provider(provider, request.query, request.display, request.sort))
        received = len(items)
    except Exception as exc:  # provider-level failure
        run.status = "failed"
        run.error_message = str(exc)
        run.completed_at = _now_utc()
        run.duration_ms = int((time.time() - start) * 1000)
        run.requested_count = request.display
        run.received_count = 0
        update_collection_run(db, run)
        # log to Error Center
        try:
            from app.schemas.error import ErrorLogCreate

            create_error_log(
                db,
                ErrorLogCreate(
                    error_code="NEWS_PROVIDER_ERROR",
                    severity="ERROR",
                    component="news_provider",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    context_json={"provider": provider, "query": request.query, "run_id": run_id},
                ),
            )
        except Exception:
            pass
        raise

    # process items
    for item in items:
        try:
            title = strip_html(item.get("title", ""))
            desc = strip_html(item.get("description", "")) if item.get("description") else None
            link = item.get("link") or ""
            original = item.get("original_link")
            publisher = item.get("publisher")
            # infer publisher when provider doesn't supply one
            if not publisher:
                try:
                    from app.utils.publisher import infer_publisher

                    inferred = infer_publisher(original, link)
                    if inferred:
                        publisher = inferred
                except Exception:
                    publisher = publisher
            published_at = None
            try:
                if item.get("published_at"):
                    published_at = datetime.fromisoformat(item.get("published_at"))
            except Exception:
                published_at = None

            title_norm = normalize_news_title(title)
            content_hash = build_news_hash(title, original, link)
            canonical_url = canonicalize_url(original or link)
            normalized_title = normalize_title_for_dedupe(title)
            fingerprint = content_fingerprint(title, desc)
            group_id = duplicate_group_id(fingerprint)
            relevance_score, is_market_relevant, is_analysis_candidate, _relevance_reason = market_relevance(
                title,
                desc,
                request.query,
            )

            existing = get_article_by_hash(db, content_hash)
            duplicate_source = existing
            duplicate_reason = "content_hash" if existing else None
            if duplicate_source is None:
                duplicate_source, duplicate_reason = find_duplicate_candidate(
                    db,
                    canonical_url=canonical_url,
                    normalized_title=normalized_title,
                    content_fingerprint=fingerprint,
                    publisher=publisher,
                    published_at=published_at,
                )
            is_duplicate = duplicate_source is not None
            if is_duplicate:
                dup += 1
                if existing is not None:
                    content_hash = _unique_duplicate_hash(content_hash, link, item.get("id"))

            article = NewsArticle(
                provider=provider,
                external_id=item.get("id"),
                query=request.query,
                title=title,
                description=desc,
                link=link,
                original_link=original,
                publisher=publisher,
                published_at=published_at,
                collected_at=_now_utc(),
                available_at=_now_utc(),
                title_normalized=title_norm,
                content_hash=content_hash,
                canonical_url=canonical_url,
                normalized_title=normalized_title,
                content_fingerprint=fingerprint,
                duplicate_group_id=getattr(duplicate_source, "duplicate_group_id", None) or group_id,
                duplicate_of_id=getattr(duplicate_source, "id", None),
                duplicate_of_article_id=getattr(duplicate_source, "id", None),
                duplicate_reason=duplicate_reason if is_duplicate else None,
                is_duplicate=is_duplicate,
                market_relevance_score=relevance_score,
                is_market_relevant=is_market_relevant,
                is_analysis_candidate=is_analysis_candidate and not is_duplicate,
                is_active=True,
                raw_data={"provider": provider},
            )
            create_article(db, article)
            saved += 1
        except Exception as exc:
            failed += 1
            # record error to Error Center
            try:
                from app.schemas.error import ErrorLogCreate

                create_error_log(
                    db,
                    ErrorLogCreate(
                        error_code="NEWS_STORAGE_ERROR",
                        severity="ERROR",
                        component="news_collector",
                        error_type=type(exc).__name__,
                        message=str(exc),
                        context_json={
                            "provider": provider,
                            "query": request.query,
                            "run_id": run_id,
                        },
                    ),
                )
            except Exception:
                pass

    run.requested_count = request.display
    run.received_count = received
    run.saved_count = saved
    run.duplicate_count = dup
    run.failed_count = failed
    run.status = "completed" if failed == 0 else "partial"
    run.completed_at = _now_utc()
    run.duration_ms = int((time.time() - start) * 1000)
    update_collection_run(db, run)

    return {
        "run_id": run.run_id,
        "provider": run.provider,
        "query": run.query,
        "requested_count": run.requested_count,
        "received_count": run.received_count,
        "saved_count": run.saved_count,
        "duplicate_count": run.duplicate_count,
        "failed_count": run.failed_count,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_ms": run.duration_ms,
    }
