from __future__ import annotations

from app.providers.securities.base import SecurityMasterProvider
from app.schemas.security import SecurityIn
from app.utils.security_names import generate_name_aliases


class MockSecurityMasterProvider(SecurityMasterProvider):
    name = "mock"
    country_code = "ALL"

    async def fetch_securities(self) -> list[SecurityIn]:
        rows = [
            SecurityIn(
                country_code="KR",
                asset_type="stock",
                exchange_code="XKRX",
                exchange_name="KOSPI",
                ticker="005930",
                local_code="005930",
                name="삼성전자",
                english_name="Samsung Electronics",
                currency="KRW",
                sector="Technology",
                industry="Semiconductors",
                source=self.name,
            ),
            SecurityIn(
                country_code="KR",
                asset_type="stock",
                exchange_code="XKOS",
                exchange_name="KOSDAQ",
                ticker="035720",
                local_code="035720",
                name="카카오",
                english_name="Kakao Corp",
                currency="KRW",
                sector="Communication Services",
                industry="Internet Services",
                source=self.name,
            ),
            SecurityIn(
                country_code="KR",
                asset_type="etf",
                exchange_code="XKRX",
                exchange_name="Korea ETF",
                ticker="069500",
                local_code="069500",
                name="KODEX 200",
                english_name="KODEX 200 ETF",
                currency="KRW",
                issuer_name="Samsung Asset Management",
                source=self.name,
            ),
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNAS",
                exchange_name="NASDAQ",
                ticker="NVDA",
                name="NVIDIA Corporation",
                english_name="NVIDIA Corporation",
                currency="USD",
                cik="0001045810",
                sector="Technology",
                industry="Semiconductors",
                source=self.name,
            ),
            SecurityIn(
                country_code="US",
                asset_type="stock",
                exchange_code="XNYS",
                exchange_name="NYSE",
                ticker="IBM",
                name="International Business Machines Corporation",
                english_name="IBM",
                currency="USD",
                cik="0000051143",
                sector="Technology",
                industry="Information Technology Services",
                source=self.name,
            ),
            SecurityIn(
                country_code="US",
                asset_type="etf",
                exchange_code="ARCX",
                exchange_name="NYSE Arca",
                ticker="SPY",
                name="SPDR S&P 500 ETF Trust",
                english_name="SPDR S&P 500 ETF Trust",
                currency="USD",
                issuer_name="State Street",
                source=self.name,
            ),
        ]
        for row in rows:
            row.aliases.extend(generate_name_aliases(row.name, row.english_name, row.ticker, row.issuer_name))
        return rows
