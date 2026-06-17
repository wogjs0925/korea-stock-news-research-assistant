from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.app_setting import AppSetting


def ensure_app_settings_schema(db: Session) -> None:
    Base.metadata.create_all(bind=db.get_bind())


def get_app_setting(db: Session, key: str) -> AppSetting | None:
    ensure_app_settings_schema(db)
    return db.scalar(select(AppSetting).where(AppSetting.setting_key == key))


def set_app_setting(
    db: Session,
    key: str,
    value: str | None,
    value_type: str = "str",
    category: str = "general",
) -> AppSetting:
    ensure_app_settings_schema(db)
    row = get_app_setting(db, key) or AppSetting(setting_key=key)
    row.setting_value = value
    row.value_type = value_type
    row.category = category
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_app_setting(db: Session, key: str) -> bool:
    ensure_app_settings_schema(db)
    row = get_app_setting(db, key)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
