# Railway Deployment Guide

## What changed from the error you saw

Railway builds from the **repo root**, not from subdirectories.
The original Dockerfile inside `backend/` couldn't find `requirements.txt`
because the build context was wrong.

Fix: four root-level Dockerfiles, one per service:
- `Dockerfile.api`      → FastAPI server
- `Dockerfile.worker`   → Celery worker
- `Dockerfile.beat`     → Celery beat scheduler
- `Dockerfile.frontend` → React dashboard

---

## Step-by-step setup

### 1. Push your repo to GitHub
The root of the repo should look like:
```
Dockerfile.api
Dockerfile.worker
Dockerfile.beat
Dockerfile.frontend
railway.json
backend/
frontend/
```

### 2. Create a Railway project
Go to https://railway.app → New Project → Empty Project

### 3. Add Postgres
+ New → Database → Add PostgreSQL
Copy the DATABASE_URL from its Variables tab.

### 4. Add Redis
+ New → Database → Add Redis
Copy the REDIS_URL from its Variables tab.

### 5. Deploy the API service
1. + New → GitHub Repo → select your repo
2. Railway auto-detects railway.json and uses Dockerfile.api
3. Name the service "api"
4. Add these environment variables:

DATABASE_URL          = (from step 3)
REDIS_URL             = (from step 4)
CELERY_BROKER_URL     = (same as REDIS_URL)
CELERY_RESULT_BACKEND = (same as REDIS_URL)
POLYGON_API_KEY       = your_polygon_key
NEWS_API_KEY          = your_newsapi_key
SECRET_KEY            = any_random_string
ENVIRONMENT           = production
MIN_MARKET_CAP        = 500000000
MIN_AVG_VOLUME        = 1000000
MIN_PRICE             = 5.0

### 6. Deploy the Worker service
1. + New → GitHub Repo → same repo
2. Settings → Build → Dockerfile path: Dockerfile.worker
3. Name it "worker", same env vars as API

### 7. Deploy the Beat service
1. + New → GitHub Repo → same repo
2. Settings → Build → Dockerfile path: Dockerfile.beat
3. Name it "beat", same env vars, set Replicas to 1

### 8. Deploy the Frontend
1. + New → GitHub Repo → same repo
2. Settings → Build → Dockerfile path: Dockerfile.frontend
3. Add: VITE_API_URL = https://your-api.up.railway.app
        VITE_WS_URL  = wss://your-api.up.railway.app

### 9. Run migration once (in the API service shell)
alembic upgrade head

### 10. Trigger first scan
curl -X POST https://your-api.up.railway.app/api/scan/trigger

---

## Common errors

"failed to compute cache key: requirements.txt not found"
→ Service is still using backend/Dockerfile. Go to service Settings → Build
  → change Dockerfile path to Dockerfile.api (or .worker / .beat / .frontend)

"ModuleNotFoundError: No module named api"
→ Add PYTHONPATH=/app to the service environment variables.

"Worker connects but tasks dont run"
→ CELERY_BROKER_URL must exactly match REDIS_URL in both API and Worker services.

"Frontend blank page"
→ VITE_API_URL is missing or set to localhost. Must be the full https:// Railway URL.
  Rebuild the frontend after changing it.
