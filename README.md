# FungiSync – Mushroom Distribution Intelligence

FungiSync is a mushroom distribution management platform designed to coordinate growers, harvest batches, buyers, orders, and supply-demand matching.

The project combines a **React + FastAPI + PostgreSQL application** with a **standalone interactive distribution operations demo** that demonstrates the broader product vision.

The long-term goal is to support intelligent coordination across the mushroom distribution lifecycle, from harvest registration and demand matching to allocation, collection, dispatch, and dynamic replanning.

---

## 1. Project Overview

FungiSync currently contains two frontend experiences:

### A. Main application – React + FastAPI

The primary application provides:

- Grower management
- Harvest batch management
- Buyer management
- Buyer order management
- Coordinator dashboard
- Deterministic supply-demand matching
- PostgreSQL-backed persistence
- REST APIs and interactive API documentation

This application uses the FastAPI backend and PostgreSQL database as its source of truth.

### B. Standalone distribution operations demo

The root-level `index.html` provides a standalone interactive product demo featuring:

- Cinematic mushroom cultivation landing page
- Forest-inspired visual design
- Overview dashboard with illustrative operational statistics
- Grower and harvest workspaces
- Buyer and order workspaces
- Supply-demand matching
- Allocation proposals
- Collection and dispatch
- Dynamic replanning
- Agent monitor
- Reports and settings
- English, Hindi, and Tamil language options

The standalone demo runs directly in a modern browser without a frontend build step or package installation.

**Important:** The standalone demo uses browser `localStorage` for its records. It is not connected to the FastAPI backend or PostgreSQL database. Its allocation, dispatch, and replanning workflows are demonstrations, not production backend services.

---

## 2. Technology Stack

| Layer                | Technologies                             |
| -------------------- | ---------------------------------------- |
| Frontend application | React 18, TypeScript, Vite, Tailwind CSS |
| Backend              | Python 3.12, FastAPI, Pydantic           |
| ORM                  | SQLAlchemy                               |
| Database             | PostgreSQL                               |
| Database migrations  | Alembic                                  |
| PostgreSQL driver    | Psycopg                                  |
| Matching engine      | Python deterministic scoring             |
| Standalone demo      | HTML, CSS, JavaScript                    |
| Demo verification    | Node.js                                  |

---

## 3. Project Structure

```text
FungiSync/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── database.py
│   │   └── main.py
│   │
│   ├── alembic/
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── services/
│   │   ├── types/
│   │   └── ...
│   ├── .env.example
│   └── package.json
│
├── index.html
├── fungisync-hero.png
├── fungisync-overview.mp4
├── verify-app.cjs
├── README.md
└── .gitignore
```

The React application is located inside `frontend/`. The standalone demo is provided separately at the repository root.

---

## 4. Prerequisites

Install the following:

- Python 3.12
- Node.js 20 or newer
- PostgreSQL 13 or newer
- Git
- npm

---

## 5. Database Setup

FungiSync uses PostgreSQL for persistent application data.

Create the development and test databases:

```sql
CREATE DATABASE fungisync_db;
CREATE DATABASE fungisync_test_db;
```

The test database is separate because the automated test suite may recreate its tables.

Never configure the test database to point to your development database.

---

## 6. Environment Configuration

### Backend

From the project root, copy the environment template:

```powershell
Copy-Item .env.example .env
```

Configure the database connection in the root `.env` file:

```env
APP_ENV=development

DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5433/fungisync_db

TEST_DATABASE_URL=postgresql+psycopg://postgres:<test-password>@localhost:5433/fungisync_test_db

BACKEND_CORS_ORIGINS=http://localhost:5173
```

Replace the placeholders with your local PostgreSQL credentials and actual port.

### Frontend

Copy the frontend environment template:

```powershell
Copy-Item frontend/.env.example frontend/.env
```

Set the backend API URL:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Do not commit local `.env` files or database credentials.

---

## 7. Running the Main Application

The main application requires the FastAPI backend and React frontend to run separately.

### Step 1: Start the backend

Open PowerShell:

