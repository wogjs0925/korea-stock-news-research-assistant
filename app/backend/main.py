from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.backend.routes import errors_router
from app.backend.routes import news as news_router_module
from app.core.config import get_settings
from app.database.base import Base
from app.database.session import SessionLocal, engine
from app.services.news_scheduler import start_scheduler, stop_scheduler
from app.services.search_term_service import ensure_default_search_terms
import app.backend.routes.news_analysis as news_analysis_router
import app.backend.routes.securities as securities_router
import app.backend.routes.themes as themes_router
import app.backend.routes.developer_settings as developer_settings_router
import app.backend.routes.market_analysis as market_analysis_router
import app.backend.routes.recommendations as recommendations_router

import app.models.app_setting  # noqa: F401
import app.models.error_log  # noqa: F401
import app.models.etf_holding  # noqa: F401
import app.models.market_theme  # noqa: F401
import app.models.recommendation_item  # noqa: F401
import app.models.recommendation_run  # noqa: F401
import app.models.security  # noqa: F401
import app.models.security_alias  # noqa: F401
import app.models.security_sync_run  # noqa: F401
import app.models.theme_analysis_run  # noqa: F401
import app.models.theme_candidate_run  # noqa: F401
import app.models.theme_news_link  # noqa: F401
import app.models.theme_recommendation  # noqa: F401
import app.models.theme_security_candidate  # noqa: F401


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_default_search_terms(db)
    finally:
        db.close()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(errors_router)
app.include_router(news_router_module.router)
app.include_router(news_analysis_router.router)
app.include_router(themes_router.router)
app.include_router(market_analysis_router.router)
app.include_router(recommendations_router.router)
app.include_router(securities_router.router)
app.include_router(developer_settings_router.router)


@app.get("/", response_model=dict)
def read_root() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "status": "running",
    }


@app.get("/health", response_model=dict)
def read_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "environment": settings.app_env,
    }
