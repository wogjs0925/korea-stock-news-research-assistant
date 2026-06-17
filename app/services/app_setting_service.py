from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.repositories.app_setting_repository import delete_app_setting, get_app_setting, set_app_setting
from app.services.credential_service import resolve_secret, secret_source


NON_SECRET_SETTINGS = {
    "OPENAI_MODEL": ("openai", "str"),
    "NEWS_PROVIDER": ("naver", "str"),
    "NEWS_SCHEDULE_MINUTES": ("naver", "int"),
    "NEWS_SCHEDULER_ENABLED": ("naver", "bool"),
    "SEC_USER_AGENT": ("sec", "str"),
    "SECURITY_SYNC_USER_AGENT": ("security", "str"),
    "KRX_API_BASE_URL": ("krx", "str"),
    "KRX_KOSPI_BASIC_API_ID": ("krx", "str"),
    "KRX_KOSDAQ_BASIC_API_ID": ("krx", "str"),
    "KRX_KONEX_BASIC_API_ID": ("krx", "str"),
    "KRX_ETF_DAILY_API_ID": ("krx", "str"),
    "KRX_API_KEY_PARAM": ("krx", "str"),
    "KRX_API_ID_PARAM": ("krx", "str"),
    "KRX_BASE_DATE_PARAM": ("krx", "str"),
    "KRX_SYNC_TIMEOUT": ("krx", "float"),
    "KRX_BUSINESS_DAY_LOOKBACK": ("krx", "int"),
}


def _coerce(value: str | None, value_type: str) -> Any:
    if value is None:
        return None
    if value_type == "bool":
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    return value


def _setting_value(key: str, db: Session | None = None) -> str | None:
    if db is not None:
        row = get_app_setting(db, key)
        return row.setting_value if row is not None else None
    owned_db = SessionLocal()
    try:
        row = get_app_setting(owned_db, key)
        return row.setting_value if row is not None else None
    finally:
        owned_db.close()


def get_runtime_setting(key: str, default: Any = None, db: Session | None = None) -> Any:
    meta = NON_SECRET_SETTINGS.get(key)
    value_type = meta[1] if meta else "str"
    value = _setting_value(key, db=db)
    if value is None:
        if default is not None:
            return default
        settings = get_settings()
        attr = key.lower()
        if key == "NEWS_SCHEDULE_MINUTES":
            attr = "news_scheduler_interval_minutes"
        return getattr(settings, attr, default)
    return _coerce(value, value_type)


def set_runtime_setting(db: Session, key: str, value: str | None) -> None:
    if key not in NON_SECRET_SETTINGS:
        raise ValueError("unsupported setting key")
    category, value_type = NON_SECRET_SETTINGS[key]
    set_app_setting(db, key, value, value_type=value_type, category=category)


def delete_runtime_setting(db: Session, key: str) -> None:
    delete_app_setting(db, key)


def get_secret_value(name: str) -> str | None:
    return resolve_secret(name)


def get_secret_source(name: str) -> str:
    return secret_source(name)


def runtime_openai_model(db: Session | None = None) -> str:
    return str(get_runtime_setting("OPENAI_MODEL", get_settings().openai_model, db=db))


def runtime_sec_user_agent(db: Session | None = None, default: str | None = None) -> str | None:
    return get_runtime_setting("SEC_USER_AGENT", default if default is not None else get_settings().sec_user_agent, db=db)
