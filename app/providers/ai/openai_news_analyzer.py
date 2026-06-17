from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from json import JSONDecodeError
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.providers.ai.base import NewsAnalyzerProvider
from app.schemas.news_analysis import NewsAnalysisOutput
from app.services.app_setting_service import get_secret_value, runtime_openai_model

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AsyncOpenAI,
        AuthenticationError,
        BadRequestError,
        ContentFilterFinishReasonError,
        LengthFinishReasonError,
        OpenAIError,
        RateLimitError,
    )
except Exception:
    AsyncOpenAI = None  # type: ignore[assignment]
    BadRequestError = AuthenticationError = RateLimitError = APITimeoutError = OpenAIError = Exception
    APIConnectionError = APIStatusError = Exception
    ContentFilterFinishReasonError = LengthFinishReasonError = Exception

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "기사 제목, 설명, 언론사 정보만 사용해 주식 뉴스 분석을 수행하세요. "
    "제공되지 않은 기업명, 수치, 종목 코드를 만들지 마세요. "
    "근거가 없으면 companies는 빈 목록으로 반환하고, 긍정 뉴스가 주가 상승을 보장한다고 표현하지 마세요. "
    "결과는 NewsAnalysisOutput 스키마에 맞는 구조화된 출력으로 제공하세요."
)
SYSTEM_PROMPT += (
    " 모든 사용자에게 보이는 설명, 요약, 근거, 위험 요인, 후보 테마는 한국어로 작성하세요."
    " ticker, 공식 회사명, ETF명, 거래소 코드는 원문을 유지해도 됩니다."
    " candidate_themes는 영어만으로 쓰지 말고 한국어 중심으로 작성하되 필요한 경우 괄호 안에 영문 키워드를 병기하세요."
)


SYSTEM_PROMPT += (
    " candidate_themes에는 단순 회사명만 넣지 말고 뉴스의 직접 이슈, 직접 영향 산업, 시장 테마를 분리해 한국어 중심 태그로 작성하세요."
    " 특정 기업이 언급되면 그 기업의 본업 산업을 합리적으로 추론하되, 모르면 임의로 만들지 말고 근거가 있는 테마만 포함하세요."
    " 비상장 기업 자체를 상장 종목 후보처럼 표현하지 말고, 관련 산업 또는 테마 맥락으로만 다루세요."
)


SYSTEM_PROMPT += (
    " Evaluate whether the news can affect listed stock or ETF prices, not just whether it is economic news. "
    " Distinguish simple finance, allocation controversy, enforcement, legal uncertainty, or policy process news from investable themes. "
    " If price direction or listed stock/ETF linkage is unclear, treat it as watchlist or risk_alert context rather than a candidate theme. "
    " Exclude themes that are hard to connect to Korean listed stocks or ETFs from candidate generation language. "
    " If the story is centered on a private company, only describe listed-stock or ETF linkage when the evidence is direct and strong."
)


class NewsAnalyzerError(Exception):
    error_code = "NEWS_ANALYZER_ERROR"
    user_message = "뉴스 분석 중 오류가 발생했습니다."


class OpenAIConfigurationError(NewsAnalyzerError):
    error_code = "OPENAI_CONFIGURATION_ERROR"
    user_message = "OpenAI 설정 오류가 발생했습니다."


class OpenAIAPIKeyMissingError(OpenAIConfigurationError):
    error_code = "OPENAI_API_KEY_MISSING"
    user_message = "OPENAI_API_KEY가 설정되지 않았습니다."


class OpenAIPackageMissingError(OpenAIConfigurationError):
    error_code = "OPENAI_PACKAGE_MISSING"
    user_message = "OpenAI Python SDK가 설치되지 않았습니다."


class OpenAIUnsupportedSDKError(OpenAIConfigurationError):
    error_code = "OPENAI_UNSUPPORTED_SDK"
    user_message = "설치된 OpenAI SDK가 Responses API Structured Outputs를 지원하지 않습니다."


