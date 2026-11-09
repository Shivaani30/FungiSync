# FungiSync Distribution Intelligence

FungiSync is a Phase 1 distribution-management application for coordinating mushroom growers, harvest batches, buyers, and buyer orders. It provides a FastAPI/PostgreSQL CRUD API and a React coordinator dashboard. Matching, allocation, dispatch, optimisation, AI agents, and replanning are deliberately outside this phase.

## Technology stack

- Frontend: React 18, TypeScript, Vite, Tailwind CSS
- Backend: Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic
- Database: PostgreSQL with Psycopg

## Project structure

```text
backend/
  app/                 FastAPI app, models, API routers, schemas, services
  alembic/             migration environment and revisions
  tests/               isolated PostgreSQL API and schema tests
frontend/
  src/                 React dashboard and typed API client
.env.example           backend/database environment template
frontend/.env.example  browser API URL template
```

## Prerequisites

- Python 3.12
- Node.js 20 or newer
- PostgreSQL 13 or newer

## Environment configuration

Copy the templates. Do not commit the resulting environment files.

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env
```

Set the root variables using your own credentials:

```text
DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5433/fungisync_db
TEST_DATABASE_URL=postgresql+psycopg://postgres:<test-password>@localhost:5433/fungisync_test_db
BACKEND_CORS_ORIGINS=http://localhost:5173
```

Tests require `TEST_DATABASE_URL` and reject any database other than `fungisync_test_db`; they never fall back to the development database.

## PostgreSQL setup

Create two databases: `fungisync_db` for the application and `fungisync_test_db` for tests. The test database may be recreated by the test suite. Never point `TEST_DATABASE_URL` at the development database.

## Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Health endpoint: `http://localhost:8000/health`

Interactive API documentation: `http://localhost:8000/docs`

## Frontend

In a second terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open the printed Vite URL, normally `http://localhost:5173`. `VITE_API_BASE_URL` in `frontend/.env` must point to the backend (normally `http://localhost:8000`).

## Phase 2 deterministic matching

Phase 2 adds inspection-only supply-demand matching. It never reserves stock, consumes quantities, creates allocations, or changes orders/batches.

Hard eligibility rules:

- Exact normalized mushroom species match.
- Batch status is `confirmed` or `harvested`.
- Positive confirmed batch quantity.
- Harvest date is not after requested delivery, and delivery is not after a known best-before date.
- Known asking price does not exceed the buyer price limit.
- Known quality grades match exactly after whitespace/case normalization.

Scores use only known criteria, then normalize to 0–100. Missing data is reported separately through data-completeness and unknown-data warnings.

| Criterion | Weight |
| --- | ---: |
| Quantity coverage | 30 |
| Freshness/date | 25 |
| Price | 20 |
| Quality | 15 |
| Exact location text match | 10 |

Matching endpoints:

```text
GET /matches/orders/{order_id}
GET /matches/batches/{batch_id}
```

Both accept optional `minimum_score`, `species`, `eligible`, and `limit` query parameters. Results provide ranked candidates, criterion breakdowns, explanations, unknown criteria, warnings, and exclusion reasons.

The dashboard Matching page selects a real buyer order and displays the corresponding batch candidates. Quality grades currently use exact text equality because no approved grade hierarchy has been defined.

## Migrations

Run from `backend`:

```powershell
alembic current
alembic upgrade head
alembic revision --autogenerate -m "describe change"
alembic check
```

## Testing and build

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -v

cd ..\frontend
npm.cmd run build
```

## Troubleshooting

- `TEST_DATABASE_URL is required__: add it to root `.env` and ensure it targets `fungisync_test_db`.
- `Database unavailable__: confirm PostgreSQL is running, verify port/credentials, then call `/health`.
- `CORS error__: set `BACKEND_CORS_ORIGINS` to the Vite origin.
- `Frontend API error__: verify `VITE_API_BASE_URL` has no trailing slash and restart Vite after changing it.
- `Migration differs__: run `alembic check` and apply `alembic upgrade head`.
