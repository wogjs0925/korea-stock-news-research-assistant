import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.error_log import ErrorLog


def test_error_log_model_can_create_and_query():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=engine)
    try:
        with TestingSessionLocal() as session:
            record = ErrorLog(
                error_code="DB_TEST",
                severity="INFO",
                component="database",
                error_type="TestError",
                message="데이터베이스 연결 테스트입니다.",
                status="new",
                context_json={"source": "test"},
            )
            session.add(record)
            session.commit()
            session.refresh(record)

            assert record.id is not None
            assert record.error_code == "DB_TEST"

            fetched = session.get(ErrorLog, record.id)
            assert fetched is not None
            assert fetched.message == "데이터베이스 연결 테스트입니다."
    finally:
        engine.dispose()
