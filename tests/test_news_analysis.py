from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from json import JSONDecodeError

import pytest
from fastapi.testclient import TestClient

from app.backend.main import app
from app import dashboard
from app.core.config import Settings
from app.models.news_analysis import NewsAnalysis
from app.models.error_log import ErrorLog
from app.models.news_article import NewsArticle
from app.providers.ai import openai_news_analyzer
from app.providers.ai.mock import MockNewsAnalyzer
from app.providers.ai.openai_news_analyzer import (
    OpenAIAPIKeyMissingError,
    OpenAIConfigurationError,
    OpenAIConnectionError,
    OpenAIInvalidRequestError,
    OpenAIEmptyResponseError,
    OpenAIMaxOutputTokensError,
    OpenAINewsAnalyzer,
    OpenAIPackageMissingError,
    OpenAIParseError,
    OpenAIQuotaExceededError,
    OpenAIRateLimitError,
    OpenAIRefusalError,
    OpenAIResponseError,
    OpenAITimeoutError,
    OpenAIUnsupportedSDKError,
    OpenAIValidationError,
)
from app.repositories.news_analysis_repository import list_unanalyzed_news
from app.schemas.news_analysis import NewsAnalysisOutput
from app.services import news_analysis_service


def _settings(api_key: str | None = "test-key", max_retries: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        openai_api_key=api_key,
        openai_model="gpt-5.4-mini",
        openai_timeout=30.0,
        openai_max_retries=max_retries,
        app_env="test",
        news_analysis_prompt_version="news-analysis-v1",
        news_analysis_batch_size=10,
    )


def _output(summary: str = "테스트 분석 결과입니다.") -> dict[str, Any]:
    return {
        "summary": summary,
        "event_type": "other",
        "impact_direction": "neutral",
        "sentiment_score": 0.0,
        "importance_score": 0.0,
        "novelty_score": 0.0,
        "market_relevance_score": 0.0,
        "confidence_score": 0.5,
        "time_horizon": "unknown",
        "candidate_themes": [],
        "companies": [],
        "evidence_points": [],
        "risk_factors": [],
        "is_investment_relevant": False,
    }


class FakeResponses:
    def __init__(self, response: Any | None = None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeClient:
    def __init__(self, response: Any | None = None, exc: Exception | None = None):
        self.responses = FakeResponses(response=response, exc=exc)


def _completed_response(output_parsed: Any | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_test",
        status="completed",
        output_parsed=_output() if output_parsed is None else output_parsed,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="safe text omitted")],
            )
        ],
        output_text=None,
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
        latency_ms=25,
    )


def _response(
    *,
    status: str = "completed",
    output_parsed: Any | None = None,
    output_text: str | None = None,
    output: list[Any] | None = None,
    incomplete_reason: str | None = None,
    usage: Any | None = None,
) -> SimpleNamespace:
    incomplete_details = None
    if incomplete_reason is not None:
        incomplete_details = SimpleNamespace(reason=incomplete_reason)
    return SimpleNamespace(
        id="resp_diag",
        status=status,
        output_parsed=output_parsed,
        output_text=output_text,
        output=[] if output is None else output,
        incomplete_details=incomplete_details,
        usage=usage,
        latency_ms=25,
    )


def _article(suffix: str = "1") -> NewsArticle:
    return NewsArticle(
        provider="mock",
        external_id=f"article-{suffix}",
        query="테스트",
        title=f"테스트 뉴스 {suffix}",
        description="삼성전자 관련 설명",
        link=f"https://example.com/{suffix}",
        publisher="테스트신문",
        available_at=datetime.now(timezone.utc),
        title_normalized=f"테스트 뉴스 {suffix}",
        content_hash=f"hash-{suffix}",
        raw_data={"secret": "raw data must not be sent"},
    )


def _analysis(
    article: NewsArticle,
    status: str,
    model_name: str = "gpt-5.4-mini",
    prompt_version: str = "news-analysis-v1",
) -> NewsAnalysis:
    return NewsAnalysis(
        news_article_id=article.id,
        analysis_run_id=f"run-{status}-{article.id}",
        model_name=model_name,
        prompt_version=prompt_version,
        status=status,
        summary=f"{status} summary",
        event_type="other",
        impact_direction="neutral",
        sentiment_score=0.0,
        importance_score=0.0,
        novelty_score=0.0,
        market_relevance_score=0.0,
        confidence_score=0.5,
        time_horizon="unknown",
        candidate_themes_json=[],
        companies_json=[],
        evidence_points_json=[],
        risk_factors_json=[],
        is_investment_relevant=False,
    )


