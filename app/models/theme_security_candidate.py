from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint

from app.database.base import Base


class ThemeSecurityCandidate(Base):
    __tablename__ = "theme_security_candidates"
    __table_args__ = (
        UniqueConstraint("market_theme_id", "security_id", name="uq_theme_security_candidate"),
        Index("ix_theme_security_candidate_theme_status", "market_theme_id", "match_status"),
    )

    id = Column(Integer, primary_key=True)
    market_theme_id = Column(Integer, ForeignKey("market_themes.id"), nullable=False, index=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=True, index=True)
    source_company_name = Column(String(256), nullable=False, index=True)
    source_keyword = Column(String(256), nullable=True, index=True)
    source_type = Column(String(32), nullable=False, default="company_name", index=True)
    match_score = Column(Float, nullable=False)
    relevance_score = Column(Float, nullable=False, default=0.0)
    theme_fit_score = Column(Float, nullable=False, default=0.0)
    evidence_score = Column(Float, nullable=False, default=0.0)
    liquidity_proxy_score = Column(Float, nullable=False, default=0.0)
    risk_penalty_score = Column(Float, nullable=False, default=0.0)
    final_candidate_score = Column(Float, nullable=False, default=0.0, index=True)
    match_method = Column(String(64), nullable=False)
    match_status = Column(String(32), nullable=False, index=True)
    country_code = Column(String(2), nullable=True, index=True)
    asset_type = Column(String(16), nullable=True, index=True)
    evidence_count = Column(Integer, nullable=False, default=0)
    reason_summary = Column(Text, nullable=True)
    matched_evidence_json = Column(JSON, nullable=False, default=list)
    risk_flags_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["ThemeSecurityCandidate"]
