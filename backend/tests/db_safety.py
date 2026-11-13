"""Fail-closed guards for destructive operations in an isolated test cluster."""

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, URL, make_url


TEST_DATABASE = "fungisync_test_db"
RUNTIME_ROLE = "fungisync_app_runtime"
ADMIN_ROLE = "fungisync_migration_admin"
KNOWN_EXISTING_SYSTEM_IDS = {7494129124120616656, 7489088127477219488}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TestTarget:
    runtime_url: URL
    admin_url: URL
    system_id: int
    data_directory: str

    @property
    def address(self) -> str:
        return self.admin_url.host or ""

    @property
    def port(self) -> int:
        return self.admin_url.port or 0


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required for isolated database tests")
    return value


def _normalized_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def load_test_target() -> TestTarget:
    """Require separate explicit URLs and independently recorded cluster identity."""
    runtime_url = make_url(_required("TEST_RUNTIME_DATABASE_URL"))
    admin_url = make_url(_required("TEST_ADMIN_DATABASE_URL"))
    app_url = make_url(_required("APP_DATABASE_URL"))
    migration_url = make_url(_required("MIGRATION_DATABASE_URL"))
    urls = ((runtime_url, RUNTIME_ROLE), (app_url, RUNTIME_ROLE),
            (admin_url, ADMIN_ROLE), (migration_url, ADMIN_ROLE))
    for url, role in urls:
        if (url.drivername != "postgresql+psycopg" or url.username != role
                or url.database != TEST_DATABASE or url.host != "127.0.0.1"
                or not url.port or url.port == 5433):
            raise RuntimeError("Test URLs must name the isolated loopback database and intended roles")
    if app_url != runtime_url or migration_url != admin_url:
        raise RuntimeError("Test APP/MIGRATION URLs must match their explicit runtime/admin test URLs")
    if (runtime_url.host, runtime_url.port, runtime_url.database) != (
            admin_url.host, admin_url.port, admin_url.database):
        raise RuntimeError("Runtime and admin test URLs must target the same isolated database")
    try:
        system_id = int(_required("TEST_EXPECTED_CLUSTER_SYSTEM_ID"))
    except ValueError as exc:
        raise RuntimeError("TEST_EXPECTED_CLUSTER_SYSTEM_ID must be an integer") from exc
    if system_id <= 0 or system_id in KNOWN_EXISTING_SYSTEM_IDS:
        raise RuntimeError("Refusing an existing non-isolated PostgreSQL cluster")
    data_directory = _normalized_path(_required("TEST_EXPECTED_DATA_DIRECTORY"))
    existing_directories = {
        _normalized_path(r"C:\Program Files\PostgreSQL\13\data"),
        _normalized_path(r"C:\Program Files\PostgreSQL\17\data"),
    }
    if (not ntpath.isabs(data_directory) or data_directory in existing_directories
            or data_directory.startswith(_normalized_path(str(REPOSITORY_ROOT)) + "\\")):
        raise RuntimeError("Test data directory must be absolute and outside the repository/shared cluster")
    return TestTarget(runtime_url, admin_url, system_id, data_directory)


_ADMIN_IDENTITY_SQL = """
SELECT current_database(), current_user, session_user,
       host(inet_server_addr()), inet_server_port(),
       current_setting('data_directory'), system_identifier
FROM pg_control_system()
"""
_RUNTIME_IDENTITY_SQL = """
SELECT current_database(), current_user, session_user,
       host(inet_server_addr()), inet_server_port(),
       rolsuper, rolcreatedb, rolcreaterole, rolinherit,
       rolreplication, rolbypassrls,
       has_database_privilege(current_user, current_database(), 'CREATE'),
       has_database_privilege(current_user, current_database(), 'TEMP'),
       EXISTS (SELECT 1 FROM pg_namespace n
               WHERE n.nspname NOT LIKE 'pg_%'
                 AND n.nspname <> 'information_schema'
                 AND has_schema_privilege(current_user, n.oid, 'CREATE')),
       EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname NOT LIKE 'pg_%'
                 AND n.nspname <> 'information_schema'
                 AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)),
       EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname NOT LIKE 'pg_%'
                 AND c.relname = 'allocation_lines'
                 AND (has_table_privilege(current_user, c.oid, 'INSERT')
                      OR has_table_privilege(current_user, c.oid, 'UPDATE')
                      OR has_table_privilege(current_user, c.oid, 'DELETE')
                      OR has_table_privilege(current_user, c.oid, 'TRUNCATE'))),
       pg_has_role(current_user, 'fungisync_function_owner', 'MEMBER'),
       pg_has_role(current_user, 'fungisync_migration_admin', 'MEMBER')
FROM pg_roles WHERE rolname = current_user
"""


def _check_admin_row(row: tuple[object, ...] | None, target: TestTarget) -> None:
    if row is None or row[:5] != (TEST_DATABASE, ADMIN_ROLE, ADMIN_ROLE,
                                    target.address, target.port):
        raise RuntimeError("Unsafe test administrator connection identity")
    if (_normalized_path(str(row[5])) != target.data_directory
            or int(row[6]) != target.system_id):
        raise RuntimeError("Unsafe test cluster: data directory or system identifier differs")


def _check_runtime_row(row: tuple[object, ...] | None, target: TestTarget) -> None:
    if row is None or row[:5] != (TEST_DATABASE, RUNTIME_ROLE, RUNTIME_ROLE,
                                    target.address, target.port):
        raise RuntimeError("Unsafe test runtime connection identity")
    if any(row[5:]):
        raise RuntimeError("Test runtime role has elevated attributes or owner membership")


def verify_admin_connection(connection: Connection, target: TestTarget) -> None:
    """Call on the exact connection before any test schema/table DDL."""
    _check_admin_row(connection.execute(text(_ADMIN_IDENTITY_SQL)).one_or_none(), target)


def verify_runtime_connection(connection: Connection, target: TestTarget) -> None:
    _check_runtime_row(connection.execute(text(_RUNTIME_IDENTITY_SQL)).one_or_none(), target)


def _guarded_engine(url: URL, target: TestTarget, *, admin: bool, **kwargs: object) -> Engine:
    engine = create_engine(url, **kwargs)
    query = _ADMIN_IDENTITY_SQL if admin else _RUNTIME_IDENTITY_SQL
    check = _check_admin_row if admin else _check_runtime_row

    @event.listens_for(engine, "connect")
    def check_new_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(query)
            check(cursor.fetchone(), target)
        finally:
            cursor.close()
            dbapi_connection.rollback()

    return engine


def create_guarded_admin_engine(*, target: TestTarget | None = None,
                                **kwargs: object) -> Engine:
    chosen = target or load_test_target()
    return _guarded_engine(chosen.admin_url, chosen, admin=True, **kwargs)


def create_guarded_runtime_engine(*, target: TestTarget | None = None,
                                  **kwargs: object) -> Engine:
    chosen = target or load_test_target()
    return _guarded_engine(chosen.runtime_url, chosen, admin=False, **kwargs)
