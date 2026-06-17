from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.repositories.theme_analysis_repository import (
    get_latest_theme_run,
    get_market_theme,
    get_theme_run_by_run_id,
    list_market_themes,
    list_theme_evidence,
    list_theme_runs,
    list_themes_for_run,
)
from app.schemas.theme_analysis import (
    LatestThemeCandidatesResponse,
    LatestThemesResponse,
    MarketThemeDetail,
    ThemeCandidateRunRequest,
    ThemeCandidateRunResponse,
    ThemeRunRequest,
    ThemeRunResponse,
    ThemeSecurityCandidateRead,
)
from app.services.theme_analysis_service import backfill_theme_tags, run_theme_analysis, test_theme_openai
from app.services.theme_security_matching_service import match_theme_securities, theme_security_candidates
from app.services.theme_candidate_service import (
    generate_theme_candidates,
    latest_theme_candidates_grouped,
    theme_candidates_for_api,
)
from app.services.recommendation_service import theme_recommendations

router = APIRouter(prefix="/themes", tags=["Market Themes"])


def _theme_detail(db: Session, theme) -> MarketThemeDetail:
    data = MarketThemeDetail.model_validate(theme)
    data.evidence = list_theme_evidence(db, theme.id)
    return data


@router.post("/run", response_model=ThemeRunResponse)
def run_themes(payload: ThemeRunRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or ThemeRunRequest()
    settings = get_settings()
    if payload.provider == "mock" and settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mock provider is development only")
    if payload.provider not in ("openai", "mock"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid provider")
    try:
        return run_theme_analysis(
            db,
            window_hours=payload.window_hours,
            max_sources=payload.max_sources,
            provider=payload.provider,
            force=payload.force,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="테마 분석 실행 중 내부 오류가 발생했습니다.",
        ) from None


@router.get("/latest", response_model=LatestThemesResponse)
def latest_themes(db: Session = Depends(get_db)):
    run = get_latest_theme_run(db)
    if run is None:
        return {"run": None, "themes": []}
    themes = [_theme_detail(db, theme) for theme in list_themes_for_run(db, run.id)]
    return {"run": run, "themes": themes}


@router.post("/test-openai")
def test_theme_openai_endpoint(db: Session = Depends(get_db), max_sources: int = 5, dry_run: bool = True):
    return test_theme_openai(db, max_sources=max_sources, dry_run=dry_run)


@router.post("/tags/backfill")
def backfill_theme_tags_endpoint(db: Session = Depends(get_db)):
    return backfill_theme_tags(db)


@router.get("/runs")
def runs(db: Session = Depends(get_db), limit: int = 100, offset: int = 0):
    return list_theme_runs(db, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
def run_detail(run_id: str, db: Session = Depends(get_db)):
    run = get_theme_run_by_run_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme run not found")
    return {"run": run, "themes": [_theme_detail(db, theme) for theme in list_themes_for_run(db, run.id)]}


@router.get("")
def themes(
    db: Session = Depends(get_db),
    run_id: str | None = None,
    impact_direction: str | None = None,
    minimum_score: float | None = None,
    limit: int = 100,
    offset: int = 0,
):
    return list_market_themes(
        db,
        run_id=run_id,
        impact_direction=impact_direction,
        minimum_score=minimum_score,
        limit=limit,
        offset=offset,
    )


@router.get("/{theme_id}", response_model=MarketThemeDetail)
def theme_detail(theme_id: int, db: Session = Depends(get_db)):
    theme = get_market_theme(db, theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    return _theme_detail(db, theme)


@router.post("/candidates/run", response_model=ThemeCandidateRunResponse)
def run_latest_theme_candidates(payload: ThemeCandidateRunRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or ThemeCandidateRunRequest()
    return generate_theme_candidates(
        db,
        theme_run_id=payload.theme_run_id,
        include_weak_industry_candidates=payload.include_weak_industry_candidates,
        include_watchlist_themes=payload.include_watchlist_themes,
        include_leveraged_inverse_etfs=payload.include_leveraged_inverse_etfs,
        max_stock_candidates_per_theme=payload.max_stock_candidates_per_theme,
        max_etf_candidates_per_theme=payload.max_etf_candidates_per_theme,
    )


@router.get("/latest/candidates", response_model=list[LatestThemeCandidatesResponse])
def latest_theme_candidates(db: Session = Depends(get_db), limit: int = 100):
    return latest_theme_candidates_grouped(db, limit=limit)


@router.post("/{theme_id}/candidates/run", response_model=ThemeCandidateRunResponse)
def run_theme_candidates(
    theme_id: int,
    payload: ThemeCandidateRunRequest | None = None,
    db: Session = Depends(get_db),
):
    if get_market_theme(db, theme_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    payload = payload or ThemeCandidateRunRequest()
    return generate_theme_candidates(
        db,
        theme_id=theme_id,
        include_weak_industry_candidates=payload.include_weak_industry_candidates,
        include_watchlist_themes=payload.include_watchlist_themes,
        include_leveraged_inverse_etfs=payload.include_leveraged_inverse_etfs,
        max_stock_candidates_per_theme=payload.max_stock_candidates_per_theme,
        max_etf_candidates_per_theme=payload.max_etf_candidates_per_theme,
    )


@router.get("/{theme_id}/candidates", response_model=list[ThemeSecurityCandidateRead])
def theme_candidate_list(
    theme_id: int,
    db: Session = Depends(get_db),
    asset_type: str | None = None,
    match_status: str | None = None,
    limit: int = 100,
):
    theme = get_market_theme(db, theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    candidates = theme_candidates_for_api(db, theme_id, asset_type=asset_type, match_status=match_status, limit=limit)
    for candidate in candidates:
        candidate["theme_name"] = theme.theme_name
    return candidates


@router.post("/{theme_id}/match-securities")
def theme_match_securities(theme_id: int, db: Session = Depends(get_db)):
    theme = get_market_theme(db, theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    return match_theme_securities(db, theme_id)


@router.get("/{theme_id}/security-candidates")
def theme_security_candidate_list(theme_id: int, db: Session = Depends(get_db)):
    theme = get_market_theme(db, theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    return theme_security_candidates(db, theme_id)


@router.get("/{theme_id}/recommendations")
def theme_recommendation_list(theme_id: int, db: Session = Depends(get_db)):
    theme = get_market_theme(db, theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme not found")
    result = theme_recommendations(db, theme_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="theme recommendations not found")
    return result
