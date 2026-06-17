from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from app.utils.text import normalize_whitespace, strip_html

ANALYSIS_CANDIDATE_THRESHOLD = 0.4
MARKET_RELEVANCE_THRESHOLD = 0.2

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "igshid",
    "ref",
}

TITLE_TAG_PATTERN = re.compile(r"^\s*(?:\[[^\]]+\]|\([^)]+\)|【[^】]+】|<[^>]+>)\s*")
TITLE_PUNCTUATION_PATTERN = re.compile(r"[^0-9a-z가-힣\s]")

PHOTO_TAGS = {"포토", "영상", "속보", "현장포토", "오늘의 운세", "부고", "인사", "알림"}
NOISE_KEYWORDS = {
    "골프",
    "야구",
    "축구",
    "농구",
    "경기 사진",
    "연예",
    "웨딩",
    "부고",
    "인사",
    "오늘의 운세",
}
MARKET_KEYWORDS = {
    "주가",
    "상장",
    "ipo",
    "실적",
    "매출",
    "영업이익",
    "투자",
    "금리",
    "환율",
    "반도체",
    "ai",
    "배터리",
    "에너지",
    "원유",
    "방산",
    "조선",
    "증권",
    "etf",
    "공모주",
    "m&a",
    "계약",
    "수주",
    "공급",
    "규제",
    "관세",
    "정책",
    "기업",
    "경제",
    "시장",
    "나스닥",
    "코스피",
    "코스닥",
}


def canonicalize_url(url: str | None) -> str:
    if not url:
        return ""
    raw = url.strip()
    parsed = urlparse(raw)
    if not parsed.scheme:
        return raw

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = {key: value for key, value in query_pairs if key.lower() not in TRACKING_PARAMS}
    target = query.get("url") or query.get("u")
    if "news.naver.com" in parsed.netloc.lower() and target:
        return canonicalize_url(unquote(target))

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parsed.path)
    cleaned = parsed._replace(scheme="https", netloc=netloc, path=path, query=urlencode(query), fragment="")
    return urlunparse(cleaned)


def normalize_title_for_dedupe(title: str | None) -> str:
    text = normalize_whitespace(strip_html(title or "")).lower()
    previous = None
    while previous != text:
        previous = text
        text = TITLE_TAG_PATTERN.sub("", text)
    text = TITLE_PUNCTUATION_PATTERN.sub(" ", text)
    return normalize_whitespace(text)


def content_fingerprint(title: str | None, description: str | None) -> str:
    title_part = normalize_title_for_dedupe(title)
    desc_part = normalize_title_for_dedupe(description)[:120]
    digest = hashlib.sha256()
    digest.update(f"{title_part}|{desc_part}".encode("utf-8"))
    return digest.hexdigest()


def duplicate_group_id(fingerprint: str) -> str:
    return fingerprint[:16]


def title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def has_photo_or_noise_tag(title: str | None) -> bool:
    lowered = strip_html(title or "").strip().lower()
    return any(lowered.startswith(f"[{tag}]") or lowered.startswith(f"({tag})") for tag in PHOTO_TAGS)


def market_relevance(title: str | None, description: str | None, query: str | None = None) -> tuple[float, bool, bool, str | None]:
    normalized_title = normalize_title_for_dedupe(title)
    normalized_description = normalize_title_for_dedupe(description)
    text = f"{normalized_title} {normalized_description} {normalize_title_for_dedupe(query)}"
    score = 0.15
    matched_market = [word for word in MARKET_KEYWORDS if word.lower() in text]
    score += min(len(matched_market) * 0.12, 0.6)
    if re.search(r"\b[A-Z]{1,5}\b", title or ""):
        score += 0.1

    reason = None
    if has_photo_or_noise_tag(title):
        score -= 0.25
        reason = "photo_or_notice"
    if any(word in text for word in NOISE_KEYWORDS):
        score -= 0.25
        reason = reason or "sports_entertainment_or_notice"
    if len(normalized_description) < 20:
        score -= 0.1
        reason = reason or "short_description"

    score = max(0.0, min(1.0, round(score, 4)))
    is_market = score >= MARKET_RELEVANCE_THRESHOLD
    is_candidate = score >= ANALYSIS_CANDIDATE_THRESHOLD
    if not matched_market and reason:
        is_candidate = False
    return score, is_market, is_candidate, reason
