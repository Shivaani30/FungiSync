"""Focused configuration and isolated-target checks for Phase 3.3.8.9."""

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.db_safety import (
    create_guarded_admin_engine,
    load_test_target,
    verify_admin_connection,
    verify_runtime_connection,
)


def test_app_url_is_required_without_legacy_fallback(monkeypatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/fungisync_db")
    with pytest.raises(ValidationError, match="app_database_url"):
        Settings(_env_file=None, reservation_pending_ttl_seconds=3600)


def test_app_rejects_admin_login() -> None:
    with pytest.raises(ValidationError, match="fungisync_app_runtime"):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg://fungisync_migration_admin@127.0.0.1:55433/fungisync_test_db",
            reservation_pending_ttl_seconds=3600,
        )


def test_shared_port_is_rejected_before_connect(monkeypatch) -> None:
    value = os.environ["TEST_ADMIN_DATABASE_URL"]
    monkeypatch.setenv("TEST_ADMIN_DATABASE_URL", value.replace(":55433/", ":5433/"))
    with pytest.raises(RuntimeError, match="isolated loopback"):
        load_test_target()


def test_connected_identities_and_cluster(test_engine, test_admin_engine) -> None:
    target = load_test_target()
    with test_admin_engine.connect() as connection:
        verify_admin_connection(connection, target)
    with test_engine.connect() as connection:
        verify_runtime_connection(connection, target)


def test_admin_engine_rejects_wrong_system_identifier() -> None:
    target = load_test_target()
    engine = create_guarded_admin_engine(target=replace(target, system_id=target.system_id + 1))
    try:
        with pytest.raises(RuntimeError, match="cluster"):
            with engine.connect():
                pass
    finally:
        engine.dispose()


def test_alembic_requires_explicit_admin_url() -> None:
    environment = os.environ.copy()
    environment.pop("MIGRATION_DATABASE_URL", None)
    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL is required" in result.stderr
