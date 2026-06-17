from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.providers.ai.openai_news_analyzer import (
    APITimeoutError,
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    OpenAIAuthError,
    OpenAIAPIKeyMissingError,
    OpenAIConfigurationError,
    OpenAIInvalidRequestError,
    OpenAIIncompleteResponseError,
    OpenAIMaxOutputTokensError,
    OpenAIEmptyResponseError,
    OpenAIOutputParsedNoneError,
    OpenAIPackageMissingError,
    OpenAIQuotaExceededError,
    OpenAIRateLimitError,
    OpenAIConnectionError,
    OpenAITimeoutError,
    OpenAIRefusalError,
    OpenAIResponseError,
    OpenAIValidationError,
    OpenAIUnsupportedSDKError,
    RateLimitError,
    build_openai_client_from_runtime_settings,
    _get_openai_error_code,
    _merge_diagnostics,
    _response_diagnostics,
)
from app.providers.ai.theme_analyzer_base import ThemeAnalyzerProvider
from app.schemas.theme_analysis import ThemeSelectionOutput

SYSTEM_PROMPT = (
    "Use only the provided news analysis records to select up to three current market themes. "
    "Do not invent news_analysis_id values, companies, stock codes, target prices, or ETFs. "
    "Merge very similar themes. If evidence is insufficient, return fewer than three themes. "
    "Each selected theme should use at least two distinct evidence items when possible. "
    "Return structured output matching ThemeSelectionOutput."
)
SYSTEM_PROMPT += (
    " 모든 사용자에게 보이는 설명, 요약, 위험 요인, 선정 이유, 근거 설명은 한국어로 작성하세요."
    " theme_name은 영어만으로 쓰지 말고 한국어 중심으로 작성하되 필요한 경우 괄호 안에 영문 키워드를 병기하세요."
    " ticker, 공식 회사명, ETF명, 거래소 코드는 원문을 유지해도 됩니다."
)


SYSTEM_PROMPT += (
    " Separate tags into issue_tags, direct_impact_industries, entity_business_industries, market_theme_tags, and candidate_search_tags."
    " Do not collapse all related industries into one field."
    " Extract direct issue tags from the news, infer normal business industries for mentioned entities only when supported,"
    " and build candidate_search_tags with Korean and English search synonyms for stock and ETF search."
    " If a mentioned company is private or unlisted, keep it as entity context and do not create a listed stock candidate from that company itself."
    " Classify negative themes as risk or caution themes rather than candidate recommendation themes."
    " 모든 사용자-facing 설명, 요약, 위험 요인, 선정 이유, 근거 설명은 한국어로 작성하세요."
    " theme_name은 영어만으로 쓰지 말고 한국어 중심으로 작성하되 필요한 경우 괄호 안에 영문 키워드를 병기하세요."
    " issue_tags, direct_impact_industries, market_theme_tags, candidate_search_tags도 한국어 중심으로 작성하고,"
    " 검색에 필요한 영어 키워드는 candidate_search_tags에만 보조로 포함하세요."
)


SYSTEM_PROMPT += (
    " Evaluate each theme by price impact and investable linkage, not by market relevance alone. "
    " Prefer themes that can plausibly connect to listed Korean stocks, listed ETFs, or clear industry price effects. "
    " Treat IPO allocation disputes, market fairness issues, enforcement actions, legal uncertainty, generic regulation, and weak private-company stories as risk_alert or watchlist context unless there is direct listed-stock or ETF impact. "
    " Do not promote low-actionability finance news into a top investable theme. "
    " Keep user-facing summaries, reasons, risks, and exclusion-style wording in Korean while preserving tickers, exchange codes, ETF names, and official company names when needed."
)


