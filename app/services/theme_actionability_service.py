from __future__ import annotations

from typing import Any

PRICE_IMPACT_KEYWORDS = {
    "실적",
    "매출",
    "영업이익",
    "수주",
    "공급",
    "공급 차질",
    "계약",
    "투자",
    "증설",
    "감산",
    "원가",
    "비용",
    "마진",
    "수출",
    "원유",
    "유가",
    "천연가스",
    "반도체",
    "배터리",
    "데이터센터",
    "earnings",
    "revenue",
    "supply",
    "contract",
    "investment",
    "capacity",
    "cost",
    "margin",
    "export",
    "oil",
    "semiconductor",
    "실적",
    "매출",
    "영업이익",
    "수주",
    "공급",
    "계약",
    "투자",
    "증설",
    "감산",
    "원가",
    "비용",
    "마진",
    "수출",
    "원유",
    "유가",
    "천연가스",
    "반도체",
    "배터리",
    "AI",
    "데이터센터",
    "금리",
    "환율",
}

LOW_ACTIONABILITY_KEYWORDS = {
    "공모주 배정 실패",
    "공모주 배정",
    "배정 실패",
    "투자자 배정 논란",
    "불공정거래",
    "시장 공정성",
    "공정성",
    "금감원",
    "조사",
    "단속",
    "법적 불확실성",
    "제도",
    "규제",
    "논란",
    "ipo allocation",
    "allocation dispute",
    "market fairness",
    "enforcement",
    "legal uncertainty",
    "regulation",
    "비상장",
    "private company",
    "배정 실패",
    "배정 논란",
    "불공정거래",
    "시장 공정성",
    "공정성",
    "금감원",
    "조사",
    "단속",
    "법적 불확실성",
    "제도",
    "규제",
    "논란",
}

RISK_ALERT_KEYWORDS = {
    "불공정거래",
    "시장 공정성",
    "단속",
    "조사",
    "법적",
    "분쟁",
    "소송",
    "제재",
    "공모주 배정 실패",
    "공모주 배정",
    "규제 리스크",
    "enforcement",
    "investigation",
    "lawsuit",
    "sanction",
    "allocation dispute",
    "불공정거래",
    "단속",
    "조사",
    "법적",
    "분쟁",
    "소송",
    "제재",
    "배정 실패",
    "배정 논란",
    "규제 리스크",
}

