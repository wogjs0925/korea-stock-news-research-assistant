from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from app.database.base import Base


class SecurityAlias(Base):
    __tablename__ = "security_aliases"
    __table_args__ = (
        UniqueConstraint("security_id", "normalized_alias", name="uq_security_alias_normalized"),
        Index("ix_security_alias_lookup", "normalized_alias", "alias_type"),
    )

    id = Column(Integer, primary_key=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    alias = Column(String(256), nullable=False)
    normalized_alias = Column(String(256), nullable=False, index=True)
    alias_type = Column(String(32), nullable=False, index=True)
    language = Column(String(16), nullable=True, index=True)
    locale = Column(String(16), nullable=True, index=True)
    source = Column(String(64), nullable=False, index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["SecurityAlias"]
