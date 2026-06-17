from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.security import SecurityIn


class SecurityMasterProvider(ABC):
    name: str
    country_code: str

    @abstractmethod
    async def fetch_securities(self) -> list[SecurityIn]:
        ...
