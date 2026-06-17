from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Index, Integer, String, UniqueConstraint

from app.database.base import Base


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (
        UniqueConstraint(
            "country_code",
            "exchange_code",
            "ticker",
            "asset_type",
            name="uq_security_market_ticker_asset",
        ),
        Index("ix_security_country_asset", "country_code", "asset_type"),
    )

    id = Column(Integer, primary_key=True)
    security_key = Column(String(64), nullable=False, unique=True, index=True)
    country_code = Column(String(2), nullable=False, index=True)
    asset_type = Column(String(16), nullable=False, index=True)
    exchange_code = Column(String(16), nullable=False, index=True)
    exchange_name = Column(String(128), nullable=False)
    ticker = Column(String(32), nullable=False, index=True)
    local_code = Column(String(32), nullable=True, index=True)
    name = Column(String(256), nullable=False, index=True)
    english_name = Column(String(256), nullable=True, index=True)
    normalized_name = Column(String(256), nullable=False, index=True)
    currency = Column(String(8), nullable=False)
    cik = Column(String(32), nullable=True, index=True)
    figi = Column(String(64), nullable=True, index=True)
    isin = Column(String(32), nullable=True, index=True)
    sector = Column(String(128), nullable=True, index=True)
    industry = Column(String(128), nullable=True, index=True)
    issuer_name = Column(String(256), nullable=True)
    market_segment = Column(String(32), nullable=True, index=True)
    security_type_detail = Column(String(64), nullable=True, index=True)
    is_recommendation_eligible = Column(Boolean, nullable=False, default=True, index=True)
    is_leveraged = Column(Boolean, nullable=False, default=False, index=True)
    is_inverse = Column(Boolean, nullable=False, default=False, index=True)
    source_status = Column(String(128), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    listed_at = Column(Date, nullable=True)
    delisted_at = Column(Date, nullable=True)
    source = Column(String(64), nullable=False, index=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["Security"]
