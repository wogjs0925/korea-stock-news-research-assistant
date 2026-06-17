from __future__ import annotations

import re
from typing import Any

from app.schemas.theme_analysis import EntityBusinessIndustryItem, SelectedThemeCandidate

ISSUE_TAG_RULES: dict[str, list[str]] = {
    "IPO": ["ipo", "상장", "기업공개", "공모주"],
    "공모주": ["공모주", "공모", "청약"],
    "실적": ["실적", "매출", "영업이익", "순이익"],
    "금리": ["금리", "기준금리", "금리 인하", "금리 인상"],
    "환율": ["환율", "원달러", "달러"],
    "수주": ["수주", "계약", "공급계약"],
    "공급계약": ["공급계약", "장기공급", "납품"],
    "규제": ["규제", "제재", "허가", "승인"],
    "관세": ["관세", "무역장벽"],
    "지정학 리스크": ["지정학", "전쟁", "분쟁", "중동", "미중 갈등"],
    "신제품 출시": ["출시", "신제품", "공개"],
    "인수합병": ["인수", "합병", "m&a"],
    "투자 유치": ["투자 유치", "자금 조달", "펀딩"],
    "배당": ["배당"],
    "자사주": ["자사주"],
    "반도체 수출": ["반도체 수출", "수출 규제"],
    "원자재 가격": ["원자재", "유가", "구리", "천연가스"],
}

INDUSTRY_RULES: dict[str, list[str]] = {
    "증권": ["증권", "브로커리지", "투자은행", "ib", "공모주", "ipo"],
    "은행": ["은행", "대출", "예대마진"],
    "보험": ["보험", "손해율"],
    "반도체": ["반도체", "gpu", "hbm", "파운드리", "메모리", "chip", "semiconductor"],
    "자동차": ["자동차", "전기차", "완성차", "모빌리티"],
    "배터리": ["배터리", "2차전지", "양극재", "음극재"],
    "조선": ["조선", "선박", "lng선"],
    "방산": ["방산", "방위산업", "무기", "미사일"],
    "에너지": ["에너지", "원유", "정유", "천연가스", "전력"],
    "바이오": ["바이오", "제약", "임상", "신약"],
    "게임": ["게임", "콘텐츠"],
    "플랫폼": ["플랫폼", "커머스", "광고"],
    "건설": ["건설", "부동산", "인프라"],
    "화학": ["화학", "석유화학"],
    "철강": ["철강", "강재"],
    "통신": ["통신", "5g", "통신사"],
    "항공우주": ["항공우주", "우주항공", "우주산업", "위성", "발사체", "로켓", "space", "aerospace", "satellite"],
    "AI 인프라": ["ai 인프라", "데이터센터", "gpu", "클라우드"],
}

MARKET_THEME_RULES: dict[str, list[str]] = {
    "AI 인프라": ["ai", "gpu", "데이터센터", "클라우드"],
    "데이터센터": ["데이터센터", "전력 수요", "냉각"],
    "반도체 공급망": ["반도체", "hbm", "파운드리", "수출 규제"],
    "우주항공": ["우주", "위성", "발사체", "로켓", "space"],
    "전기차": ["전기차", "ev", "자율주행"],
    "2차전지": ["배터리", "2차전지", "양극재"],
    "로봇": ["로봇", "자동화"],
    "원전": ["원전", "원자력"],
    "방산": ["방산", "방위산업"],
    "에너지 가격": ["유가", "원유", "천연가스", "에너지 가격"],
    "금리 인하": ["금리 인하", "기준금리 인하"],
    "공모주 시장": ["ipo", "공모주", "상장"],
    "해외 비상장 투자": ["비상장", "해외 투자", "투자 유치"],
    "미중 갈등": ["미중", "관세", "수출 규제"],
    "중동 리스크": ["중동", "지정학", "분쟁"],
}

ENTITY_ALIASES: dict[str, list[str]] = {
    "엔비디아": ["엔비디아", "nvidia", "nvda"],
    "테슬라": ["테슬라", "tesla", "tsla"],
    "스페이스X": ["스페이스x", "spacex", "space x"],
    "삼성전자": ["삼성전자"],
    "현대차": ["현대차", "현대자동차"],
    "미래에셋증권": ["미래에셋증권"],
}

ENTITY_INDUSTRIES: dict[str, list[str]] = {
    "엔비디아": ["반도체", "GPU", "AI 인프라", "데이터센터"],
    "테슬라": ["전기차", "배터리", "자율주행", "에너지 저장"],
    "스페이스X": ["항공우주", "우주산업", "위성", "발사체"],
    "삼성전자": ["반도체", "스마트폰", "가전", "디스플레이"],
    "현대차": ["자동차", "전기차", "수소차", "모빌리티"],
    "미래에셋증권": ["증권", "금융투자", "자산관리", "투자은행"],
}

