from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.providers.ai.openai_news_analyzer import OpenAIConfigurationError, OpenAIInvalidRequestError
from app.services.news_analysis_service import run_analysis, run_one_analysis_test

router = APIRouter(prefix="/news-analysis", tags=["AI News Analysis"])


@router.post("/run")
def run_endpoint(limit: int = 10, provider: str = "openai", force: bool = False, db: Session = Depends(get_db)):
    settings = get_settings()
    if provider not in ("openai", "mock"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid provider")
    if force and settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="force is allowed in development only")

    try:
        return run_analysis(db, limit=limit, provider=provider, force=force)
    except OpenAIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=getattr(exc, "user_message", "OpenAI 설정 오류가 발생했습니다."),
        ) from None
    except OpenAIInvalidRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=getattr(exc, "user_message", "OpenAI API 요청 형식이 올바르지 않습니다."),
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="뉴스 분석 실행 중 내부 오류가 발생했습니다.",
        )


@router.post("/test-one")
def test_one_endpoint(provider: str = "openai", db: Session = Depends(get_db)):
    if provider not in ("openai", "mock"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid provider")
    try:
        return run_one_analysis_test(db, provider=provider)
    except OpenAIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=getattr(exc, "user_message", "OpenAI 설정 오류가 발생했습니다."),
        ) from None
    except OpenAIInvalidRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=getattr(exc, "user_message", "OpenAI API 요청 형식이 올바르지 않습니다."),
        ) from None


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    from app.repositories.news_analysis_repository import list_unanalyzed_news

    settings = get_settings()
    pending = len(list_unanalyzed_news(db, settings.openai_model, settings.news_analysis_prompt_version, limit=1))
    return {"pending_sample": pending}
