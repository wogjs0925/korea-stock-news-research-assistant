from typing import Optional
from urllib.parse import urlparse


DOMAIN_MAP = {
    "yna.co.kr": "연합뉴스",
    "yonhapnews.co.kr": "연합뉴스",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "mt.co.kr": "머니투데이",
    "news1.kr": "뉴스1",
    "newsis.com": "뉴시스",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "kbs.co.kr": "KBS",
    "imbc.com": "MBC",
    "sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN",
    "etnews.com": "전자신문",
    "zdnet.co.kr": "지디넷코리아",
    "bloter.net": "블로터",
}


def _clean_hostname(hostname: str) -> str:
    # remove common prefixes
    for p in ("www.", "m.", "news."):
        if hostname.startswith(p):
            hostname = hostname[len(p) :]
    return hostname


def infer_publisher(original_link: Optional[str], link: Optional[str]) -> Optional[str]:
    url = original_link or link
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return None
        hostname = _clean_hostname(hostname)
        # try exact matches and suffix matches
        for domain, name in DOMAIN_MAP.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return name

        # fallback: return cleaned hostname (strip possible port)
        # remove possible leading subdomains we didn't strip
        return hostname
    except Exception:
        return None