def test_mock_analyzer_returns_structure():
    analyzer = MockNewsAnalyzer()
    out, meta = asyncio.run(analyzer.analyze({"title": "Test", "description": "d"}))
    assert isinstance(out, NewsAnalysisOutput)
    assert out.summary.startswith("Mock analysis")
    assert isinstance(meta, dict)


def test_openai_concurrency_setting_is_bounded():
    assert Settings(openai_concurrency=1).openai_concurrency == 1
    assert Settings(openai_concurrency=5).openai_concurrency == 5
    with pytest.raises(ValueError):
        Settings(openai_concurrency=0)
    with pytest.raises(ValueError):
        Settings(openai_concurrency=6)


def test_news_analysis_run_mock_endpoint(client: TestClient):
    response = client.post("/news-analysis/run", params={"provider": "mock", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == body["completed"] + body["failed"] + body["skipped"]
    assert "run_id" in body


def test_openai_analyzer_requires_configuration(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings(api_key=None))
    with pytest.raises(OpenAIAPIKeyMissingError):
        OpenAINewsAnalyzer()


def test_openai_provider_missing_key_returns_503(client: TestClient):
    response = client.post("/news-analysis/run", params={"provider": "openai", "limit": 1})
    assert response.status_code == 503
    assert response.json()["detail"] == "OPENAI_API_KEY가 설정되지 않았습니다."


def test_openai_package_missing_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    monkeypatch.setattr(openai_news_analyzer, "AsyncOpenAI", None)
    with pytest.raises(OpenAIPackageMissingError) as exc_info:
        OpenAINewsAnalyzer()
    assert exc_info.value.error_code == "OPENAI_PACKAGE_MISSING"


def test_openai_unsupported_sdk_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    with pytest.raises(OpenAIUnsupportedSDKError) as exc_info:
        OpenAINewsAnalyzer(client=SimpleNamespace(responses=SimpleNamespace()))
    assert exc_info.value.error_code == "OPENAI_UNSUPPORTED_SDK"


def test_openai_parse_request_is_list_json_and_text_format(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    client = FakeClient(response=_completed_response())
    analyzer = OpenAINewsAnalyzer(client=client)

    out, meta = asyncio.run(
        analyzer.analyze({"title": "삼성전자 테스트", "description": "한글 설명", "publisher": "테스트신문"})
    )

    assert len(client.responses.calls) == 1
    kwargs = client.responses.calls[0]
    assert kwargs["model"] == "gpt-5.4-mini"
    assert isinstance(kwargs["input"], list)
    assert not isinstance(kwargs["input"], dict)
    assert kwargs["text_format"] == NewsAnalysisOutput

    for item in kwargs["input"]:
        assert set(item) == {"role", "content"}
        assert isinstance(item["role"], str)
        assert isinstance(item["content"], str)

    user_content = kwargs["input"][1]["content"]
    payload = json.loads(user_content)
    assert payload == {"title": "삼성전자 테스트", "description": "한글 설명", "publisher": "테스트신문"}
    assert "한글 설명" in user_content
    assert "raw_data" not in user_content
    assert out.summary == "테스트 분석 결과입니다."
    assert meta["tokens"] == {"input": 1, "output": 2, "total": 3}
    assert meta["openai_request_id"] == "resp_test"


def test_openai_output_parsed_none_raises_validation_error(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = SimpleNamespace(status="completed", output_parsed=None, usage=None, latency_ms=None)
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    with pytest.raises(OpenAIValidationError):
        asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (SimpleNamespace(status="incomplete", output_parsed=_output(), incomplete_details={"reason": "max_tokens"}), "incomplete"),
        (SimpleNamespace(status="refused", output_parsed=_output()), "refused"),
    ],
)
def test_openai_incomplete_or_refused_raises_validation_error(monkeypatch, response, expected_status):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    with pytest.raises(OpenAIValidationError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))
    assert exc_info.value.status == expected_status


def test_openai_pydantic_validation_error_is_converted(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=_completed_response(output_parsed={"event_type": "bad_type"})))

    with pytest.raises(OpenAIValidationError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))
    assert exc_info.value.error_code == "OPENAI_SCHEMA_VALIDATION_ERROR"


