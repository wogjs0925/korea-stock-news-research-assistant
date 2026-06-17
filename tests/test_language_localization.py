from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from app import dashboard
from app.providers.ai import openai_news_analyzer, openai_theme_analyzer
from app.providers.ai.mock_theme_analyzer import MockThemeAnalyzer
from app.utils.display_labels import (
    EXCLUSION_FLAG_LABELS,
    RISK_FLAG_LABELS,
    STATUS_LABELS,
    label_list,
    label_value,
)


def _has_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def test_news_prompt_contains_korean_output_instruction():
    prompt = openai_news_analyzer.SYSTEM_PROMPT

    assert "한국어" in prompt
    assert "ticker" in prompt
    assert "공식 회사명" in prompt
    assert "candidate_themes" in prompt


def test_theme_prompt_contains_korean_output_instruction():
    prompt = openai_theme_analyzer.SYSTEM_PROMPT

    assert "한국어" in prompt
    assert "theme_name" in prompt
    assert "영어만으로 쓰지 말고" in prompt
    assert "거래소 코드" in prompt


def test_mock_theme_output_is_not_english_only():
    analyzer = MockThemeAnalyzer()
    output, _meta = asyncio.run(
        analyzer.analyze(
            [
                {"news_analysis_id": 1, "companies": ["NVIDIA"]},
                {"news_analysis_id": 2, "companies": ["Apple"]},
            ],
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )
    )

    assert output.themes
    assert _has_korean(output.themes[0].theme_name)
    assert _has_korean(output.themes[0].theme_summary)
    assert _has_korean(output.themes[0].risk_factors[0])


def test_ui_status_risk_and_exclusion_labels_are_korean():
    assert label_value("completed", STATUS_LABELS) == "완료"
    assert label_value("partial", STATUS_LABELS) == "부분 완료"
    assert label_list(["leveraged_etf", "inverse_etf"], RISK_FLAG_LABELS) == ["레버리지 ETF", "인버스 ETF"]
    assert label_list(["insufficient_evidence", "ambiguous_match", "low_candidate_score"], EXCLUSION_FLAG_LABELS) == [
        "근거 부족",
        "매칭 불확실",
        "후보 점수 낮음",
    ]


def test_localized_rows_keep_ticker_and_exchange_code_raw():
    rows = dashboard._localized_recommendation_rows(
        [
            {
                "rank": 1,
                "security_name": "NVIDIA Corporation",
                "ticker": "NVDA",
                "country_code": "US",
                "exchange_code": "XNAS",
                "asset_type": "stock",
                "final_score": 0.8,
                "selection_reason": "AI 인프라 테마와 관련된 후보입니다.",
                "evidence_summary": "근거 요약",
                "risk_flags": ["leveraged_etf"],
                "exclusion_flags": ["insufficient_evidence"],
            }
        ]
    )

    assert rows[0]["ticker/종목코드"] == "NVDA"
    assert rows[0]["거래소"] == "XNAS"
    assert rows[0]["종목명"] == "NVIDIA Corporation"
    assert rows[0]["자산 유형"] == "주식"
    assert rows[0]["위험 플래그"] == "레버리지 ETF"
    assert rows[0]["제외 사유"] == "근거 부족"


def test_forbidden_korean_phrases_not_in_user_facing_docs_or_prompts():
    combined = "\n".join(
        [
            openai_news_analyzer.SYSTEM_PROMPT,
            openai_theme_analyzer.SYSTEM_PROMPT,
            Path(dashboard.__file__).read_text(encoding="utf-8"),
            Path("README.md").read_text(encoding="utf-8"),
            Path("AGENTS.md").read_text(encoding="utf-8"),
        ]
    )
    forbidden = ["매수" + " 추천", "무" + "조건", "확실히" + " 오른다", "수익" + " 보장"]
    for phrase in forbidden:
        assert phrase not in combined
