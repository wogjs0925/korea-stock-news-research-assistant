from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.market_analysis import MarketAnalysisRunRequest, MarketAnalysisRunResponse
from app.services.market_analysis_pipeline_service import run_market_analysis_pipeline

router = APIRouter(prefix="/market-analysis", tags=["Market Analysis"])


@router.post("/run", response_model=MarketAnalysisRunResponse)
def run_market_analysis(payload: MarketAnalysisRunRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or MarketAnalysisRunRequest()
    return run_market_analysis_pipeline(
        db,
        analysis_window_hours=payload.analysis_window_hours,
        max_news_analysis_count=payload.max_news_analysis_count,
        max_theme_source_count=payload.max_theme_source_count,
        force_reanalyze=payload.force_reanalyze,
        run_candidate_generation=payload.run_candidate_generation,
        include_weak_industry_candidates=payload.include_weak_industry_candidates,
        include_watchlist_themes=payload.include_watchlist_themes,
        include_leveraged_inverse_etfs=payload.include_leveraged_inverse_etfs,
        max_stock_candidates_per_theme=payload.max_stock_candidates_per_theme,
        max_etf_candidates_per_theme=payload.max_etf_candidates_per_theme,
        run_recommendations=payload.run_recommendations,
        max_stocks_per_theme=payload.max_stocks_per_theme,
        max_etfs_per_theme=payload.max_etfs_per_theme,
        diversify_country=payload.diversify_country,
    )
