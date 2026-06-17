from __future__ import annotations

import re


_LEGAL_SUFFIXES = (
    "incorporated",
    "corporation",
    "corp",
    "company",
    "co",
    "limited",
    "ltd",
    "inc",
    "plc",
    "holdings",
    "holding",
)


def normalize_ticker(ticker: str | None) -> str:
    if not ticker:
        return ""
    return re.sub(r"\s+", "", ticker).upper()


def normalize_company_name(name: str | None) -> str:
    if not name:
        return ""
    value = name.strip().lower()
    value = value.replace("㈜", " ")
    value = re.sub(r"\b주식회사\b", " ", value)
    value = re.sub(r"[\(\)\[\]\{\},.'\"`·]", " ", value)
    value = re.sub(r"&", " and ", value)
    value = re.sub(r"[^0-9a-z가-힣\s-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    tokens = value.split()
    while tokens and tokens[-1].replace(".", "") in _LEGAL_SUFFIXES:
        tokens.pop()
    value = " ".join(tokens)
    return re.sub(r"\s+", " ", value).strip()


def generate_security_key(country_code: str, exchange_code: str, ticker: str) -> str:
    return f"{country_code.upper()}:{exchange_code.upper()}:{normalize_ticker(ticker)}"


def generate_name_aliases(
    name: str,
    english_name: str | None = None,
    ticker: str | None = None,
    issuer_name: str | None = None,
) -> list[dict[str, str | None]]:
    candidates = [
        (name, "korean_name" if re.search(r"[가-힣]", name or "") else "legal_name"),
        (english_name, "english_name"),
        (issuer_name, "legal_name"),
        (ticker, "ticker_alias"),
    ]
    aliases: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for alias, alias_type in candidates:
        if not alias:
            continue
        normalized = normalize_ticker(alias) if alias_type == "ticker_alias" else normalize_company_name(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(
            {
                "alias": alias,
                "normalized_alias": normalized,
                "alias_type": alias_type,
                "language": "ko" if re.search(r"[가-힣]", alias) else "en",
            }
        )
    return aliases