INVESTABLE_INDUSTRY_KEYWORDS = {
    "반도체",
    "AI 인프라",
    "데이터센터",
    "배터리",
    "2차전지",
    "자동차",
    "전기차",
    "조선",
    "방산",
    "에너지",
    "원유",
    "정유",
    "천연가스",
    "바이오",
    "증권",
    "은행",
    "보험",
    "건설",
    "철강",
    "화학",
    "우주항공",
    "semiconductor",
    "ai infrastructure",
    "datacenter",
    "battery",
    "energy",
    "oil",
    "defense",
    "shipbuilding",
    "반도체",
    "AI 인프라",
    "데이터센터",
    "배터리",
    "2차전지",
    "자동차",
    "전기차",
    "조선",
    "방산",
    "에너지",
    "원유",
    "정유",
    "천연가스",
    "바이오",
    "증권",
    "은행",
    "보험",
    "건설",
    "철강",
    "화학",
    "항공우주",
    "ETF",
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def _flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_flatten(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_flatten(item))
        return result
    return [str(value)]


def _text(*parts: Any) -> str:
    return " ".join(_flatten(list(parts))).lower()


def score_news_selection(source: dict[str, Any]) -> dict[str, float]:
    text = _text(
        source.get("title"),
        source.get("summary"),
        source.get("event_type"),
        source.get("candidate_themes"),
        source.get("companies"),
        source.get("evidence_points"),
        source.get("risk_factors"),
    )
    market = _clamp(float(source.get("market_relevance_score") or 0.0))
    price = 0.15
    price += 0.12 * min(5, sum(1 for keyword in PRICE_IMPACT_KEYWORDS if keyword.lower() in text))
    if source.get("impact_direction") in {"positive", "negative", "mixed"}:
        price += 0.15
    if source.get("event_type") in {"earnings", "contract", "investment", "product", "technology", "merger_acquisition", "financing"}:
        price += 0.15
    if any(keyword.lower() in text for keyword in LOW_ACTIONABILITY_KEYWORDS):
        price -= 0.30

    investable = 0.10
    if source.get("companies"):
        investable += 0.25
    investable += 0.10 * min(4, sum(1 for keyword in INVESTABLE_INDUSTRY_KEYWORDS if keyword.lower() in text))
    if source.get("candidate_themes"):
        investable += 0.15
    if any(keyword.lower() in text for keyword in LOW_ACTIONABILITY_KEYWORDS):
        investable -= 0.25

    price = _clamp(price)
    investable = _clamp(investable)
    final = _clamp(market * 0.3 + price * 0.4 + investable * 0.3)
    return {
        "market_relevance_score": market,
        "price_impact_score": price,
        "investable_link_score": investable,
        "final_news_selection_score": final,
    }


def score_theme_actionability(
    *,
    theme_name: str,
    theme_summary: str,
    why_now: str,
    impact_direction: str,
    issue_tags: list[str],
    direct_impact_industries: list[str],
    market_theme_tags: list[str],
    candidate_search_tags: list[str],
    related_companies: list[str],
    evidence_count: int,
    source_scores: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    text = _text(theme_name, theme_summary, why_now, issue_tags, direct_impact_industries, market_theme_tags, candidate_search_tags, related_companies)
    avg_price = sum(item.get("price_impact_score", 0.0) for item in source_scores or []) / max(1, len(source_scores or []))
    avg_investable = sum(item.get("investable_link_score", 0.0) for item in source_scores or []) / max(1, len(source_scores or []))

    price = max(avg_price, 0.15)
    price += 0.10 * min(4, sum(1 for keyword in PRICE_IMPACT_KEYWORDS if keyword.lower() in text))
    if impact_direction in {"positive", "negative", "mixed"}:
        price += 0.10
    if any(keyword.lower() in text for keyword in LOW_ACTIONABILITY_KEYWORDS):
        price -= 0.25

    investable = max(avg_investable, 0.10)
    if related_companies:
        investable += 0.20
    investable += 0.08 * min(5, len(direct_impact_industries) + len(market_theme_tags))
    if candidate_search_tags:
        investable += 0.20
    if any(keyword.lower() in text for keyword in LOW_ACTIONABILITY_KEYWORDS):
        investable -= 0.25

    price = _clamp(price)
    investable = _clamp(investable)
    actionability = _clamp(price * 0.45 + investable * 0.40 + min(evidence_count, 5) / 5 * 0.15)
    reasons: list[str] = []

    if impact_direction == "negative" or any(keyword.lower() in text for keyword in RISK_ALERT_KEYWORDS):
        bucket = "risk_alert"
        reasons.append("risk_or_regulatory_issue")
    elif actionability >= 0.62 and price >= 0.55 and investable >= 0.50:
        bucket = "investable_opportunity"
    elif actionability >= 0.45 and price >= 0.40:
        bucket = "watchlist"
        reasons.append("moderate_price_impact_or_link")
    elif price < 0.35 and investable < 0.35:
        bucket = "low_actionability"
        reasons.append("low_price_impact_and_investable_link")
    else:
        bucket = "macro_background"
        reasons.append("market_context_without_direct_candidate_link")

    is_investable = bucket == "investable_opportunity"
    return {
        "actionability_score": actionability,
        "price_impact_score": price,
        "investable_link_score": investable,
        "is_investable_theme": is_investable,
        "theme_bucket": bucket,
        "theme_bucket_reason": ", ".join(reasons) if reasons else None,
    }
