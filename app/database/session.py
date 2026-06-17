from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


settings = get_settings()
connect_args = {}
engine_kwargs: dict[str, object] = {"future": True}

if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    engine_kwargs["connect_args"] = connect_args
    if settings.database_url == "sqlite:///:memory:":
        from sqlalchemy.pool import StaticPool

        engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(settings.database_url, **engine_kwargs)
else:
    engine_kwargs["connect_args"] = connect_args
    engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