class OpenAIInvalidRequestError(NewsAnalyzerError):
    error_code = "OPENAI_INVALID_REQUEST"
    user_message = "OpenAI API 요청 형식이 올바르지 않습니다."

    def __init__(
        self,
        message: str,
        http_status_code: int | None = None,
        diagnostics: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.http_status_code = http_status_code
        self.diagnostic_context: dict[str, Any] = diagnostics or {"http_status_code": http_status_code}
        self.diagnostic_context.setdefault("http_status_code", http_status_code)
        self.diagnostic_context.setdefault("error_class", type(self).__name__)
        self.diagnostic_context.setdefault("error_message_safe", self.user_message)
        self.diagnostic_context.setdefault("retryable", False)
        self.safe_context = self.diagnostic_context
        self.safe_message = self.user_message


class OpenAIValidationError(NewsAnalyzerError):
    error_code = "OPENAI_SCHEMA_VALIDATION_ERROR"
    user_message = "OpenAI 응답을 검증할 수 없습니다."

    def __init__(self, message: str, status: str | None = None, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.diagnostic_context = diagnostics or {}
        self.diagnostic_context.setdefault("error_class", type(self).__name__)
        self.diagnostic_context.setdefault("error_message_safe", self.user_message)
        self.diagnostic_context.setdefault("retryable", False)
        self.safe_context = self.diagnostic_context
        self.safe_message = self.user_message


class OpenAIIncompleteResponseError(OpenAIValidationError):
    error_code = "OPENAI_INCOMPLETE"
    user_message = "OpenAI 응답이 완료되지 않았습니다."


class OpenAIMaxOutputTokensError(OpenAIIncompleteResponseError):
    error_code = "OPENAI_MAX_OUTPUT_TOKENS"
    user_message = "응답이 출력 토큰 제한으로 중단되었습니다."


class OpenAIRefusalError(OpenAIValidationError):
    error_code = "OPENAI_REFUSAL"
    user_message = "모델이 요청 처리를 거부했습니다."


class OpenAIParseError(OpenAIValidationError):
    error_code = "OPENAI_JSON_PARSE_ERROR"
    user_message = "구조화된 응답을 해석하지 못했습니다."


class OpenAIEmptyResponseError(OpenAIValidationError):
    error_code = "OPENAI_EMPTY_OUTPUT"
    user_message = "OpenAI 응답에 분석 결과가 없습니다."


class OpenAIOutputParsedNoneError(OpenAIValidationError):
    error_code = "OPENAI_OUTPUT_PARSED_NONE"
    user_message = "OpenAI 응답에 구조화된 분석 결과가 없습니다."


class OpenAIResponseFormatError(OpenAIValidationError):
    error_code = "OPENAI_RESPONSE_FORMAT_ERROR"
    user_message = "OpenAI 응답 형식이 예상과 다릅니다."


class OpenAIModelConfigError(OpenAIConfigurationError):
    error_code = "OPENAI_MODEL_CONFIG_ERROR"
    user_message = "OpenAI 모델 설정이 올바르지 않습니다."


class OpenAIResponseError(NewsAnalyzerError):
    error_code = "OPENAI_RESPONSE_ERROR"
    user_message = "OpenAI API 응답 오류가 발생했습니다."

    def __init__(
        self,
        message: str,
        http_status_code: int | None = None,
        diagnostics: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.http_status_code = http_status_code
        self.diagnostic_context: dict[str, Any] = diagnostics or {"http_status_code": http_status_code}
        self.diagnostic_context.setdefault("http_status_code", http_status_code)
        self.diagnostic_context.setdefault("error_class", type(self).__name__)
        self.diagnostic_context.setdefault("error_message_safe", self.user_message)
        self.diagnostic_context.setdefault("retryable", False)
        self.safe_context = self.diagnostic_context
        self.safe_message = self.user_message


class OpenAIQuotaExceededError(OpenAIResponseError):
    error_code = "OPENAI_QUOTA_ERROR"
    user_message = "OpenAI API 사용 가능 잔액 또는 결제 한도를 확인하세요."


class OpenAIRateLimitError(OpenAIResponseError):
    error_code = "OPENAI_RATE_LIMIT_ERROR"
    user_message = "OpenAI API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요."


class OpenAIConnectionError(OpenAIResponseError):
    error_code = "OPENAI_CONNECTION_ERROR"
    user_message = "OpenAI API 연결에 실패했습니다."


class OpenAITimeoutError(OpenAIResponseError):
    error_code = "OPENAI_TIMEOUT_ERROR"
    user_message = "OpenAI 요청 시간이 초과되었습니다."


class OpenAIAuthError(OpenAIResponseError):
    error_code = "OPENAI_AUTH_ERROR"
    user_message = "OpenAI 인증 오류가 발생했습니다."


def build_openai_client_from_runtime_settings(client: Any | None = None) -> tuple[Any, str]:
    settings = get_settings()
    api_key = get_secret_value("OPENAI_API_KEY") or settings.openai_api_key
    model_name = runtime_openai_model() or settings.openai_model
    if not OpenAINewsAnalyzer._is_valid_model_name(model_name):
        raise OpenAIModelConfigError(OpenAIModelConfigError.user_message)
    if not api_key:
        raise OpenAIAPIKeyMissingError(OpenAIAPIKeyMissingError.user_message)
    if AsyncOpenAI is None:
        raise OpenAIPackageMissingError(OpenAIPackageMissingError.user_message)
    openai_client = client or AsyncOpenAI(
        api_key=api_key,
        timeout=_openai_timeout(),
        max_retries=settings.openai_max_retries,
    )
    responses = getattr(openai_client, "responses", None)
    if responses is None or not hasattr(responses, "parse"):
        raise OpenAIUnsupportedSDKError(OpenAIUnsupportedSDKError.user_message)
    return openai_client, model_name


def _timeout_settings() -> dict[str, float]:
    return {
        "connect": 10.0,
        "read": 60.0,
        "write": 20.0,
        "pool": 10.0,
    }


def _openai_timeout() -> httpx.Timeout:
    values = _timeout_settings()
    return httpx.Timeout(
        connect=values["connect"],
        read=values["read"],
        write=values["write"],
        pool=values["pool"],
    )


def _get_value(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _get_usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = _get_value(usage, name)
        if value is not None:
            return value
    return None


def _get_openai_error_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            error_code = error.get("code")
            if isinstance(error_code, str) and error_code:
                return error_code
        body_code = body.get("code")
        if isinstance(body_code, str) and body_code:
            return body_code

    return None


def _get_request_id(exc: Exception) -> str | None:
    for name in ("request_id", "_request_id", "x_request_id"):
        value = getattr(exc, name, None)
        if isinstance(value, str) and value:
            return value

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("x-request-id", "request-id"):
            try:
                value = headers.get(name)
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
    return None


def _original_exception_context(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    body_error = body.get("error") if isinstance(body, dict) else None
    cause = getattr(exc, "__cause__", None)
    context = getattr(exc, "__context__", None)
    return {
        "original_exception_type": type(exc).__name__,
        "original_exception_module": type(exc).__module__,
        "original_error_code": _get_openai_error_code(exc),
        "original_error_type": body_error.get("type") if isinstance(body_error, dict) else getattr(exc, "type", None),
        "original_param": body_error.get("param") if isinstance(body_error, dict) else getattr(exc, "param", None),
        "http_status_code": getattr(exc, "status_code", None),
        "request_id": _get_request_id(exc),
        "cause_exception_type": type(cause).__name__ if cause is not None else None,
        "context_exception_type": type(context).__name__ if context is not None else None,
    }


def _merge_diagnostics(exc: Exception, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = _original_exception_context(exc)
    diagnostics["error_class"] = type(exc).__name__
    diagnostics["error_message_safe"] = type(exc).__name__
    if extra:
        diagnostics.update(extra)
    return diagnostics


def _validation_error_context(exc: ValidationError, diagnostics: dict[str, Any]) -> dict[str, Any]:
    fields: list[str] = []
    for error in exc.errors()[:5]:
        loc = error.get("loc")
        if isinstance(loc, (list, tuple)):
            fields.append(".".join(str(part) for part in loc))
        elif loc is not None:
            fields.append(str(loc))
    return {
        **diagnostics,
        "validation_error_type": type(exc).__name__,
        "validation_error_field": fields,
        "error_class": type(exc).__name__,
        "error_message_safe": "Pydantic validation failed",
        "retryable": False,
    }


def _connection_retry_context(exc: Exception, attempts: int) -> dict[str, Any]:
    return _merge_diagnostics(
        exc,
        {
            "retry_attempts": attempts,
            "timeout": _timeout_settings(),
            "retryable": True,
        },
    )


def _rate_limit_context(exc: Exception, openai_error_code: str | None) -> dict[str, Any]:
    diagnostics = _original_exception_context(exc)
    diagnostics["openai_error_code"] = openai_error_code
    diagnostics["error_class"] = type(exc).__name__
    diagnostics["error_message_safe"] = type(exc).__name__
    diagnostics["retryable"] = openai_error_code != "insufficient_quota"
    return diagnostics


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value) if isinstance(value, tuple) else []


def _response_diagnostics(resp: Any) -> dict[str, Any]:
    output_items = _as_list(_get_value(resp, "output"))
    output_item_types: list[str | None] = []
    content_item_types: list[str | None] = []
    has_refusal = bool(_get_value(resp, "refusal"))

    for item in output_items:
        item_type = _get_value(item, "type")
        output_item_types.append(item_type)
        if item_type == "refusal":
            has_refusal = True

        for content in _as_list(_get_value(item, "content")):
            content_type = _get_value(content, "type")
            content_item_types.append(content_type)
            if content_type == "refusal" or _get_value(content, "refusal"):
                has_refusal = True

    incomplete_details = _get_value(resp, "incomplete_details")
    incomplete_reason = _get_value(incomplete_details, "reason")
    output_text = _get_value(resp, "output_text")
    output_parsed = _get_value(resp, "output_parsed")
    usage = _get_value(resp, "usage")

    return {
        "response_id": _get_value(resp, "id"),
        "response_status": _get_value(resp, "status"),
        "incomplete_reason": incomplete_reason,
        "output_item_types": output_item_types,
        "content_item_types": content_item_types,
        "has_refusal": has_refusal,
        "has_output_parsed": output_parsed is not None,
        "has_output_text": isinstance(output_text, str) and len(output_text) > 0,
        "output_text_length": len(output_text) if isinstance(output_text, str) else None,
        "input_tokens": _get_usage_value(usage, "input", "input_tokens"),
        "output_tokens": _get_usage_value(usage, "output", "output_tokens"),
        "total_tokens": _get_usage_value(usage, "total", "total_tokens"),
    }


def _has_no_output_content(diagnostics: dict[str, Any]) -> bool:
    return (
        not diagnostics["output_item_types"]
        and not diagnostics["content_item_types"]
        and not diagnostics["has_output_text"]
    )


def _extract_openai_response(exc: Exception) -> Any | None:
    for name in ("response", "openai_response", "parsed_response"):
        value = getattr(exc, name, None)
        if value is None:
            continue
        if isinstance(value, dict) and ("output" in value or "status" in value):
            return value
        if hasattr(value, "output") or hasattr(value, "output_parsed") or hasattr(value, "status"):
            return value
    return None


def _coerce_response_output(resp: Any) -> tuple[NewsAnalysisOutput, dict[str, Any]]:
    diagnostics = _response_diagnostics(resp)
    status = diagnostics["response_status"]
    incomplete_reason = diagnostics["incomplete_reason"]

    if status == "incomplete":
        if incomplete_reason in {"max_output_tokens", "max_tokens"}:
            raise OpenAIMaxOutputTokensError(OpenAIMaxOutputTokensError.user_message, status, diagnostics)
        raise OpenAIIncompleteResponseError(OpenAIIncompleteResponseError.user_message, status, diagnostics)

    if status == "refused" or diagnostics["has_refusal"]:
        raise OpenAIRefusalError(OpenAIRefusalError.user_message, status, diagnostics)

    output_parsed = _get_value(resp, "output_parsed")
    output_text = _get_value(resp, "output_text")
    if output_parsed is None and isinstance(output_text, str) and output_text.strip():
        try:
            output_parsed = NewsAnalysisOutput.model_validate_json(output_text)
        except ValidationError as exc:
            first_type = (exc.errors() or [{}])[0].get("type")
            if first_type == "json_invalid":
                raise OpenAIParseError(
                    OpenAIParseError.user_message,
                    status,
                    {**diagnostics, "validation_error_type": first_type, "retryable": False},
                ) from exc
            raise OpenAIValidationError(
                OpenAIValidationError.user_message,
                status,
                _validation_error_context(exc, diagnostics),
            ) from exc
        except JSONDecodeError as exc:
            raise OpenAIParseError(OpenAIParseError.user_message, status, diagnostics) from exc

    if output_parsed is None:
        if _has_no_output_content(diagnostics):
            raise OpenAIEmptyResponseError(OpenAIEmptyResponseError.user_message, status, diagnostics)
        raise OpenAIOutputParsedNoneError(OpenAIOutputParsedNoneError.user_message, status, diagnostics)

    try:
        if isinstance(output_parsed, NewsAnalysisOutput):
            return output_parsed, diagnostics
        return NewsAnalysisOutput.model_validate(output_parsed), diagnostics
    except ValidationError as exc:
        raise OpenAIValidationError(
            OpenAIValidationError.user_message,
            status,
            _validation_error_context(exc, diagnostics),
        ) from exc


class OpenAINewsAnalyzer(NewsAnalyzerProvider):
    def __init__(self, client: Any | None = None):
        self.settings = get_settings()
        self.client, self._model = build_openai_client_from_runtime_settings(client)

    @staticmethod
    def _is_valid_model_name(model_name: str | None) -> bool:
        if not model_name or not isinstance(model_name, str):
            return False
        if model_name.startswith(("http://", "https://")) or any(ch.isspace() for ch in model_name):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9._:-]+", model_name))

    async def analyze(self, article_input: dict[str, Any]) -> tuple[NewsAnalysisOutput, dict[str, Any]]:
        article_payload = {
            "title": article_input.get("title"),
            "description": article_input.get("description"),
            "publisher": article_input.get("publisher"),
        }
        article_json = json.dumps(article_payload, ensure_ascii=False, default=str)
        request_input = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": article_json},
        ]

        try:
            resp = await self._parse_with_rate_limit_retry(request_input)
        except NewsAnalyzerError:
            raise
        except BadRequestError as exc:
            self._log_original_exception(exc)
            raise OpenAIInvalidRequestError(
                OpenAIInvalidRequestError.user_message,
                http_status_code=getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc),
            ) from exc
        except AuthenticationError as exc:
            self._log_original_exception(exc)
            raise OpenAIAuthError(
                "OpenAI 인증 오류가 발생했습니다.",
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc),
            ) from exc
        except RateLimitError as exc:
            self._log_original_exception(exc)
            self._raise_rate_limit_error(exc)
        except APITimeoutError as exc:
            self._log_original_exception(exc)
            raise OpenAITimeoutError(
                OpenAITimeoutError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_connection_retry_context(exc, attempts=1),
            ) from exc
        except APIConnectionError as exc:
            self._log_original_exception(exc)
            raise OpenAIConnectionError(
                OpenAIConnectionError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_connection_retry_context(exc, attempts=1),
            ) from exc
        except LengthFinishReasonError as exc:
            self._log_original_exception(exc)
            raise OpenAIMaxOutputTokensError(
                OpenAIMaxOutputTokensError.user_message,
                None,
                _merge_diagnostics(exc, {"incomplete_reason": "max_output_tokens"}),
            ) from exc
        except ContentFilterFinishReasonError as exc:
            self._log_original_exception(exc)
            raise OpenAIRefusalError(
                OpenAIRefusalError.user_message,
                None,
                _merge_diagnostics(exc, {"has_refusal": True}),
            ) from exc
        except ValidationError as exc:
            self._log_original_exception(exc)
            raise OpenAIValidationError(
                OpenAIValidationError.user_message,
                None,
                _merge_diagnostics(exc),
            ) from exc
        except JSONDecodeError as exc:
            self._log_original_exception(exc)
            raise OpenAIParseError(
                OpenAIParseError.user_message,
                None,
                _merge_diagnostics(exc),
            ) from exc
        except (APIStatusError, OpenAIError) as exc:
            self._log_original_exception(exc)
            response = _extract_openai_response(exc)
            if response is not None:
                _coerce_response_output(response)
            raise OpenAIResponseError(
                OpenAIResponseError.user_message,
                getattr(exc, "status_code", None),
                diagnostics=_merge_diagnostics(exc),
            ) from exc
        except TypeError as exc:
            self._log_original_exception(exc)
            raise OpenAIResponseFormatError(
                OpenAIResponseFormatError.user_message,
                None,
                diagnostics=_merge_diagnostics(exc),
            ) from exc

        out, diagnostics = _coerce_response_output(resp)

        meta: dict[str, Any] = {
            "tokens": {
                "input": diagnostics["input_tokens"],
                "output": diagnostics["output_tokens"],
                "total": diagnostics["total_tokens"],
            },
            "latency_ms": getattr(resp, "latency_ms", None),
            "openai_request_id": diagnostics["response_id"],
            "diagnostics": diagnostics,
        }
        return out, meta

    async def _parse_with_rate_limit_retry(self, request_input: list[dict[str, str]]) -> Any:
        rate_limit_attempts = max(1, int(getattr(self.settings, "openai_max_retries", 0)) + 1)
        connection_attempts = 2
        last_connection_error: Exception | None = None

        for connection_attempt in range(connection_attempts):
            try:
                for rate_limit_attempt in range(rate_limit_attempts):
                    try:
                        return await self.client.responses.parse(
                            model=self._model,
                            input=request_input,
                            text_format=NewsAnalysisOutput,
                        )
                    except RateLimitError as exc:
                        if _get_openai_error_code(exc) == "insufficient_quota":
                            raise
                        if rate_limit_attempt >= rate_limit_attempts - 1:
                            raise
                        await asyncio.sleep(min(2 ** rate_limit_attempt, 8))
            except APIConnectionError as exc:
                last_connection_error = exc
                if connection_attempt >= connection_attempts - 1:
                    diagnostics = _connection_retry_context(exc, attempts=connection_attempt + 1)
                    raise OpenAIConnectionError(
                        OpenAIConnectionError.user_message,
                        getattr(exc, "status_code", None),
                        diagnostics=diagnostics,
                    ) from exc
                delay = min(2 ** connection_attempt, 8) + random.uniform(0, 0.25)
                await asyncio.sleep(delay)

        if last_connection_error is not None:
            raise last_connection_error
        raise RuntimeError("unreachable rate limit retry state")

    def _log_original_exception(self, exc: Exception) -> None:
        if getattr(self.settings, "app_env", "development") == "development":
            logger.exception("OpenAI news analyzer provider exception: %s", type(exc).__name__)

    def _raise_rate_limit_error(self, exc: Exception) -> None:
        openai_error_code = _get_openai_error_code(exc)
        diagnostics = _rate_limit_context(exc, openai_error_code)
        if openai_error_code == "insufficient_quota":
            err = OpenAIQuotaExceededError(
                OpenAIQuotaExceededError.user_message,
                http_status_code=getattr(exc, "status_code", None),
            )
        else:
            err = OpenAIRateLimitError(
                OpenAIRateLimitError.user_message,
                http_status_code=getattr(exc, "status_code", None),
            )
        err.diagnostic_context.update(diagnostics)
        raise err from exc
