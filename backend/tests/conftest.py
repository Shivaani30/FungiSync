from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.core.config import get_settings


def _test_url() -> str:
    value = get_settings().test_database_url
    if not value:
        raise RuntimeError("TEST_DATABASE_URL is required; tests never use DATABASE_URL.")
    test_url = make_url(value)
    development_url = make_url(get_settings().database_url)
    if test_url.database != "fungisync_test_db" or test_url.database == development_url.database:
        raise RuntimeError("TEST_DATABASE_URL must target fungisync_test_db, never the development database.")
    return value


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(_test_url())
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def database(test_engine) -> Generator[None, None, None]:
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield


@pytest.fixture()
def client(test_engine) -> Generator[TestClient, None, None]:
    factory = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    def override() -> Generator[Session, None, None]:
        session = factory()
        try: yield session
        finally: session.close()
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
