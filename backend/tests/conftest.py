from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from tests.db_safety import (
    create_guarded_admin_engine,
    create_guarded_runtime_engine,
    load_test_target,
    verify_admin_connection,
    verify_runtime_connection,
)


@pytest.fixture(scope="session")
def test_admin_engine():
    target = load_test_target()
    engine = create_guarded_admin_engine(target=target)
    try:
        with engine.connect() as connection:
            verify_admin_connection(connection, target)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_engine(test_admin_engine):
    target = load_test_target()
    engine = create_guarded_runtime_engine(target=target)
    try:
        with engine.connect() as connection:
            verify_runtime_connection(connection, target)
        with test_admin_engine.begin() as connection:
            verify_admin_connection(connection, target)
            Base.metadata.drop_all(connection)
            Base.metadata.create_all(connection)
        with engine.connect() as connection:
            verify_runtime_connection(connection, target)
        yield engine
    finally:
        with test_admin_engine.begin() as connection:
            verify_admin_connection(connection, target)
            Base.metadata.drop_all(connection)
        engine.dispose()


@pytest.fixture(autouse=True)
def database(test_engine, test_admin_engine) -> Generator[None, None, None]:
    target = load_test_target()
    with test_admin_engine.begin() as connection:
        verify_admin_connection(connection, target)
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
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
