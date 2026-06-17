from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.models.news_analysis import NewsAnalysis
from app.models.news_analysis_run import NewsAnalysisRun
from app.providers.ai.mock import MockNewsAnalyzer
from app.providers.ai.openai_news_analyzer import (
    OpenAIConfigurationError,
    OpenAIInvalidRequestError,
    OpenAINewsAnalyzer,
    OpenAIResponseError,
    OpenAIValidationError,
)
from app.repositories.news_analysis_repository import (
    create_analysis_run,
    list_unanalyzed_news,
    save_or_update_analysis,
    update_analysis_run,
)
from app.schemas.error import ErrorLogCreate
from app.services.error_service import create_error_log

settings = get_settings()
logger = logging.getLogger(__name__)

SAFE_OPENAI_CONTEXT_KEYS = {
    "http_status_code",
    "openai_error_code",
    "original_exception_type",
    "original_exception_module",
    "original_error_code",
    "original_error_type",
    "original_param",
    "request_id",
    "cause_exception_type",
    "context_exception_type",
    "retry_attempts",
    "timeout",
    "response_id",
    "response_status",
    "incomplete_reason",
    "output_item_types",
    "content_item_types",
    "has_refusal",
    "has_output_parsed",
    "has_output_text",
    "output_text_length",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "title_length",
    "description_length",
    "has_publisher",
    "has_published_at",
    "input_json_length",
    "validation_error_type",
    "validation_error_field",
    "error_class",
    "error_message_safe",
    "retryable",
}

OPENAI_ERROR_MESSAGES = {
    "OPENAI_INCOMPLETE_RESPONSE": "출력이 중간에 중단됐습니다.",
    "OPENAI_MAX_OUTPUT_TOKENS": "출력이 중간에 중단됐습니다.",
    "OPENAI_REFUSAL": "모델이 분석 요청을 거부했습니다.",
    "OPENAI_PARSE_ERROR": "구조화된 분석 결과를 해석하지 못했습니다.",
    "OPENAI_EMPTY_RESPONSE": "OpenAI 응답에 분석 결과가 없습니다.",
    "OPENAI_RESPONSE_ERROR": "OpenAI API 응답 오류가 발생했습니다.",
    "OPENAI_CONNECTION_ERROR": "일부 뉴스 분석 중 연결 오류가 발생했습니다. 실패한 뉴스는 다음 실행에서 다시 시도할 수 있습니다.",
    "OPENAI_TIMEOUT": "OpenAI 요청 시간이 초과되었습니다.",
    "OPENAI_QUOTA_EXCEEDED": "OpenAI API 사용 가능 잔액 또는 결제 한도를 확인하세요.",
    "OPENAI_RATE_LIMIT": "OpenAI API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
    "OPENAI_INVALID_REQUEST": "OpenAI API 요청 형식이 올바르지 않습니다.",
    "OPENAI_VALIDATION_ERROR": "구조화된 분석 결과를 해석하지 못했습니다.",
}

OPENAI_ERROR_MESSAGES.update(
    {
        "OPENAI_OUTPUT_PARSED_NONE": "OpenAI 응답에 구조화된 분석 결과가 없습니다.",
        "OPENAI_SCHEMA_VALIDATION_ERROR": "구조화된 분석 결과가 스키마 검증을 통과하지 못했습니다.",
        "OPENAI_REFUSAL": "모델이 분석 요청을 거부했습니다.",
        "OPENAI_INCOMPLETE": "출력이 중간에 중단됐습니다.",
        "OPENAI_MAX_OUTPUT_TOKENS": "응답이 출력 토큰 제한으로 중단되었습니다.",
        "OPENAI_EMPTY_OUTPUT": "OpenAI 응답에 분석 결과가 없습니다.",
        "OPENAI_JSON_PARSE_ERROR": "OpenAI 응답 JSON을 해석하지 못했습니다.",
        "OPENAI_RESPONSE_FORMAT_ERROR": "OpenAI 응답 형식이 예상과 다릅니다.",
        "OPENAI_MODEL_CONFIG_ERROR": "OpenAI 모델 설정이 올바르지 않습니다.",
        "OPENAI_AUTH_ERROR": "OpenAI 인증 오류가 발생했습니다.",
        "OPENAI_RATE_LIMIT_ERROR": "OpenAI API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
        "OPENAI_QUOTA_ERROR": "OpenAI API 사용 가능 잔액 또는 결제 한도를 확인하세요.",
        "OPENAI_CONNECTION_ERROR": "일부 뉴스 분석 중 연결 오류가 발생했습니다. 실패한 뉴스는 다음 실행에서 다시 시도할 수 있습니다.",
        "OPENAI_TIMEOUT_ERROR": "OpenAI 요청 시간이 초과되었습니다.",
        "OPENAI_INVALID_REQUEST": "OpenAI API 요청 형식이 올바르지 않습니다.",
        "OPENAI_RESPONSE_ERROR": "OpenAI API 응답 오류가 발생했습니다.",
    }
)

