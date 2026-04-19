# Quant Dip Finder — Deployment Guide

## Prerequisites
- Docker + Docker Compose v2
- Python 3.11+ (for local dev)
- Node.js 20+ (for frontend dev)
- API keys: Polygon.io, NewsAPI, FMP (Financial Modeling Prep)

---

## Quick Start (Docker — recommended)

### 1. Clone and configure
```bash
git clone https://github.com/yourorg/quant-platform
cd quant-platform
cp .env.example .env
# Edit .env — add your API keys
nano .env
```

### 2. Start all services
```bash
docker compose up -d
# Services started:
#   db       → TimescaleDB on :5432
#   redis    → Redis on :6379
#   api      → FastAPI on :8000
#   worker   → Celery worker
#   beat     → Celery beat scheduler
#   frontend → React on :3000
```

### 3. Run database migrations
```bash
docker compose exec api alembic upgrade head
```

### 4. Seed the ticker universe
```bash
docker compose exec worker python -c "
from workers.tasks import refresh_ticker_universe
refresh_ticker_universe()
"
```

### 5. Trigger initial scan
```bash
curl -X POST http://localhost:8000/api/scan/trigger
```

### 6. Open dashboard
Navigate to: http://localhost:3000

---

## Local Development (no Docker)

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start TimescaleDB + Redis separately (Docker is easiest):
docker run -d -p 5432:5432 -e POSTGRES_USER=quant -e POSTGRES_PASSWORD=quant_secret \
  -e POSTGRES_DB=quant_db timescale/timescaledb:latest-pg15

docker run -d -p 6379:6379 redis:7-alpine

# Run migrations
alembic upgrade head

# Start API
uvicorn api.main:app --reload --port 8000

# Start worker (separate terminal)
celery -A workers.tasks worker --loglevel=info

# Start beat scheduler (separate terminal)
celery -A workers.tasks beat --loglevel=info
```

### Frontend
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

---

## API Keys Setup

### Polygon.io
1. Sign up at https://polygon.io
2. Free tier: 5 API calls/minute (limited)
3. Starter tier ($29/mo): unlimited REST calls — recommended for production
4. Set POLYGON_API_KEY in .env

### NewsAPI
1. Sign up at https://newsapi.org
2. Free tier: 100 requests/day, developer tier: 500/day
3. Set NEWS_API_KEY in .env

### Financial Modeling Prep
1. Sign up at https://financialmodelingprep.com
2. Free tier: 250 requests/day
3. Set FMP_API_KEY in .env

---

## Configuration Tuning

Key .env settings to tune for your use case:

```bash
# Scan frequency (seconds)
SCAN_INTERVAL_SECONDS=300        # 5 minutes — reduce to 60 for more aggressive scanning
WATCHLIST_SCAN_SECONDS=60        # 1 minute for watchlisted tickers

# Liquidity filters (increase for safer, more liquid names)
MIN_MARKET_CAP=500000000         # $500M — set to 2B for large caps only
MIN_AVG_VOLUME=1000000           # 1M shares/day
MIN_PRICE=5.0                    # No stocks below $5

# Signal thresholds (higher = fewer but higher quality signals)
DIP_SCORE_THRESHOLD=55.0         # Only process tickers scoring 55+
```

---

## Production Deployment (AWS/GCP)

### Recommended stack
- **DB**: RDS PostgreSQL 15 + TimescaleDB extension or self-managed EC2
- **Cache**: ElastiCache Redis
- **API**: ECS Fargate (2 vCPU, 4GB RAM per task, 2–4 replicas)
- **Workers**: ECS Fargate (2 vCPU, 8GB RAM — pandas is memory-hungry)
- **Beat**: Single ECS task (singleton — do NOT scale beat horizontally)
- **Frontend**: CloudFront + S3 (static build)

### Environment variables in production
Set all .env variables as ECS task definition environment variables or
AWS Secrets Manager references.  Never commit .env to version control.

### Horizontal scaling
Workers scale independently — add more ECS tasks to increase throughput.
The API is stateless and scales horizontally.
Beat must remain a single instance.

---

## Monitoring

### Celery Flower (task monitor)
```bash
docker compose exec worker celery -A workers.tasks flower --port=5555
# Access at http://localhost:5555
```

### Health check
```bash
curl http://localhost:8000/health
# {"status": "ok", "ts": "...", "ws_connections": 2}
```

### Logs
```bash
docker compose logs -f api      # API logs
docker compose logs -f worker   # Worker logs
docker compose logs -f beat     # Scheduler logs
```

---

## Data Pipeline Overview

```
Polygon.io ──► [Ingestion] ──► TimescaleDB (price_bars)
                                      │
                               [Scanner worker]
                                      │
                         ┌────────────┴────────────┐
                  [Dip Engine]              [News Service]
                  RSI/BB/MA/Z-score         NewsAPI / Benzinga
                  Mean reversion            Yahoo RSS
                         │                         │
                         └────────────┬────────────┘
                                [Decision Engine]
                                composite score
                                      │
                              PostgreSQL (signals)
                                      │
                         ┌────────────┴────────────┐
                   FastAPI REST                 WebSocket
                         │                         │
                    React Dashboard ◄─────────────┘
```

---

## Troubleshooting

**Scan produces 0 signals**
- Check API keys are valid: `curl "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL&apiKey=YOUR_KEY"`
- Lower DIP_SCORE_THRESHOLD to 30 temporarily to verify pipeline works
- Check ticker universe was seeded: `SELECT COUNT(*) FROM tickers WHERE is_active=true;`

**TimescaleDB hypertable error**
- Ensure you're using `timescale/timescaledb` image, NOT plain `postgres`
- Run: `SELECT * FROM timescaledb_information.hypertables;` to verify

**WebSocket not connecting**
- Check CORS settings in `api/main.py` match your frontend URL
- Verify nginx is proxying WebSocket: add `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade;`

**Celery tasks not running**
- Verify Redis is reachable: `redis-cli -u $REDIS_URL ping`
- Check beat schedule: `celery -A workers.tasks inspect scheduled`