class OpenAIThemeAnalyzer(ThemeAnalyzerProvider):
    def __init__(self, client: Any | None = None):
        self.settings = get_settings()
        self.client, self._model = build_openai_client_from_runtime_settings(client)

    async def analyze(
        self,
        sources: list[dict[str, Any]],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[ThemeSelectionOutput, dict[str, Any]]:
        source_payload = {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "sources": sources,
        }
        source_json = json.dumps(source_payload, ensure_ascii=False, default=str)
        request_input = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": source_json},
        ]
        try:
            response = await self._parse_with_rate_limit_retry(request_input)
        except BadRequestError as exc:
            raise OpenAIInvalidRequestError(
                OpenAIInvalidRequestError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(
                    exc,
                    {
                        "model_name": self._model,
                        "schema_name": "ThemeSelectionOutput",
                        "input_item_count": len(request_input),
                        "prompt_version": self.settings.theme_analysis_prompt_version,
                    },
                ),
            ) from exc
        except AuthenticationError as exc:
            raise OpenAIAuthError(
                OpenAIAuthError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc, {"retryable": False}),
            ) from exc
        except RateLimitError as exc:
            code = _get_openai_error_code(exc)
            if code == "insufficient_quota":
                err = OpenAIQuotaExceededError(OpenAIQuotaExceededError.user_message, getattr(exc, "status_code", None))
            else:
                err = OpenAIRateLimitError(OpenAIRateLimitError.user_message, getattr(exc, "status_code", None))
            err.diagnostic_context.update({"openai_error_code": code})
            raise err from exc
        except APITimeoutError as exc:
            raise OpenAITimeoutError(
                OpenAITimeoutError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc, {"retryable": True}),
            ) from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise OpenAIConnectionError(
                OpenAIConnectionError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc, {"retryable": True}),
            ) from exc

        diagnostics = _response_diagnostics(response)
        status = diagnostics.get("response_status")
        if status == "incomplete":
            if diagnostics.get("incomplete_reason") in {"max_output_tokens", "max_tokens"}:
                raise OpenAIMaxOutputTokensError(OpenAIMaxOutputTokensError.user_message, status, diagnostics)
            raise OpenAIIncompleteResponseError(OpenAIIncompleteResponseError.user_message, status, diagnostics)
        if status == "refused" or diagnostics.get("has_refusal"):
            raise OpenAIRefusalError(OpenAIRefusalError.user_message, status, diagnostics)

        output_parsed = getattr(response, "output_parsed", None)
        if output_parsed is None:
            if not diagnostics.get("has_output_text") and not diagnostics.get("output_item_types"):
                raise OpenAIEmptyResponseError(OpenAIEmptyResponseError.user_message, status, diagnostics)
            raise OpenAIOutputParsedNoneError(OpenAIOutputParsedNoneError.user_message, status, diagnostics)

        try:
            output = (
                output_parsed
                if isinstance(output_parsed, ThemeSelectionOutput)
                else ThemeSelectionOutput.model_validate(output_parsed)
            )
        except ValidationError as exc:
            raise OpenAIValidationError(
                OpenAIValidationError.user_message,
                status,
                {**diagnostics, "validation_error_type": type(exc).__name__, "retryable": False},
            ) from exc

        usage = getattr(response, "usage", None)
        meta = {
            "tokens": {
                "input": getattr(usage, "input_tokens", None) if usage is not None else None,
                "output": getattr(usage, "output_tokens", None) if usage is not None else None,
                "total": getattr(usage, "total_tokens", None) if usage is not None else None,
            },
            "latency_ms": getattr(response, "latency_ms", None),
            "response_id": getattr(response, "id", None),
            "response_status": status,
            "model_name": self._model,
        }
        return output, meta

    async def _parse_with_rate_limit_retry(self, request_input: list[dict[str, str]]) -> Any:
        attempts = max(1, int(self.settings.openai_max_retries) + 1)
        for attempt in range(attempts):
            try:
                return await self.client.responses.parse(
                    model=self._model,
                    input=request_input,
                    text_format=ThemeSelectionOutput,
                )
            except RateLimitError as exc:
                if _get_openai_error_code(exc) == "insufficient_quota" or attempt >= attempts - 1:
                    raise
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError("unreachable theme retry state")
