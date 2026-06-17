from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    setting_key = Column(String(128), nullable=False, unique=True, index=True)
    setting_value = Column(Text, nullable=True)
    value_type = Column(String(32), nullable=False, default="str")
    category = Column(String(64), nullable=False, index=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


__all__ = ["AppSetting"]
