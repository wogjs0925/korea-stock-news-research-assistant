from __future__ import annotations

STATUS_LABELS = {
    "completed": "완료",
    "partial": "부분 완료",
    "failed": "실패",
    "insufficient_data": "데이터 부족",
    "insufficient_candidates": "선정 후보 부족",
    "running": "실행 중",
    "pending": "대기",
    "skipped": "건너뜀",
}

ASSET_TYPE_LABELS = {"stock": "주식", "etf": "ETF"}
MATCH_STATUS_LABELS = {"matched": "매칭됨", "ambiguous": "매칭 불확실", "unmatched": "미매칭", "rejected": "제외"}
IMPACT_DIRECTION_LABELS = {"positive": "긍정", "negative": "부정", "mixed": "혼재", "neutral": "중립"}
TIME_HORIZON_LABELS = {"intraday": "당일", "short_term": "단기", "medium_term": "중기", "long_term": "장기", "unknown": "알 수 없음"}

RISK_FLAG_LABELS = {
    "leveraged_etf": "레버리지 ETF",
    "inverse_etf": "인버스 ETF",
    "not_recommendation_eligible": "관심 후보 제외 증권",
    "inactive_security": "비활성 종목",
    "weak_industry_candidate": "약한 산업 기반 후보",
}

EXCLUSION_FLAG_LABELS = {
    "low_candidate_score": "후보 점수 낮음",
    "insufficient_evidence": "근거 부족",
    "ambiguous_match": "매칭 불확실",
    "unmatched_match": "미매칭",
    "rejected_match": "제외된 매칭",
    "missing_security": "종목 마스터 없음",
    "inactive_security": "비활성 종목",
    "recommendation_excluded_security": "관심 후보 제외 증권",
    "unsupported_asset_type": "지원하지 않는 자산 유형",
    "unsupported_country": "지원하지 않는 국가",
    "leveraged_etf_default_excluded": "레버리지 ETF 기본 제외",
    "inverse_etf_default_excluded": "인버스 ETF 기본 제외",
    "weak_industry_candidate": "약한 산업 기반 후보",
    "duplicate_company": "중복 기업",
    "too_broad_etf": "테마 관련성이 넓은 ETF",
    "lower_ranked_alternative": "상위 후보 우선",
}

STAGE_LABELS = {
    "news_analysis": "뉴스 분석",
    "theme_analysis": "테마 분석",
    "candidate_generation": "후보 생성",
    "recommendations": "관심 후보 선정",
}

EXCLUSION_FLAG_LABELS.update(
    {
        "overseas_reference_stock": "해외 참고 주식",
        "outside_stock_country_scope": "선택 범위 밖의 주식",
    }
)


def label_value(value: str | None, mapping: dict[str, str]) -> str:
    if value is None:
        return "-"
    return mapping.get(str(value), str(value))


def label_list(values: list[str] | None, mapping: dict[str, str]) -> list[str]:
    return [label_value(value, mapping) for value in values or []]