def test_openai_usage_can_be_missing(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = SimpleNamespace(status="completed", output_parsed=_output(), usage=None, latency_ms=None)
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    _, meta = asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))
    assert meta["tokens"] == {"input": None, "output": None, "total": None}


def test_completed_with_output_parsed_success_includes_diagnostics(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = _response(
        output_parsed=_output(),
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
        usage=SimpleNamespace(input_tokens=11, output_tokens=22, total_tokens=33),
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    out, meta = asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))

    assert out.summary == _output()["summary"]
    assert meta["diagnostics"]["response_id"] == "resp_diag"
    assert meta["diagnostics"]["response_status"] == "completed"
    assert meta["diagnostics"]["output_item_types"] == ["message"]
    assert meta["diagnostics"]["content_item_types"] == ["output_text"]
    assert meta["diagnostics"]["has_refusal"] is False
    assert meta["diagnostics"]["has_output_parsed"] is True
    assert meta["tokens"] == {"input": 11, "output": 22, "total": 33}


def test_completed_output_text_json_fallback_success(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    output_text = json.dumps(_output(summary="json fallback"), ensure_ascii=False)
    response = _response(
        output_parsed=None,
        output_text=output_text,
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    out, meta = asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))

    assert out.summary == "json fallback"
    assert meta["diagnostics"]["has_output_parsed"] is False
    assert meta["diagnostics"]["has_output_text"] is True
    assert meta["diagnostics"]["output_text_length"] == len(output_text)


def test_completed_output_text_bad_json_raises_parse_error(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = _response(
        output_parsed=None,
        output_text="{not valid json",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    with pytest.raises(OpenAIParseError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "article secret", "description": "body secret", "publisher": "safe"}))
    assert exc_info.value.error_code == "OPENAI_JSON_PARSE_ERROR"
    assert exc_info.value.diagnostic_context["has_output_text"] is True
    assert "article secret" not in str(exc_info.value.diagnostic_context)
    assert "body secret" not in str(exc_info.value.diagnostic_context)


def test_openai_sdk_exception_with_response_is_diagnosed(monkeypatch):
    class FakeOpenAIError(Exception):
        pass

    exc = FakeOpenAIError("sdk parser failed with secret-token-test")
    exc.response = _response(
        output_parsed=None,
        output_text="{not valid json",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
    )
    monkeypatch.setattr(openai_news_analyzer, "OpenAIError", FakeOpenAIError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=exc))

    with pytest.raises(OpenAIParseError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "article secret", "description": "body secret", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_JSON_PARSE_ERROR"
    assert exc_info.value.diagnostic_context["response_status"] == "completed"
    assert exc_info.value.diagnostic_context["has_output_parsed"] is False
    assert exc_info.value.diagnostic_context["has_output_text"] is True
    assert "secret-token-test" not in str(exc_info.value)
    assert "article secret" not in str(exc_info.value.diagnostic_context)


def test_incomplete_max_output_tokens_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = _response(
        status="incomplete",
        incomplete_reason="max_output_tokens",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
        usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    with pytest.raises(OpenAIMaxOutputTokensError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))
    assert exc_info.value.error_code == "OPENAI_MAX_OUTPUT_TOKENS"
    assert exc_info.value.diagnostic_context["incomplete_reason"] == "max_output_tokens"


def test_refusal_response_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = _response(
        output_parsed=None,
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="omitted")])],
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    with pytest.raises(OpenAIRefusalError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))
    assert exc_info.value.error_code == "OPENAI_REFUSAL"
    assert exc_info.value.diagnostic_context["has_refusal"] is True


def test_empty_response_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=_response(output_parsed=None)))

    with pytest.raises(OpenAIEmptyResponseError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))
    assert exc_info.value.error_code == "OPENAI_EMPTY_OUTPUT"


