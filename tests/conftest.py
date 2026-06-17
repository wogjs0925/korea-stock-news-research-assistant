import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.main import app
from app.database.base import Base
from app.database.session import get_db


@pytest.fixture
def sqlite_engine():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_session_local(sqlite_engine):
    TestingSessionLocal = sessionmaker(bind=sqlite_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(bind=sqlite_engine)
    try:
        yield TestingSessionLocal
    finally:
        Base.metadata.drop_all(bind=sqlite_engine)


@pytest.fixture
def client(sqlite_session_local):
    def override_get_db():
        db = sqlite_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolate_keyring(monkeypatch):
    from app.services import credential_service

    class EmptyKeyring:
        def __init__(self):
            self.values = {}

        def set_password(self, service, name, value):
            self.values[(service, name)] = value

        def get_password(self, service, name):
            return self.values.get((service, name))

        def delete_password(self, service, name):
            self.values.pop((service, name), None)

    fake = EmptyKeyring()
    monkeypatch.setattr(credential_service, "_keyring", lambda: fake)
    return fake