FATAL_EARLY_STOP_LIMITS = {
    "OPENAI_AUTH_ERROR": 1,
    "OPENAI_MODEL_CONFIG_ERROR": 1,
    "OPENAI_QUOTA_ERROR": 1,
    "OPENAI_SCHEMA_VALIDATION_ERROR": 5,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log_analysis_error(
    db: Any,
    error_code: str,
    error_type: str,
    message: str,
    context_json: dict[str, Any] | None = None,
) -> None:
    try:
        create_error_log(
            db,
            ErrorLogCreate(
                error_code=error_code,
                severity="ERROR",
                component="news_ai_analyzer",
                error_type=error_type,
                message=message,
                context_json=context_json or {},
            ),
        )
    except Exception as exc:
        logger.warning("failed to write analysis error log: %s", type(exc).__name__)


def _safe_openai_context(exc: Exception) -> dict[str, Any]:
    diagnostics = getattr(exc, "safe_context", None) or getattr(exc, "diagnostic_context", {}) or {}
    return {key: diagnostics.get(key) for key in SAFE_OPENAI_CONTEXT_KEYS if key in diagnostics}


def _safe_article_input_profile(article: Any, article_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "title_length": len(article_input.get("title") or ""),
        "description_length": len(article_input.get("description") or ""),
        "has_publisher": bool(article_input.get("publisher")),
        "has_published_at": getattr(article, "published_at", None) is not None,
        "input_json_length": len(json.dumps(article_input, ensure_ascii=False, default=str)),
    }


def run_analysis(db: Any, limit: int | None = None, provider: str = "openai", force: bool = False) -> dict[str, Any]:
    run_id = f"ANALYSIS-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    model_name = settings.openai_model
    prompt_version = settings.news_analysis_prompt_version
    batch = limit or settings.news_analysis_batch_size
    batch = max(1, min(batch, 50))

    run = NewsAnalysisRun(
        run_id=run_id,
        model_name=model_name,
        prompt_version=prompt_version,
        requested_count=batch,
        status="running",
        started_at=_now(),
    )
    run = create_analysis_run(db, run)

    if provider == "mock":
        analyzer = MockNewsAnalyzer()
    else:
        try:
            analyzer = OpenAINewsAnalyzer()
        except OpenAIConfigurationError as exc:
            error_code = getattr(exc, "error_code", "OPENAI_CONFIGURATION_ERROR")
            _log_analysis_error(
                db,
                error_code=error_code,
                error_type=type(exc).__name__,
                message="OpenAI 설정 오류가 발생했습니다.",
                context_json={"run_id": run_id, "reason": error_code},
            )
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = _now()
            run.duration_ms = 0
            update_analysis_run(db, run)
            raise

    to_process = list_unanalyzed_news(db, model_name, prompt_version, limit=batch, include_completed=force)
    run.requested_count = len(to_process)
    completed = 0
    failed = 0
    skipped = 0
    input_tokens = 0
    output_tokens = 0
    error_codes: list[str] = []
    error_messages: list[str] = []
    consecutive_error_code: str | None = None
    consecutive_error_count = 0
    early_stopped = False
    stop_reason: str | None = None
    start = time.time()

    for article in to_process:
        article_input = {
            "title": article.title,
            "description": article.description,
            "publisher": article.publisher,
        }
        input_profile = _safe_article_input_profile(article, article_input)
        try:
            out, meta = asyncio.run(
                analyzer.analyze(article_input)
            )
            na = NewsAnalysis(
                news_article_id=article.id,
                analysis_run_id=run_id,
                model_name=model_name,
                prompt_version=prompt_version,
                status="completed",
                summary=out.summary,
                event_type=out.event_type,
                impact_direction=out.impact_direction,
                sentiment_score=out.sentiment_score,
                importance_score=out.importance_score,
                novelty_score=out.novelty_score,
                market_relevance_score=out.market_relevance_score,
                confidence_score=out.confidence_score,
                time_horizon=out.time_horizon,
                candidate_themes_json=out.candidate_themes,
                companies_json=[c.model_dump() for c in out.companies],
                evidence_points_json=out.evidence_points,
                risk_factors_json=out.risk_factors,
                is_investment_relevant=out.is_investment_relevant,
                input_tokens=meta.get("tokens", {}).get("input"),
                output_tokens=meta.get("tokens", {}).get("output"),
                total_tokens=meta.get("tokens", {}).get("total"),
                latency_ms=meta.get("latency_ms"),
                openai_request_id=meta.get("openai_request_id"),
            )
            save_or_update_analysis(db, na)
            completed += 1
            input_tokens += na.input_tokens or 0
            output_tokens += na.output_tokens or 0
            consecutive_error_code = None
            consecutive_error_count = 0
        except Exception as exc:
            failed += 1
            error_code = "NEWS_ANALYSIS_ERROR"
            if isinstance(exc, (OpenAIInvalidRequestError, OpenAIValidationError, OpenAIResponseError)):
                error_code = getattr(exc, "error_code", error_code)

            error_codes.append(error_code)
            message = OPENAI_ERROR_MESSAGES.get(
                error_code,
                getattr(exc, "safe_message", None) or "뉴스 분석 중 오류가 발생했습니다.",
            )
            if error_code in OPENAI_ERROR_MESSAGES and message not in error_messages:
                error_messages.append(message)

            context_json = {
                "news_article_id": article.id,
                "run_id": run_id,
                "model_name": model_name,
                "prompt_version": prompt_version,
            }
            if error_code not in {"OPENAI_QUOTA_EXCEEDED", "OPENAI_RATE_LIMIT"}:
                context_json.update(input_profile)
            context_json.update(_safe_openai_context(exc))

            _log_analysis_error(
                db,
                error_code=error_code,
                error_type=type(exc).__name__,
                message=message,
                context_json=context_json,
            )

            if error_code == consecutive_error_code:
                consecutive_error_count += 1
            else:
                consecutive_error_code = error_code
                consecutive_error_count = 1
            stop_limit = FATAL_EARLY_STOP_LIMITS.get(error_code)
            if stop_limit is not None and consecutive_error_count >= stop_limit:
                early_stopped = True
                stop_reason = f"{error_code} repeated {consecutive_error_count} time(s)"
                skipped += max(len(to_process) - completed - failed - skipped, 0)
                break

    run.completed_count = completed
    run.failed_count = failed
    run.skipped_count = skipped
    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.total_tokens = input_tokens + output_tokens
    run.status = "completed" if failed == 0 else ("partial" if completed > 0 else "failed")
    run.completed_at = _now()
    run.duration_ms = int((time.time() - start) * 1000)
    update_analysis_run(db, run)

    return {
        "run_id": run_id,
        "requested": len(to_process),
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "error_codes": sorted(set(error_codes)),
        "error_messages": error_messages,
        "early_stopped": early_stopped,
        "stop_reason": stop_reason,
    }


def run_one_analysis_test(db: Any, provider: str = "openai") -> dict[str, Any]:
    settings = get_settings()
    model_name = settings.openai_model
    prompt_version = settings.news_analysis_prompt_version
    rows = list_unanalyzed_news(db, model_name, prompt_version, limit=1)
    if not rows:
        return {
            "status": "insufficient_data",
            "completed": False,
            "error_code": None,
            "diagnostics": {},
            "message": "분석 가능한 미분석 뉴스가 없습니다.",
        }
    article = rows[0]
    analyzer = MockNewsAnalyzer() if provider == "mock" else OpenAINewsAnalyzer()
    article_input = {
        "title": article.title,
        "description": article.description,
        "publisher": article.publisher,
    }
    try:
        out, meta = asyncio.run(analyzer.analyze(article_input))
        diagnostics = dict(meta.get("diagnostics") or {})
        return {
            "status": "completed",
            "completed": True,
            "article_id": article.id,
            "summary_preview": out.summary[:200],
            "event_type": out.event_type,
            "impact_direction": out.impact_direction,
            "diagnostics": {key: diagnostics.get(key) for key in SAFE_OPENAI_CONTEXT_KEYS if key in diagnostics},
        }
    except Exception as exc:
        error_code = getattr(exc, "error_code", "NEWS_ANALYSIS_ERROR")
        context_json = {
            "news_article_id": article.id,
            "run_id": "TEST-ONE",
            "model_name": model_name,
            "prompt_version": prompt_version,
            **_safe_article_input_profile(article, article_input),
            **_safe_openai_context(exc),
        }
        message = OPENAI_ERROR_MESSAGES.get(error_code, getattr(exc, "safe_message", None) or "뉴스 분석 중 오류가 발생했습니다.")
        _log_analysis_error(db, error_code, type(exc).__name__, message, context_json)
        return {
            "status": "failed",
            "completed": False,
            "article_id": article.id,
            "error_code": error_code,
            "message": message,
            "diagnostics": context_json,
        }
