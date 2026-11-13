# Phase 3.3.8.8 — Isolated PostgreSQL and role foundation

**Status:** Isolated cluster and three identities created and directly verified on 2026-11-12. This is a test foundation, not the controlled-writer migration, a production grant contract, or Phase 3.3.8 acceptance. O1–O9 approval is recorded in [the controlled-write design](PHASE_3_3_8_6_CONTROLLED_WRITE_CONTRACT.md#owner-approval-record--phase-3387).

## Completed and directly verified

| Item | Evidence |
| --- | --- |
| Repository | `phase-3-allocation-engine` at `df41fbce400c2f4f9e084128c1c1c8aabfc40a74` before setup. Existing tracked modifications and untracked files were preserved; no staging or commit. |
| Isolated server | PostgreSQL **13.20**, binary `C:\Program Files\PostgreSQL\13\bin\postgres.exe`; started with `pg_ctl` as Windows user `DESKTOP-179IBJ8\LENOVO`, PID 33216 at verification. Bound only to `127.0.0.1:55433`; SQL reported `listen_addresses=127.0.0.1`, `port=55433`. |
| Data and database | Dedicated data directory `C:\Users\LENOVO\AppData\Local\FungiSync\IsolatedPg13\data`, outside the repository. Database `fungisync_test_db`, OID `16386`, owned by `fungisync_migration_admin`. Admin SQL reported this directory, database, port, version and system identifier. |
| Cluster identity | New system identifier **7707207135383944876**, from `pg_control` and `pg_control_system()`. The running shared PostgreSQL 13 data directory `C:\Program Files\PostgreSQL\13\data` had identifier **7494129124120616656**. An existing PostgreSQL 17 data directory `C:\Program Files\PostgreSQL\17\data` had identifier **7489088127477219488**. All three identifiers differ. No PostgreSQL 17 service or usable `17\bin\postgres.exe`/`initdb.exe` was found; its existing data was not changed. |
| Authentication and credential storage | `initdb` used SCRAM-SHA-256 for local and host rules. A libpq passfile at `C:\Users\LENOVO\AppData\Local\FungiSync\IsolatedPg13\pgpass.conf` contains generated admin and runtime credentials; no credentials were printed or written to the repository. The parent directory has a protected user-only ACL; the passfile and data directory each showed one inherited FullControl entry for `DESKTOP-179IBJ8\LENOVO`. The temporary `initdb` password file was removed. Set `PGPASSFILE` to this path for local connections; keep this file outside Git and restricted to this Windows user. |
| Application runtime | `fungisync_app_runtime`: LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION, NOBYPASSRLS. An actual runtime login reached only the isolated test database; catalog checks showed CONNECT=true, database CREATE/TEMP=false, `public` CREATE/USAGE=false, and no membership in either privileged identity. Runtime attempts to create a schema/function or `SET ROLE` to either owner/admin failed (exit 1). A connection attempt to the `postgres` database failed for lack of CONNECT (exit 2). |
| Function owner | `fungisync_function_owner`: NOLOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION, NOBYPASSRLS; no role memberships. Direct login attempt failed (exit 2); the catalog's `rolcanlogin=false` is the decisive NOLOGIN evidence. It owns no table or function yet. |
| Migration/admin | `fungisync_migration_admin`: separate LOGIN and bootstrap superuser **only in this newly created cluster**, database owner, never used as application runtime. Actual login succeeded. A `CREATE SCHEMA` in a transaction succeeded and was rolled back; catalog check found no probe schema afterward. |
| Empty privilege surface | The isolated test database had zero user relations in `public` at verification. PUBLIC database privileges were revoked, runtime received CONNECT only, and PUBLIC privileges on `public` schema were revoked. No speculative allocation table/function grants were made. |

The installed PostgreSQL 13 binaries include `initdb.exe`, `pg_ctl.exe`, `psql.exe`, `pg_controldata.exe`, and `postgres.exe` under `C:\Program Files\PostgreSQL\13\bin`. The PostgreSQL 17 installation has an existing data directory but lacks the checked server and setup binaries; it was not reused. The existing Windows service `postgresql-x64-13` continued running on `::/0.0.0.0:5433` with data directory `C:\Program Files\PostgreSQL\13\data`. The new server is a separate user-started process, not that service.

## Completed but not independently verified

The repository's ignored `.env` currently configures both development and old test connections at `localhost:5433` using the `postgres` login, with separate database names. These endpoint values were read without disclosing credentials. This task did not connect to either shared database to recheck their current names, OIDs or privileges. Cluster separation is established by the directly compared system identifiers, server process paths and new-server SQL diagnostics.

## Not completed

- The new server is not registered as a Windows service or configured for automatic restart. Start it with `pg_ctl` using the dedicated data directory and loopback/port options; verify its identity after every restart. No existing service was altered.
- Existing `.env`, application settings, SQLAlchemy session construction and Alembic still use the old single-URL arrangement. They were not changed. The current test fixtures use `drop_all`/`create_all`, and migration tests use `DROP SCHEMA ... CASCADE`; their database-name guard alone does not prove cluster isolation. **Do not point those tests at the old shared cluster or run them with the restricted runtime login until the fixture/URL split is implemented and checked.**
- The untracked `f8a3c9d2e641` revision's formal acceptance and deployment status were not established. No migration was applied to any database.

## Deferred until the controlled-writer migration exists

- The actual `SECURITY DEFINER` writer, line-lock helper, hardened reservation-snapshot trigger, guard table, trusted-schema grants, PUBLIC EXECUTE revocation and final runtime table privileges do not exist in this empty database. Direct allocation-line INSERT/UPDATE/DELETE/TRUNCATE denial and function EXECUTE permissions **cannot yet be tested** against real objects.
- Application persistence integration, concurrency tests, migration upgrade/downgrade tests, the backend suite, deployment cutover and fail-closed rollback are later tasks. No application tests were run here.

## Minimum next-task configuration contract

Keep separate connection purposes: an application runtime URL for normal requests, a migration/admin URL for Alembic and privileged fixture setup, and isolated test URLs for runtime assertions versus privileged setup. Use the new host/port/database and each role's own login; supply passwords through the user-only `PGPASSFILE` or an equivalently protected secret mechanism, never in tracked files or command output. The current `DATABASE_URL`/`TEST_DATABASE_URL` settings and Alembic `env.py` do not implement this split. The next task must add explicit settings and fixture guards that verify the new cluster identity and prevent destructive setup from targeting the shared development cluster. The runtime must never receive the admin credentials or a direct allocation-line write grant.

No shared development or old test database was modified. No migration, controlled function, trigger, application test, staging, commit, or push occurred.