def test_usage_partial_none_is_preserved(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    response = _response(
        output_parsed=_output(),
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
        usage=SimpleNamespace(input_tokens=10, output_tokens=None, total_tokens=None),
    )
    analyzer = OpenAINewsAnalyzer(client=FakeClient(response=response))

    _, meta = asyncio.run(analyzer.analyze({"title": "safe title", "description": "safe desc", "publisher": "safe"}))

    assert meta["tokens"] == {"input": 10, "output": None, "total": None}


def test_openai_bad_request_raises_invalid_request_error(monkeypatch):
    class FakeBadRequestError(Exception):
        pass

    monkeypatch.setattr(openai_news_analyzer, "BadRequestError", FakeBadRequestError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings(api_key="secret-token-test"))
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeBadRequestError("invalid type for input secret-token-test")))

    with pytest.raises(OpenAIInvalidRequestError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))
    assert exc_info.value.error_code == "OPENAI_INVALID_REQUEST"
    assert "secret-token-test" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("exception_name", "message"),
    [
        ("AuthenticationError", "auth failed"),
        ("RateLimitError", "rate limited"),
        ("APITimeoutError", "timeout"),
    ],
)
def test_openai_response_errors_are_converted(monkeypatch, exception_name, message):
    class FakeOpenAIError(Exception):
        pass

    monkeypatch.setattr(openai_news_analyzer, exception_name, FakeOpenAIError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeOpenAIError(message)))

    with pytest.raises(OpenAIResponseError):
        asyncio.run(analyzer.analyze({"title": "테스트", "description": "설명", "publisher": "신문"}))


