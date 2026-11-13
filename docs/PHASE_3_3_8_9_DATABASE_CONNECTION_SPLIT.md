# Phase 3.3.8.9 — Database connection split and test safety

**Status:** Configuration and fixture wiring only. The controlled writer, its grants, and its migration are not implemented. Phase 3.3.8 is not accepted.

## Connection purposes

| Variable | Consumer | Required identity |
| --- | --- | --- |
| `APP_DATABASE_URL` | `app.core.config.Settings` and `app.db.session` for ordinary requests; also the app import in tests | `fungisync_app_runtime` only; missing or admin username fails configuration. The application settings model does not read migration credentials. |
| `MIGRATION_DATABASE_URL` | `alembic/env.py` only, and an explicitly privileged test subprocess | `fungisync_migration_admin` only; Alembic has no runtime-URL fallback. In test-schema mode it validates the connected isolated cluster before migrations. |
| `TEST_RUNTIME_DATABASE_URL` | Guarded `test_engine`, ordinary application sessions and API dependency overrides | `fungisync_app_runtime` on the isolated test database. |
| `TEST_ADMIN_DATABASE_URL` | Guarded `test_admin_engine`, schema setup/teardown and migration-test engines | `fungisync_migration_admin` on the same isolated test database. |
| `TEST_EXPECTED_CLUSTER_SYSTEM_ID`, `TEST_EXPECTED_DATA_DIRECTORY` | `tests.db_safety` and test-schema Alembic guard | Independently recorded identity of the dedicated cluster; required, with no default. |

The legacy `DATABASE_URL` and `TEST_DATABASE_URL` values in an existing ignored `.env` are not consumed by these paths. They must not be used as a fallback. Normal application deployments should inject only `APP_DATABASE_URL` and other non-database settings into the request process; supply `MIGRATION_DATABASE_URL` only to the migration command. The test runner needs all four URLs because it contains both ordinary runtime checks and privileged fixture setup. Keep passwords out of URL strings when using the protected libpq passfile. The current local passfile contains **both** test credentials and is appropriate only for the privileged test runner or admin session; do not give that passfile to a normal application process. Separate runtime-only secret delivery and OS/process isolation remain deployment prerequisites.

## Destructive fixture boundary

`backend/tests/conftest.py` uses the admin engine for `Base.metadata.drop_all` and `create_all`; `test_engine` is the guarded runtime engine. Migration fixtures in `test_allocation_migrations.py`, `test_proposal_version_integrity.py`, and `test_database_audit_integrity.py` use guarded admin engines for `DROP SCHEMA ... CASCADE`, schema creation and Alembic revisions. The Alembic test subprocess receives only the explicit admin URL and performs the same connected-target check before migration DDL. Ordinary test sessions and API overrides use the runtime engine.

Before a guarded admin connection is returned, `tests.db_safety` checks its actual `current_database`, `current_user`, `session_user`, TCP server address and port, `data_directory`, and `pg_control_system().system_identifier`. It requires the expected test database and admin role and rejects known existing-cluster identifiers and shared port 5433. The runtime connection checks the same database/address/port, restricted role, lack of elevated attributes or privileged memberships, and absence of direct allocation-line DML grants on metadata-created tables. Missing or mismatched values fail closed. Destructive setup uses the checked connection itself; reconnects are checked again. The test URL guard checks that application and test runtime URLs match, migration and test admin URLs match, and both test roles target the same endpoint. In test-schema mode, Alembic checks URL settings **before connecting** and verifies the connected admin identity inside its migration transaction before DDL. The database name alone is never treated as proof of isolation.

## Local isolated test configuration

The verified local test cluster is PostgreSQL 13.20 at `127.0.0.1:55433`, database `fungisync_test_db`, data directory `C:\Users\LENOVO\AppData\Local\FungiSync\IsolatedPg13\data`, system identifier `7707207135383944876`. Its protected passfile is outside the repository at `C:\Users\LENOVO\AppData\Local\FungiSync\IsolatedPg13\pgpass.conf`. Confirm the running server's address, port, data directory and identifier again before a test run; historical values are not proof that a later process still targets it.

