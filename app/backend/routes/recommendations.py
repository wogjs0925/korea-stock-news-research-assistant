from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.recommendation import RecommendationDetailResponse, RecommendationRunRequest, RecommendationRunResponse
from app.services.recommendation_service import (
    latest_recommendations,
    recommendation_run_detail,
    recommendation_runs,
    run_recommendations,
    theme_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])


@router.post("/run", response_model=RecommendationRunResponse)
def run_recommendation_engine(payload: RecommendationRunRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or RecommendationRunRequest()
    return run_recommendations(
        db,
        theme_run_id=payload.theme_run_id,
        max_stocks_per_theme=payload.max_stocks_per_theme,
        max_etfs_per_theme=payload.max_etfs_per_theme,
        include_leveraged_inverse_etfs=payload.include_leveraged_inverse_etfs,
        min_candidate_score=payload.min_candidate_score,
        min_evidence_score=payload.min_evidence_score,
        diversify_country=payload.diversify_country,
        stock_country_scope=payload.stock_country_scope,
    )


@router.get("/latest", response_model=RecommendationDetailResponse)
def latest(db: Session = Depends(get_db)):
    return latest_recommendations(db)


@router.get("/runs")
def runs(db: Session = Depends(get_db), limit: int = 100, offset: int = 0):
    return recommendation_runs(db, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RecommendationDetailResponse)
def run_detail(run_id: str, db: Session = Depends(get_db)):
    result = recommendation_run_detail(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recommendation run not found")
    return result


@router.get("/themes/{theme_id}", response_model=RecommendationDetailResponse)
def theme_detail(theme_id: int, db: Session = Depends(get_db)):
    result = theme_recommendations(db, theme_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme recommendations not found")
    return result