def test_pydantic_validation_error_from_parse_is_distinct(monkeypatch):
    try:
        NewsAnalysisOutput.model_validate({"event_type": "bad_type"})
    except OpenAIValidationError:
        raise
    except Exception as exc:
        validation_exc = exc

    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=validation_exc))

    with pytest.raises(OpenAIValidationError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_SCHEMA_VALIDATION_ERROR"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "ValidationError"


def test_json_decode_error_from_parse_is_distinct(monkeypatch):
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=JSONDecodeError("secret output", "{}", 0)))

    with pytest.raises(OpenAIParseError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_JSON_PARSE_ERROR"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "JSONDecodeError"
    assert "secret output" not in str(exc_info.value.diagnostic_context)


def test_api_connection_error_is_distinct(monkeypatch):
    class FakeAPIConnectionError(Exception):
        request_id = "req_conn"

    sleep_calls: list[float] = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(openai_news_analyzer, "APIConnectionError", FakeAPIConnectionError)
    monkeypatch.setattr(openai_news_analyzer.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(openai_news_analyzer.random, "uniform", lambda _start, _end: 0.1)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeAPIConnectionError("network secret")))

    with pytest.raises(OpenAIConnectionError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_CONNECTION_ERROR"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeAPIConnectionError"
    assert exc_info.value.diagnostic_context["request_id"] == "req_conn"
    assert exc_info.value.diagnostic_context["retry_attempts"] == 2
    assert exc_info.value.diagnostic_context["timeout"] == {
        "connect": 10.0,
        "read": 60.0,
        "write": 20.0,
        "pool": 10.0,
    }
    assert sleep_calls == [1.1]
    assert "network secret" not in str(exc_info.value.diagnostic_context)


def test_api_connection_error_retry_success(monkeypatch):
    class FakeAPIConnectionError(Exception):
        pass

    sleep_calls: list[float] = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    class RetryResponses:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def parse(self, **kwargs: Any):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise FakeAPIConnectionError("temporary network secret")
            return _completed_response()

    monkeypatch.setattr(openai_news_analyzer, "APIConnectionError", FakeAPIConnectionError)
    monkeypatch.setattr(openai_news_analyzer.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(openai_news_analyzer.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    client = SimpleNamespace(responses=RetryResponses())
    analyzer = OpenAINewsAnalyzer(client=client)

    out, _ = asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert out.summary == _output()["summary"]
    assert len(client.responses.calls) == 2
    assert sleep_calls == [1.0]


def test_api_timeout_error_is_distinct(monkeypatch):
    class FakeAPITimeoutError(Exception):
        request_id = "req_timeout"

    monkeypatch.setattr(openai_news_analyzer, "APITimeoutError", FakeAPITimeoutError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeAPITimeoutError("timeout secret")))

    with pytest.raises(OpenAITimeoutError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_TIMEOUT_ERROR"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeAPITimeoutError"
    assert exc_info.value.diagnostic_context["request_id"] == "req_timeout"


def test_length_finish_reason_error_is_max_tokens(monkeypatch):
    class FakeLengthFinishReasonError(Exception):
        pass

    monkeypatch.setattr(openai_news_analyzer, "LengthFinishReasonError", FakeLengthFinishReasonError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeLengthFinishReasonError("too long secret")))

    with pytest.raises(OpenAIMaxOutputTokensError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_MAX_OUTPUT_TOKENS"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeLengthFinishReasonError"
    assert exc_info.value.diagnostic_context["incomplete_reason"] == "max_output_tokens"


def test_content_filter_finish_reason_error_is_refusal(monkeypatch):
    class FakeContentFilterFinishReasonError(Exception):
        pass

    monkeypatch.setattr(openai_news_analyzer, "ContentFilterFinishReasonError", FakeContentFilterFinishReasonError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeContentFilterFinishReasonError("filtered secret")))

    with pytest.raises(OpenAIRefusalError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_REFUSAL"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeContentFilterFinishReasonError"
    assert exc_info.value.diagnostic_context["has_refusal"] is True


def test_custom_analyzer_error_is_not_overwritten(monkeypatch):
    original = OpenAIParseError(
        OpenAIParseError.user_message,
        status="completed",
        diagnostics={"response_status": "completed", "has_output_text": True},
    )
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings())
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=original))

    with pytest.raises(OpenAIParseError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value is original
    assert exc_info.value.error_code == "OPENAI_JSON_PARSE_ERROR"


def test_openai_insufficient_quota_is_not_retried(monkeypatch):
    class FakeRateLimitError(Exception):
        status_code = 429
        code = "insufficient_quota"

    monkeypatch.setattr(openai_news_analyzer, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings(max_retries=3))
    client = FakeClient(exc=FakeRateLimitError("quota response contains secret-token-test"))
    analyzer = OpenAINewsAnalyzer(client=client)

    with pytest.raises(OpenAIQuotaExceededError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert len(client.responses.calls) == 1
    assert exc_info.value.error_code == "OPENAI_QUOTA_ERROR"
    assert exc_info.value.diagnostic_context["http_status_code"] == 429
    assert exc_info.value.diagnostic_context["openai_error_code"] == "insufficient_quota"
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeRateLimitError"
    assert exc_info.value.diagnostic_context["original_error_code"] == "insufficient_quota"
    assert "secret-token-test" not in str(exc_info.value)


def test_openai_rate_limit_is_retried_with_backoff(monkeypatch):
    class FakeRateLimitError(Exception):
        status_code = 429
        code = "rate_limit_exceeded"

    sleep_calls: list[int] = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(openai_news_analyzer, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(openai_news_analyzer.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings(max_retries=2))

    class RetryResponses:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def parse(self, **kwargs: Any):
            self.calls.append(kwargs)
            if len(self.calls) < 3:
                raise FakeRateLimitError("rate limited")
            return _completed_response()

    client = SimpleNamespace(responses=RetryResponses())
    analyzer = OpenAINewsAnalyzer(client=client)

    out, _ = asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert out.summary == _output()["summary"]
    assert len(client.responses.calls) == 3
    assert sleep_calls == [1, 2]


def test_openai_rate_limit_without_code_defaults_to_rate_limit(monkeypatch):
    class FakeRateLimitError(Exception):
        status_code = 429

    monkeypatch.setattr(openai_news_analyzer, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(openai_news_analyzer, "get_settings", lambda: _settings(max_retries=0))
    analyzer = OpenAINewsAnalyzer(client=FakeClient(exc=FakeRateLimitError("headers and body omitted")))

    with pytest.raises(OpenAIRateLimitError) as exc_info:
        asyncio.run(analyzer.analyze({"title": "safe", "description": "safe", "publisher": "safe"}))

    assert exc_info.value.error_code == "OPENAI_RATE_LIMIT_ERROR"
    assert exc_info.value.diagnostic_context["http_status_code"] == 429
    assert exc_info.value.diagnostic_context["openai_error_code"] is None
    assert exc_info.value.diagnostic_context["original_exception_type"] == "FakeRateLimitError"


def test_news_analysis_run_counts_match_processed_articles(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        def __init__(self):
            self.calls = 0

        async def analyze(self, article_input: dict[str, Any]):
            self.calls += 1
            if self.calls == 2:
                raise OpenAIInvalidRequestError(OpenAIInvalidRequestError.user_message)
            return NewsAnalysisOutput.model_validate(_output(summary="정상 완료")), {
                "tokens": {"input": 1, "output": 2, "total": 3},
                "latency_ms": 10,
                "openai_request_id": "resp_ok",
            }

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        db.add(_article("1"))
        db.add(_article("2"))
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=2, provider="openai")

        assert result["requested"] == 2
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert result["skipped"] == 0
        assert result["requested"] == result["completed"] + result["failed"] + result["skipped"]
        assert len(list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=10)) == 1


def test_run_analysis_reuses_one_provider_and_processes_sequentially(monkeypatch, sqlite_session_local):
    instances = 0
    active = 0
    max_active = 0

    class FakeAnalyzer:
        def __init__(self):
            nonlocal instances
            instances += 1

        async def analyze(self, article_input: dict[str, Any]):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return NewsAnalysisOutput.model_validate(_output(summary="sequential")), {
                "tokens": {"input": 1, "output": 2, "total": 3},
                "latency_ms": 10,
                "openai_request_id": "resp_seq",
            }

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        db.add(_article("seq-1"))
        db.add(_article("seq-2"))
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=2, provider="openai")

        assert result["completed"] == 2
        assert instances == 1
        assert max_active == 1


def test_connection_error_center_context_records_retry_attempts(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, article_input: dict[str, Any]):
            raise OpenAIConnectionError(
                OpenAIConnectionError.user_message,
                diagnostics={
                    "original_exception_type": "FakeAPIConnectionError",
                    "cause_exception_type": "ConnectError",
                    "context_exception_type": None,
                    "request_id": "req_retry",
                    "retry_attempts": 2,
                    "timeout": {"connect": 10.0, "read": 60.0, "write": 20.0, "pool": 10.0},
                },
            )

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        article = _article("connection-context")
        db.add(article)
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=1, provider="openai")
        error = db.query(ErrorLog).one()
        context = error.context_json

        assert result["failed"] == 1
        assert result["error_codes"] == ["OPENAI_CONNECTION_ERROR"]
        assert result["error_messages"] == [
            "일부 뉴스 분석 중 연결 오류가 발생했습니다. 실패한 뉴스는 다음 실행에서 다시 시도할 수 있습니다."
        ]
        assert context["original_exception_type"] == "FakeAPIConnectionError"
        assert context["cause_exception_type"] == "ConnectError"
        assert context["request_id"] == "req_retry"
        assert context["retry_attempts"] == 2
        assert context["timeout"]["read"] == 60.0
        assert context["input_json_length"] > 0
        assert "secret-token-test" not in str(context)


def test_error_center_context_contains_only_safe_openai_diagnostics(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, article_input: dict[str, Any]):
            raise OpenAIParseError(
                OpenAIParseError.user_message,
                status="completed",
                diagnostics={
                    "response_id": "resp_safe",
                    "response_status": "completed",
                    "incomplete_reason": None,
                    "output_item_types": ["message"],
                    "content_item_types": ["output_text"],
                    "has_refusal": False,
                    "has_output_parsed": False,
                    "has_output_text": True,
                    "output_text_length": 123,
                    "input_tokens": 10,
                    "output_tokens": None,
                    "total_tokens": None,
                    "original_exception_type": "FakeSDKParseError",
                    "original_exception_module": "tests.fake",
                    "original_error_code": "fake_code",
                    "original_error_type": "fake_type",
                    "original_param": "text.format",
                    "request_id": "req_safe",
                    "cause_exception_type": "ValidationError",
                    "context_exception_type": None,
                    "api_key": "secret-token-test",
                    "article": "article body secret",
                    "output_text": "full output secret",
                },
            )

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        article = _article("safe-context")
        article.title = "article title secret"
        article.description = "article body secret"
        db.add(article)
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=1, provider="openai")
        error = db.query(ErrorLog).one()
        context = error.context_json

        assert result["failed"] == 1
        assert result["error_codes"] == ["OPENAI_JSON_PARSE_ERROR"]
        assert result["error_messages"] == ["OpenAI 응답 JSON을 해석하지 못했습니다."]
        assert error.error_code == "OPENAI_JSON_PARSE_ERROR"
        assert context["news_article_id"] == article.id
        assert context["response_id"] == "resp_safe"
        assert context["response_status"] == "completed"
        assert context["output_item_types"] == ["message"]
        assert context["content_item_types"] == ["output_text"]
        assert context["has_output_parsed"] is False
        assert context["has_output_text"] is True
        assert context["output_text_length"] == 123
        assert context["input_tokens"] == 10
        assert context["output_tokens"] is None
        assert context["original_exception_type"] == "FakeSDKParseError"
        assert context["original_exception_module"] == "tests.fake"
        assert context["original_error_code"] == "fake_code"
        assert context["original_error_type"] == "fake_type"
        assert context["original_param"] == "text.format"
        assert context["request_id"] == "req_safe"
        assert context["cause_exception_type"] == "ValidationError"
        assert context["title_length"] == len("article title secret")
        assert context["description_length"] == len("article body secret")
        assert context["has_publisher"] is True
        assert context["has_published_at"] is False
        assert context["input_json_length"] > 0
        assert "api_key" not in context
        assert "article" not in context
        assert "output_text" not in context
        assert "secret-token-test" not in str(context)
        assert "article body secret" not in str(context)