```powershell
cd backend

python -m venv .venv

.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt

alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

Backend:

- API: http://localhost:8000
- Health check: http://localhost:8000/health
- Swagger documentation: http://localhost:8000/docs

### Step 2: Start the frontend

Open another PowerShell terminal:

```powershell
cd frontend

npm.cmd install

npm.cmd run dev
```

Open the Vite URL, normally:

http://localhost:5173

Make sure the backend is running and `VITE_API_BASE_URL` points to the correct backend address.

---

## 8. Phase 1 – Core Distribution Management

Phase 1 establishes the application's CRUD foundation.

### Grower Management

- Register and manage growers.
- Store grower contact details and locations.
- View associated harvest batches.

### Harvest Batch Management

- Register mushroom harvest batches.
- Associate batches with growers.
- Record species, expected quantity, confirmed quantity, harvest date, and status.
- Validate quantities and relationships.

### Buyer Management

- Register and manage buyers.
- Store buyer contact information and locations.

### Buyer Order Management

- Create and manage buyer orders.
- Record species, quantity, price limit, delivery date, and order status.
- Maintain buyer-order relationships.

### Coordinator Dashboard

- View operational records and summary information.
- Monitor grower, batch, buyer, and order data.

The backend provides REST APIs with validation, database persistence, and appropriate error handling.

---

## 9. Phase 2 – Deterministic Supply-Demand Matching

Phase 2 introduces inspection-only supply-demand matching.

The matching engine evaluates harvest batches against buyer orders and returns ranked candidate matches with explanations.

### Eligibility criteria

Hard eligibility rules include:

- Exact normalized mushroom species match.
- Batch status must be `confirmed` or `harvested`.
- Positive confirmed batch quantity.
- Harvest date must not be after the requested delivery date.
- Delivery date must not exceed a known best-before date.
- Known asking price must not exceed the buyer's price limit.
- Known quality grades must match exactly after whitespace and case normalization.

### Compatibility scoring

Eligible candidates are scored using the following weighted criteria:

| Criterion                 |  Weight |
| ------------------------- | ------: |
| Quantity coverage         |      30 |
| Freshness/date            |      25 |
| Price                     |      20 |
| Quality                   |      15 |
| Exact location text match |      10 |
| **Total**                 | **100** |

Scores use known criteria and are normalized to a 0–100 scale.

Missing information is reported separately through data-completeness indicators and unknown-data warnings.

### Matching API endpoints

```http
GET /matches/orders/{order_id}

GET /matches/batches/{batch_id}
```

Both endpoints support optional query parameters:

- `minimum_score`
- `species`
- `eligible`
- `limit`

Results include ranked candidates, criterion breakdowns, explanations, unknown criteria, warnings, and exclusion reasons.

### Matching limitations

- Quality grades currently use exact normalized text equality. No quality hierarchy has been assumed.
- Location matching uses exact normalized text comparison. Geographic distance and travel time are not calculated.
- Matching is inspection-only. It does not reserve stock, consume quantities, create allocations, or modify orders or batches.

---

## 10. Standalone Demo – Running the Product Experience

The standalone demo can be opened independently of the React application.

### Option 1: Open directly

Open `index.html` in a modern browser.

### Option 2: Use a local server

From the project root:

```powershell
python -m http.server 8000
```

Open:

http://localhost:8000

If the main backend is already running on port 8000, use another port for the demo:

```powershell
python -m http.server 8080
```

Then open:

http://localhost:8080

### Demo workspaces

The standalone demo showcases:

1. Overview dashboard
2. Grower and harvest registry
3. Buyer and order registry
4. Supply-demand matching
5. Allocation proposals and coordinator review
6. Collection and dispatch
7. Dynamic replanning
8. Agent monitor
9. Operational reports
10. Settings

### Demo sign-in

The standalone demo does not include production authentication.

Use **Continue in demo mode** to enter the sample workspace. Password-based sign-in is unavailable because no authentication service has been supplied.

The demo session is limited to the current browser tab.

### Demo data and persistence

- Local changes are stored using browser `localStorage`.
- JSON export and restore are available.
- A reset option restores the starter dataset.
- Demo records are illustrative and are not authoritative multi-user business records.

The standalone demo does not claim production server synchronization, enforceable role permissions, live geographic routing, trip execution, buyer acceptance, or actual AI-agent telemetry.

---

## 11. Landing Page and Visual Assets

The standalone landing page uses a mushroom-forest video as its full-height hero background, with a dark readability overlay and a static image fallback.

Assets:

- `fungisync-hero.png` – hero poster and fallback image.
- `fungisync-overview.mp4` – looping forest fungi video.

The interface includes restrained pointer parallax, camera drift, reduced-motion support, and a six-step workflow presentation.

The overview video:

- Uses muted 720p MP4 playback.
- Loads metadata initially.
- Pauses while the browser document is hidden.
- Respects reduced-motion preferences.
- Falls back to a static image when playback is unavailable.

The video is credited to Edie Reed via [Pexels](https://www.pexels.com/video/forest-mushrooms-5684469/).

The visual flow lines in the demo are derived from local sample records. They do not represent geographic maps or live routes.

---

## 12. Database Migrations

Run these commands from the `backend/` directory:

```powershell
alembic current

