# Axilattice Insight Engine v2

Pre-computed analytics engine with deep cube exploitation.
Connect data → cube builds once → every question is a lookup with "why".

Pre-computed analytics engine: Cube + Voice + NLU.  
Connect data → cube builds once → every question is a lookup.

---

## What's New in v2

| Feature | v1 | v2 |
|---------|-----|-----|
| Cross-dimensional filtering | ❌ | ✅ `"categories in South Region"` |
| Anomaly detection | ❌ | ✅ z-score based, ±2σ auto-flag |
| Period comparison | ❌ | ✅ Change arrows + explanations |
| Growth analysis (CAGR) | ❌ | ✅ |
| Seasonality detection | ❌ | ✅ Autocorrelation-based |
| Ranking with change tracking | ❌ | ✅ |
| "Why" generation | ❌ | ✅ Reads deltas + stats |
| Time hierarchy exploitation | Partial | Full — deltas at every grain |

## Architecture

```
frontend/ (React + Recharts)     → Vercel
backend/  (FastAPI + DuckDB)     → Render
```

### Core Design Decisions

| Problem in v1           | Fix in v2                                              |
|-------------------------|--------------------------------------------------------|
| Cross-dimensional filtering missing | `query_filtered_breakdown()` with JSON_EXTRACT |
| No "why" generation | `z_score`, `delta_pct`, `val_mean`, `val_stddev` in every response |
| Only 5 basic insight types | 10 types: +anomaly, comparison, growth, seasonality, ranking |
| Ephemeral in-memory cube | DuckDB persisted to disk on Render                    |
| Time deltas only for latest | Pre-computed at every grain, full hierarchy traversal |

---

## Deploy Backend (Render)

1. Push `backend/` to a GitHub repo
2. Create a new **Web Service** on [render.com](https://render.com)
3. Connect your repo → Render auto-detects `render.yaml`
4. Add env var: `ANTHROPIC_API_KEY = sk-ant-...`
5. Deploy → note your service URL: `https://axilattice-backend.onrender.com`

---

## Deploy Frontend (Vercel)

1. Push `frontend/` to GitHub
2. Import project on [vercel.com](https://vercel.com)
3. Add env var: `REACT_APP_API_URL = https://axilattice-backend.onrender.com`
4. Update `vercel.json` → replace `your-backend.onrender.com` with your Render URL
5. Deploy

---

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... uvicorn main:app --reload

# Frontend
cd frontend
npm install
REACT_APP_API_URL=http://localhost:8000 npm start
```

---

## Query Endpoints

| Endpoint              | Method | Description                          |
|-----------------------|--------|--------------------------------------|
| `/upload`             | POST   | Upload CSV/Excel/Parquet, build cube |
| `/query`              | POST   | NLU → cube lookup → card payload     |
| `/suggest`            | GET    | Contextual query suggestions         |
| `/schema`             | GET    | Schema + cube build status           |
| `/periods/{grain}`    | GET    | Available period keys                |
| `/dashboard`          | POST   | Save dashboard                       |
| `/dashboard/{id}`     | GET    | Load dashboard                       |
| `/health`             | GET    | Status check                         |

---

## Cube Design

The cube is a DuckDB table (`axl_cube`) with this schema:

```
grain       VARCHAR   -- day | week | month | quarter | year
period_key  VARCHAR   -- 2024-01 | 2024-Q1 | 2024 etc.
dim_combo   VARCHAR   -- region | region|category | __total__
dim_json    VARCHAR   -- {"region": "North"}
measure     VARCHAR   -- revenue | units | margin
val_sum     DOUBLE
val_count   BIGINT
val_min     DOUBLE
val_max     DOUBLE
val_mean    DOUBLE
val_stddev  DOUBLE
```

Deltas (period-over-period %) are pre-computed via `axl_deltas` using a LAG window.

**New in v2:** `axl_stats` table pre-computes distribution statistics (mean, stddev) per group for z-score anomaly detection.

---

## Cardinality Cutoff

Dimensions with > 50 distinct values are excluded from the cube (configurable in `profiler.py`).  
They remain queryable via DuckDB SQL fallback in `CubeEngine._raw()`.

**Why 50?** A bar chart with > 50 bars is unreadable. A cube cell for a dimension with 10,000 values wastes memory and produces noise, not insight.

---

## Insight Types

### Basic
- `breakdown` — Pie/bar chart of measure by dimension
- `trend` — Line chart over time with anomaly flags
- `topk` — Top N performers with concentration alerts
- `total` — Grand total with sparkline
- `cross` — Heatmap/matrix of two dimensions

### Deep (v2)
- `comparison` — Current vs prior period with change arrows
- `anomaly` — ±2σ z-score detection with severity classification
- `growth` — CAGR and total growth percentage
- `seasonality` — Autocorrelation-based period detection
- `ranking` — Rankings with change tracking

## Example Queries

```
"Categories in South Region"           → Cross-dimensional filter
"Revenue by category this quarter"     → Breakdown + anomaly detection
"Compare revenue by region vs last quarter" → Comparison with deltas
"Why did electronics revenue drop?"    → Anomaly + "why" generation
"Growth of revenue over time"          → CAGR analysis
"Seasonality in delivery times"        → Period detection
"Anomalies in revenue"                 → z-score scan
"Ranking of cities by revenue"         → Rankings
```

## Roadmap

- [x] Anomaly detection on cube deltas (±2σ auto-flag)
- [ ] Incremental CDC append (`CubeEngine.append()` is built, wire up `/append` endpoint)
- [ ] Multi-tenant (key cube by session token)
- [ ] Alert engine (threshold watchers on cube cells)
- [ ] Embedded iframe mode (drop into any BI tool)