def test_rate_limit_error_center_context_is_minimal(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, article_input: dict[str, Any]):
            err = OpenAIQuotaExceededError(
                OpenAIQuotaExceededError.user_message,
                http_status_code=429,
            )
            err.diagnostic_context.update(
                {
                    "http_status_code": 429,
                    "openai_error_code": "insufficient_quota",
                    "headers": {"authorization": "secret-token-test"},
                    "body": "full response body secret",
                }
            )
            raise err

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        article = _article("quota-context")
        db.add(article)
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=1, provider="openai")
        error = db.query(ErrorLog).one()
        context = error.context_json

        assert result["error_codes"] == ["OPENAI_QUOTA_ERROR"]
        assert result["error_messages"] == ["OpenAI API 사용 가능 잔액 또는 결제 한도를 확인하세요."]
        assert error.error_code == "OPENAI_QUOTA_ERROR"
        assert context["news_article_id"] == article.id
        assert context["model_name"] == "gpt-5.4-mini"
        assert context["prompt_version"] == "news-analysis-v1"
        assert context["http_status_code"] == 429
        assert context["openai_error_code"] == "insufficient_quota"
        assert "secret-token-test" not in str(context)
        assert "full response body secret" not in str(context)


def test_list_unanalyzed_news_selects_news_without_analysis(sqlite_session_local):
    with sqlite_session_local() as db:
        article = _article("no-analysis")
        db.add(article)
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert [row.id for row in rows] == [article.id]