SYNONYMS: dict[str, list[str]] = {
    "항공우주": ["우주항공", "우주산업", "위성", "발사체", "로켓", "aerospace", "space", "satellite"],
    "반도체": ["AI 반도체", "GPU", "HBM", "chip", "semiconductor"],
    "증권": ["금융투자", "브로커리지", "투자은행", "IB", "공모주", "IPO"],
    "에너지": ["원유", "정유", "천연가스", "oil", "energy", "crude"],
    "전기차": ["EV", "배터리", "자율주행", "모빌리티"],
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def _unique(values: list[str], limit: int = 30) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = _normalize(clean)
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _text_for_theme(theme: SelectedThemeCandidate, sources: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        theme.theme_name,
        theme.theme_summary,
        theme.why_now,
        " ".join(theme.related_industries),
        " ".join(theme.related_companies),
        " ".join(theme.risk_factors),
    ]
    evidence_ids = {item.news_analysis_id for item in theme.evidence}
    for source in sources:
        if int(source.get("news_analysis_id", -1)) in evidence_ids:
            parts.extend(
                [
                    str(source.get("title") or ""),
                    str(source.get("summary") or ""),
                    " ".join(str(item) for item in source.get("candidate_themes", []) if item),
                    " ".join(str(item) for item in source.get("companies", []) if item),
                ]
            )
    return _normalize(" ".join(parts))


def _matches(text: str, rules: dict[str, list[str]]) -> list[str]:
    return [tag for tag, keywords in rules.items() if any(_normalize(keyword) in text for keyword in keywords)]


def _entity_businesses(text: str, companies: list[str]) -> list[EntityBusinessIndustryItem]:
    rows: list[EntityBusinessIndustryItem] = []
    names = _unique(companies, limit=30)
    for entity, aliases in ENTITY_ALIASES.items():
        if entity in names or any(_normalize(alias) in text for alias in aliases):
            rows.append(
                EntityBusinessIndustryItem(
                    entity=entity,
                    industries=ENTITY_INDUSTRIES.get(entity, []),
                    confidence=0.85 if entity in names else 0.7,
                    reason="기업명 또는 별칭 기반 본업 산업 보강",
                )
            )
    known = {_normalize(row.entity) for row in rows}
    for name in names:
        if _normalize(name) not in known:
            rows.append(
                EntityBusinessIndustryItem(
                    entity=name,
                    industries=[],
                    confidence=0.35,
                    reason="본업 산업 추정 없음",
                )
            )
    return rows[:15]


def _expand_tags(*groups: list[str]) -> list[str]:
    tags: list[str] = []
    for group in groups:
        for tag in group:
            tags.append(tag)
            tags.extend(SYNONYMS.get(tag, []))
    return _unique(tags, limit=40)


def build_tag_confidence(theme: SelectedThemeCandidate) -> dict[str, float]:
    confidence: dict[str, float] = {}
    for tag in theme.issue_tags + theme.direct_impact_industries + theme.market_theme_tags:
        confidence.setdefault(tag, 0.75)
    for tag in theme.candidate_search_tags:
        confidence.setdefault(tag, 0.6)
    for row in theme.entity_business_industries:
        confidence.setdefault(row.entity, row.confidence)
    return confidence


def enrich_theme_tags(theme: SelectedThemeCandidate, sources: list[dict[str, Any]]) -> SelectedThemeCandidate:
    text = _text_for_theme(theme, sources)
    issue_tags = _unique([*theme.issue_tags, *_matches(text, ISSUE_TAG_RULES)], limit=15)
    direct_industries = _unique(
        [*theme.direct_impact_industries, *theme.related_industries, *_matches(text, INDUSTRY_RULES)],
        limit=15,
    )
    market_tags = _unique([*theme.market_theme_tags, *_matches(text, MARKET_THEME_RULES)], limit=15)
    entity_rows = theme.entity_business_industries or _entity_businesses(text, theme.related_companies)
    entity_industries = [industry for row in entity_rows for industry in row.industries if isinstance(industry, str)]
    candidate_tags = _expand_tags(theme.candidate_search_tags, issue_tags, direct_industries, market_tags, entity_industries)

    return theme.model_copy(
        update={
            "issue_tags": issue_tags,
            "direct_impact_industries": direct_industries,
            "entity_business_industries": entity_rows,
            "market_theme_tags": market_tags,
            "candidate_search_tags": candidate_tags,
            "related_industries": _unique([*theme.related_industries, *direct_industries], limit=10),
        }
    )
