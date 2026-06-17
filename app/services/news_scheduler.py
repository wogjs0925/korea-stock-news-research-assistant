import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.repositories.news_repository import news_summary
from app.repositories.search_term_repository import list_active_search_terms, list_scheduled_search_terms
from app.services.news_service import collect_news
from app.schemas.news import NewsCollectionRequest


logger = logging.getLogger(__name__)
settings = get_settings()
_scheduler: Optional[BackgroundScheduler] = None
_last_run_at: datetime | None = None


def _run_collection_for_term(db: Session, term) -> bool:
    request = NewsCollectionRequest(
        query=term.query,
        display=term.display,
        sort=term.sort,
        provider=term.provider,
    )
    try:
        collect_news(db, request)
        return True
    except Exception as exc:
        logger.exception("Scheduled collection failed for term %s: %s", term.query, exc)
        return False


def run_scheduled_search_terms() -> dict[str, int | None]:
    global _last_run_at
    db = SessionLocal()
    try:
        _last_run_at = datetime.now(timezone.utc)
        search_terms = list_scheduled_search_terms(db)
        if not search_terms:
            logger.debug("No active scheduled news search terms found for scheduled run.")
            return {
                "active_terms": 0,
                "succeeded": 0,
                "failed": 0,
            }

        logger.info("Running scheduled news collection for %d scheduled search terms.", len(search_terms))
        succeeded = 0
        failed = 0
        for term in search_terms:
            if _run_collection_for_term(db, term):
                succeeded += 1
            else:
                failed += 1
        return {
            "active_terms": len(search_terms),
            "succeeded": succeeded,
            "failed": failed,
        }
    finally:
        db.close()


def get_collection_status(db: Session) -> dict[str, int | bool | None]:
    job = _scheduler.get_job("news_search_terms_job") if _scheduler else None
    summary = news_summary(db)
    active_terms = len(list_scheduled_search_terms(db))
    return {
        "enabled": settings.news_scheduler_enabled,
        "interval_minutes": settings.news_scheduler_interval_minutes,
        "last_run_at": _last_run_at,
        "next_run_at": job.next_run_time if job is not None else None,
        "active_profile_count": active_terms,
        "total_articles": int(summary.get("total_articles", 0)),
        "articles_last_24h": int(summary.get("articles_last_24h", 0)),
        "duplicate_articles": int(summary.get("duplicate_articles", 0)),
        "total_collection_runs": int(summary.get("total_collection_runs", 0)),
        "failed_collection_runs": int(summary.get("failed_collection_runs", 0)),
    }


def start_scheduler() -> None:
    global _scheduler
    if not settings.news_scheduler_enabled:
        logger.info("News scheduler is disabled by configuration.")
        return

    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")

    if _scheduler.running:
        logger.info("News scheduler already running.")
        return

    _scheduler.add_job(
        run_scheduled_search_terms,
        trigger=IntervalTrigger(minutes=settings.news_scheduler_interval_minutes),
        id="news_search_terms_job",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("News scheduler started with interval %d minutes.", settings.news_scheduler_interval_minutes)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("News scheduler stopped.")