def test_list_unanalyzed_news_excludes_completed_same_model_and_prompt(sqlite_session_local):
    with sqlite_session_local() as db:
        article = _article("completed")
        db.add(article)
        db.commit()
        db.add(_analysis(article, "completed"))
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert rows == []


@pytest.mark.parametrize("status", ["failed", "pending", "skipped"])
def test_list_unanalyzed_news_reselects_non_completed_statuses(sqlite_session_local, status):
    with sqlite_session_local() as db:
        article = _article(status)
        db.add(article)
        db.commit()
        db.add(_analysis(article, status))
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert [row.id for row in rows] == [article.id]


def test_completed_other_model_does_not_block_current_model(sqlite_session_local):
    with sqlite_session_local() as db:
        article = _article("other-model")
        db.add(article)
        db.commit()
        db.add(_analysis(article, "completed", model_name="other-model"))
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert [row.id for row in rows] == [article.id]


def test_completed_other_prompt_does_not_block_current_prompt(sqlite_session_local):
    with sqlite_session_local() as db:
        article = _article("other-prompt")
        db.add(article)
        db.commit()
        db.add(_analysis(article, "completed", prompt_version="other-prompt"))
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert [row.id for row in rows] == [article.id]


def test_list_unanalyzed_news_applies_limit_after_completed_filter(sqlite_session_local):
    with sqlite_session_local() as db:
        completed_article = _article("newer-completed")
        failed_article = _article("older-failed")
        db.add(completed_article)
        db.add(failed_article)
        db.commit()
        completed_article.available_at = datetime.now(timezone.utc)
        failed_article.available_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        db.add(_analysis(completed_article, "completed"))
        db.add(_analysis(failed_article, "failed"))
        db.commit()

        rows = list_unanalyzed_news(db, "gpt-5.4-mini", "news-analysis-v1", limit=1)

        assert [row.id for row in rows] == [failed_article.id]


