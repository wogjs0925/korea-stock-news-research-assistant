import re
import html
import hashlib
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from bs4 import BeautifulSoup


UTM_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def strip_html(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be a str")
    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text(separator=" ")
    text = html.unescape(text)
    return text.strip()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_news_title(value: str) -> str:
    text = strip_html(value)
    text = normalize_whitespace(text)
    # lowercasing for comparison purposes
    return text.lower()


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        return url
    query = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query if k not in UTM_PARAMS]
    new_query = urlencode(filtered)
    cleaned = parsed._replace(query=new_query)
    return urlunparse(cleaned)


def build_news_hash(title: str, original_link: Optional[str], link: str) -> str:
    title_norm = normalize_news_title(title)
    url = None
    if original_link:
        url = _normalize_url(original_link)
    elif link:
        url = _normalize_url(link)
    base = url or title_norm
    digest = hashlib.sha256()
    digest.update(base.encode("utf-8"))
    digest.update(title_norm.encode("utf-8"))
    return digest.hexdigest()
