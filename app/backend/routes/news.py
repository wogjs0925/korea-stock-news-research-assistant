from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import get_db
from app.providers.news.naver import NewsProviderError
from app.schemas.news import (
    NewsCollectionRequest,
    NewsCollectionResult,
    NewsArticleRead,
    NewsSummary,
    NewsCollectionRunRead,
    NewsDedupeRunResult,
    SearchTermCreate,
    SearchTermUpdate,
    SearchTermRead,
    SearchTermSchedulerStatus,
    CollectionStatus,
)
from app.services.news_service import collect_news
from app.services.news_quality_service import run_news_dedupe
from app.services.news_scheduler import get_collection_status, run_scheduled_search_terms
from app.utils.publisher import infer_publisher
from app.services.search_term_service import (
    create_search_term,
    delete_search_term,
    list_search_terms as list_search_terms_service,
    update_search_term,
)
from app.repositories.search_term_repository import get_search_term_by_id
from app.repositories.news_repository import (
    list_articles,
    news_summary,
    list_collection_runs,
    get_collection_run_by_run_id,
    get_article,
)

settings = get_settings()

router = APIRouter(prefix="/news", tags=["News"])


@router.post("/collect", response_model=NewsCollectionResult, status_code=status.HTTP_201_CREATED)
def collect(request: NewsCollectionRequest, db: Session = Depends(get_db)) -> dict:
    try:
        result = collect_news(db, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except (RuntimeError, NewsProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")
    return result


@router.post("/demo", response_model=NewsCollectionResult, status_code=status.HTTP_201_CREATED)
def demo(db: Session = Depends(get_db)) -> dict:
    # development only
    from app.core.config import get_settings

    settings = get_settings()
    if settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="demo only in development")
    request = NewsCollectionRequest(query="AI 반도체", display=3, sort="date", provider="mock")
    return collect_news(db, request)


@router.get("/summary", response_model=NewsSummary)
def summary(db: Session = Depends(get_db)) -> dict:
    return news_summary(db)


@router.get("/collection-status", response_model=CollectionStatus)
def collection_status(db: Session = Depends(get_db)) -> CollectionStatus:
    return get_collection_status(db)


@router.post("/collect-all", response_model=dict)
def collect_all(db: Session = Depends(get_db)) -> dict:
    return run_scheduled_search_terms()


@router.post("/backfill-publishers", response_model=dict)
def backfill_publishers(db: Session = Depends(get_db)) -> dict:
    from app.models.news_article import NewsArticle
    from app.repositories.news_repository import update_article

    settings = get_settings()
    if settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="backfill allowed in development only")

    q = select(NewsArticle).where((NewsArticle.publisher == None) | (NewsArticle.publisher == ""))
    rows = db.scalars(q).all()
    checked = len(rows)
    updated = 0
    for a in rows:
        inferred = infer_publisher(a.original_link, a.link)
        if inferred and (a.publisher is None or a.publisher == ""):
            a.publisher = inferred
            update_article(db, a)
            updated += 1

    return {"checked": checked, "updated": updated}


@router.post("/dedupe/run", response_model=NewsDedupeRunResult)
def run_dedupe_endpoint(db: Session = Depends(get_db)) -> dict[str, int]:
    return run_news_dedupe(db)


@router.get("/", response_model=list[NewsArticleRead])
def list_endpoint(
    db: Session = Depends(get_db),
    query: str | None = None,
    provider: str | None = None,
    publisher: str | None = None,
    is_duplicate: bool | None = None,
    is_market_relevant: bool | None = None,
    is_analysis_candidate: bool | None = None,
    limit: int = 100,
    offset: int = 0,
):
    items = list_articles(
        db,
        query=query,
        provider=provider,
        publisher=publisher,
        is_duplicate=is_duplicate,
        is_market_relevant=is_market_relevant,
        is_analysis_candidate=is_analysis_candidate,
        limit=limit,
        offset=offset,
    )
    return items


@router.get("/runs", response_model=list[NewsCollectionRunRead])
def runs(db: Session = Depends(get_db), provider: str | None = None, status: str | None = None, limit: int = 100):
    return list_collection_runs(db, provider=provider, status=status, limit=limit)


@router.get("/runs/{run_id}", response_model=NewsCollectionRunRead)
def run_detail(run_id: str, db: Session = Depends(get_db)):
    run = get_collection_run_by_run_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.get("/search-terms/status", response_model=SearchTermSchedulerStatus)
def search_term_scheduler_status() -> SearchTermSchedulerStatus:
    return {
        "enabled": settings.news_scheduler_enabled,
        "interval_minutes": settings.news_scheduler_interval_minutes,
    }


@router.post("/search-terms", response_model=SearchTermRead, status_code=status.HTTP_201_CREATED)
def create_search_term_endpoint(payload: SearchTermCreate, db: Session = Depends(get_db)) -> SearchTermRead:
    try:
        return create_search_term(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/search-terms", response_model=list[SearchTermRead])
def list_search_terms_endpoint(
    db: Session = Depends(get_db),
    active_only: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SearchTermRead]:
    return list_search_terms_service(db, active_only=active_only, limit=limit, offset=offset)


@router.get("/search-terms/{term_id}", response_model=SearchTermRead)
def get_search_term_endpoint(term_id: int, db: Session = Depends(get_db)) -> SearchTermRead:
    term = get_search_term_by_id(db, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="search term not found")
    return term


@router.patch("/search-terms/{term_id}", response_model=SearchTermRead)
def patch_search_term_endpoint(term_id: int, payload: SearchTermUpdate, db: Session = Depends(get_db)) -> SearchTermRead:
    try:
        return update_search_term(db, term_id, payload)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="search term not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/search-terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_search_term_endpoint(term_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        delete_search_term(db, term_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="search term not found")


@router.get("/{news_id}", response_model=NewsArticleRead)
def article_detail(news_id: int, db: Session = Depends(get_db)):
    article = get_article(db, news_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article not found")
    return article