def test_reanalysis_success_updates_failed_row_without_unique_error(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, article_input: dict[str, Any]):
            return NewsAnalysisOutput.model_validate(_output(summary="retry completed")), {
                "tokens": {"input": 1, "output": 2, "total": 3},
                "latency_ms": 10,
                "openai_request_id": "resp_retry",
            }

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        article = _article("retry")
        db.add(article)
        db.commit()
        db.add(_analysis(article, "failed"))
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=1, provider="openai")
        analyses = db.query(NewsAnalysis).filter(NewsAnalysis.news_article_id == article.id).all()

        assert result["requested"] == 1
        assert result["completed"] == 1
        assert result["failed"] == 0
        assert len(analyses) == 1
        assert analyses[0].status == "completed"
        assert analyses[0].summary == "retry completed"


def test_news_analysis_endpoint_returns_empty_result_for_zero_targets(client: TestClient):
    response = client.post("/news-analysis/run", params={"provider": "mock", "limit": 1})

    assert response.status_code == 200
    assert response.json()["requested"] == 0
    assert response.json()["completed"] == 0
    assert response.json()["failed"] == 0
    assert response.json()["skipped"] == 0


def test_news_analysis_test_one_endpoint_success_with_mock(client: TestClient, sqlite_session_local):
    with sqlite_session_local() as db:
        db.add(_article("test-one"))
        db.commit()

    response = client.post("/news-analysis/test-one", params={"provider": "mock"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed"] is True
    assert "summary_preview" in body


def test_news_analysis_early_stops_repeated_schema_errors(monkeypatch, sqlite_session_local):
    class FakeAnalyzer:
        async def analyze(self, article_input: dict[str, Any]):
            raise OpenAIValidationError(
                OpenAIValidationError.user_message,
                status="completed",
                diagnostics={"response_status": "completed", "validation_error_field": ["event_type"]},
            )

    monkeypatch.setattr(news_analysis_service, "OpenAINewsAnalyzer", FakeAnalyzer)
    with sqlite_session_local() as db:
        for index in range(10):
            db.add(_article(f"schema-stop-{index}"))
        db.commit()

        result = news_analysis_service.run_analysis(db, limit=10, provider="openai")

    assert result["failed"] == 5
    assert result["skipped"] == 5
    assert result["early_stopped"] is True
    assert result["error_codes"] == ["OPENAI_SCHEMA_VALIDATION_ERROR"]


def test_streamlit_zero_target_result_is_not_success(monkeypatch):
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(dashboard.st, "info", lambda message: calls.append(("info", message)))
    monkeypatch.setattr(dashboard.st, "success", lambda message: calls.append(("success", message)))
    monkeypatch.setattr(dashboard.st, "json", lambda data: calls.append(("json", data)))

    dashboard.render_ai_analysis_result({"requested": 0, "completed": 0, "failed": 0, "skipped": 0})

    assert ("info", "현재 분석 가능한 미분석 뉴스가 없습니다.") in calls
    assert not any(kind == "success" for kind, _ in calls)


def test_streamlit_failed_result_shows_safe_error_message(monkeypatch):
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(dashboard.st, "info", lambda message: calls.append(("info", message)))
    monkeypatch.setattr(dashboard.st, "warning", lambda message: calls.append(("warning", message)))
    monkeypatch.setattr(dashboard.st, "success", lambda message: calls.append(("success", message)))
    monkeypatch.setattr(dashboard.st, "json", lambda data: calls.append(("json", data)))

    dashboard.render_ai_analysis_result(
        {
            "requested": 1,
            "completed": 0,
            "failed": 1,
            "skipped": 0,
            "error_codes": ["OPENAI_MAX_OUTPUT_TOKENS"],
            "error_messages": ["출력이 중간에 중단됐습니다."],
        }
    )

    assert ("warning", "출력이 중간에 중단됐습니다.") in calls
    assert not any(kind == "success" for kind, _ in calls)
