from datetime import date, datetime, timezone

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from app.database.base import Base


class ETFHolding(Base):
    __tablename__ = "etf_holdings"
    __table_args__ = (
        UniqueConstraint("etf_security_id", "holding_security_id", "holding_ticker", name="uq_etf_holding_identity"),
        Index("ix_etf_holding_etf", "etf_security_id"),
        Index("ix_etf_holding_security", "holding_security_id"),
    )

    id = Column(Integer, primary_key=True)
    etf_security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    holding_security_id = Column(Integer, ForeignKey("securities.id"), nullable=True, index=True)
    holding_name = Column(String(256), nullable=False, index=True)
    holding_ticker = Column(String(32), nullable=True, index=True)
    country_code = Column(String(2), nullable=True, index=True)
    weight = Column(Float, nullable=True)
    source = Column(String(64), nullable=False, index=True)
    as_of_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["ETFHolding"]