For an isolated **test-runner process only**, set `PGPASSFILE` to that protected path; set `APP_DATABASE_URL` and `TEST_RUNTIME_DATABASE_URL` to the restricted runtime login at the isolated endpoint; set `MIGRATION_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` to the migration/admin login at the **same** endpoint; and set `TEST_EXPECTED_CLUSTER_SYSTEM_ID` and `TEST_EXPECTED_DATA_DIRECTORY` to the independently verified values above. Set the existing required `RESERVATION_PENDING_TTL_SECONDS` normally. Do not overwrite the existing `.env`, put an admin URL in the normal application process, or run tests with the old shared-cluster `localhost:5433` settings. `PGPASSFILE` is user-ACL protected and contains credentials; never print or copy it into Git.

The setup report [Phase 3.3.8.8](PHASE_3_3_8_8_ISOLATED_DATABASE_SETUP.md) records the cluster and role evidence. A guarded connection check must pass before any destructive fixture or migration test. If the cluster is stopped, start only its dedicated data directory with `pg_ctl` and bind to loopback port 55433; do not change the shared Windows PostgreSQL service.

## Current test limits and next work

The isolated database currently has no allocation tables or controlled functions, and the runtime has only CONNECT. Existing application tests often seed growers, batches, proposals, and allocation lines through their normal `test_engine`; these tests cannot yet pass under the restricted role. Do not grant direct line DML merely to make them pass. The next controlled-writer phase must establish reviewed table/function grants and move privileged test-only seed operations behind explicit admin fixtures where needed, while keeping application behavior under the runtime connection. The current split permits focused configuration, target-guard and migration-admin tests, but it is not evidence of the final allocation-line privilege boundary or complete backend-suite compatibility.

No existing migration history, allocation service, role, credential, or shared PostgreSQL cluster was changed by this connection-split task.

## Verification on 2026-11-12

Before any fixture DDL, independent `psql` connections confirmed both identities at `127.0.0.1:55433`; the admin connection reported the dedicated data directory and system identifier `7707207135383944876`. The new Python guards then connected successfully as admin and runtime. The first guard attempt failed closed because PostgreSQL rendered `inet_server_addr()::text` as `127.0.0.1/32`; using `host(inet_server_addr())` fixed the comparison before any fixture DDL ran.

All listed pytest commands used the explicit isolated runtime/admin URLs and protected `PGPASSFILE` in the process environment:

| Command, from `backend` | Result | Identity and scope |
| --- | --- | --- |
| `python -m pytest -q tests/test_database_connection_split.py` (first run) | Exit 1; 5 passed, 1 failed. The test used `alembic history`, which does not load `env.py`; the test was corrected to use `alembic current`. | Guarded runtime connection plus admin fixture setup in isolated database. |
| Same focused command (rerun) | Exit 0; 6 passed. | Guarded runtime/admin target and missing-config checks. |
| `python -m pytest -q tests/test_allocation_migrations.py` (first run) | Exit 1; 2 failed. A pre-transaction Alembic safety SELECT caused its migration DDL to roll back on close. | Admin-only migration schema, isolated database. |
| Same allocation migration command (rerun) | Exit 0; 2 passed after moving the safety SELECT inside Alembic's transaction, before DDL. | Admin-only migration schema, isolated database. |
| `python -m pytest -q tests/test_proposal_version_integrity.py` | Exit 0; 2 passed. | Admin-only migration integrity tests. |
| `python -m pytest -q tests/test_database_audit_integrity.py` | Exit 0; 24 passed. | Admin-only database audit integrity tests; **not** runtime-role lifecycle evidence. |
| `python -m pytest -q tests/test_database_connection_split.py` (final guard rerun) | Exit 0; 6 passed. | Runtime/admin guards also checked direct line-DML grants on metadata-created tables. |
| `python -m pytest -q tests/test_allocation_migrations.py::test_allocation_migration_preserves_populated_current_schema` | Exit 0; 1 passed. | Admin-only regression after Alembic's test URL precheck moved ahead of connection. |

No ordinary application persistence, reservation lifecycle, API or complete-backend tests were run: those tests currently seed records through `test_engine`, now correctly bound to the restricted runtime, which has no table grants yet. They must not be made to pass by granting direct allocation-line DML. No test was run against the shared cluster. An empty diagnostic schema created during the Alembic transaction investigation was removed from the isolated database after an admin identity and emptiness check.