alembic upgrade head

alembic revision --autogenerate -m "describe change"

alembic check
```

Use Alembic to manage database schema changes.

---

## 13. Testing and Verification

### Backend tests

Configure `TEST_DATABASE_URL` in the root `.env` file before running tests.

From the backend directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

The test suite covers database schema, relationships, constraints, API behavior, and related application functionality.

The test suite must use the isolated test database, not the development database.

### React frontend production build

From the frontend directory:

```powershell
npm.cmd run build
```

### Standalone demo verification

The standalone demo includes a dependency-free Node.js verification script.

From the repository root:

```powershell
node verify-app.cjs
```

This script exercises demo workflows including:

- Demo entry and language options
- Grower creation
- Harvest quantity validation
- Matching
- Allocation validation and approval
- Dispatch capacity and stops
- Order cancellation
- Impact analysis
- Revised allocation and dispatch proposals
- Persistence after application reinitialization

These checks validate the standalone demo, not the React/FastAPI backend.

---

## 14. Current Scope and Future Development

The current implementation focuses on distribution data management and deterministic matching.

The broader FungiSync roadmap includes:

| Phase   | Planned capability                           |
| ------- | -------------------------------------------- |
| Phase 1 | Core distribution management                 |
| Phase 2 | Deterministic supply-demand matching         |
| Phase 3 | Coordinator-reviewed allocation engine       |
| Phase 4 | Collection and dispatch management           |
| Phase 5 | Dynamic replanning and exception handling    |
| Phase 6 | AI-assisted coordination and agent workflows |

Future allocation and dispatch functionality will require dedicated backend services, explicit quantity reservation rules, validation, and audit trails.

AI components may assist with interpretation, explanations, and recommendations, while deterministic services will handle quantity calculations, constraints, and allocation validation.

Consequential actions will require coordinator approval.

---

## 15. Troubleshooting

| Issue                    | Suggested resolution                                                              |
| ------------------------ | --------------------------------------------------------------------------------- |
| Database unavailable     | Verify PostgreSQL is running, check the port and credentials, and test `/health`. |
| Test database error      | Configure `TEST_DATABASE_URL` and ensure it points to `fungisync_test_db`.        |
| CORS error               | Set `BACKEND_CORS_ORIGINS` to the React development server origin.                |
| Frontend API error       | Verify `VITE_API_BASE_URL` and restart Vite after changing `.env`.                |
| Migration mismatch       | Run `alembic check` and `alembic upgrade head`.                                   |
| Demo data not persisting | Check browser localStorage settings and browser privacy restrictions.             |
| Demo video unavailable   | Verify the MP4 asset exists and use the static hero image fallback.               |

---

## 16. License and Contributions

This repository contains the FungiSync application, its backend services, frontend experiences, and demonstration assets.

Contributions should preserve the distinction between the standalone demo and the backend-connected React application unless an explicit integration is implemented.
