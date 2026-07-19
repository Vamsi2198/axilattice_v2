# AxiLattice Insight Engine v2

**Pre-computed analytics engine: Cube + Voice + NLU + Dashboard Builder**

> Connect data → cube builds once → every question is a lookup → voice builds dashboards

---

## What's New in v2

| Feature | v1 | v2 |
|---------|-----|-----|
| Multi-tenancy | ❌ Global state | ✅ Session-keyed DuckDB cubes |
| Dashboard persistence | ❌ In-memory dict | ✅ SQLite with CRUD |
| Conversation memory | ❌ Last 20 in array | ✅ SQLite + semantic context |
| Voice pipeline | ❌ Not implemented | ✅ `/voice` endpoint + Web Speech API |
| Streaming responses | ❌ Blocking JSON | ✅ SSE `/query/stream` |
| Alert engine | ❌ Roadmap item | ✅ Background poller + threshold watchers |
| Cube cleanup | ❌ Memory leak | ✅ TTL-based stale session eviction |
| API retry | ❌ Single attempt | ✅ Exponential backoff (3 attempts) |
| Frontend | ❌ Missing | ✅ Complete React SPA with Chart.js |
| Standalone mode | ❌ Missing | ✅ Zero-backend HTML file |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Voice Input │  │  Text Input │  │  Dashboard Builder (D&D) │ │
│  │  Web Speech  │  │  NLU Query  │  │  Layout: grid/wide/single│ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│         └─────────────────┴─────────────────────┘                │
│                           │                                      │
│                    ┌──────┴──────┐                               │
│                    │  React SPA   │ ← Chart.js, Tailwind          │
│                    │  (Vercel)    │                                │
│                    └──────┬──────┘                               │
└───────────────────────────┼─────────────────────────────────────┘
                            │ HTTP/SSE
┌───────────────────────────┼─────────────────────────────────────┐
│                      FASTAPI BACKEND                             │
│  ┌─────────────┐  ┌────────┴────────┐  ┌────────────────────┐ │
│  │  /upload    │  │  /query/stream  │  │  /voice            │ │
│  │  Profiler   │  │  SSE streaming  │  │  STT → NLU → TTS  │ │
│  │  Cube build │  │  Real-time UX   │  │  Voice suggestions │ │
│  └──────┬──────┘  └─────────────────┘  └────────────────────┘ │
│         │                                                        │
│  ┌──────┴────────────────────────────────────────────────────┐ │
│  │  SESSION STATE (isolated per user)                        │ │
│  │  • DuckDB: cube_{session_id}.duckdb  (Render disk)         │ │
│  │  • SQLite: conversations, dashboards, alerts (meta.db)     │ │
│  │  • In-memory: hot cache of active sessions                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│         │                                                        │
│  ┌──────┴────────────────────────────────────────────────────┐ │
│  │  BACKGROUND TASKS                                         │ │
│  │  • Cube builder (per upload)                               │ │
│  │  • Alert poller (every 60s)                                │ │
│  │  • Stale session cleanup (every 5min, TTL=24h)             │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Deploy on Render (Single Service)

1. Push the full repository to GitHub
2. Create new **Web Service** on [render.com](https://render.com)
3. Connect repo → Render auto-detects root `render.yaml`
4. Add env var: `ANTHROPIC_API_KEY = sk-ant-...`
5. Deploy → use one URL for both UI and API

The root `render.yaml` configures:
- **Disk mount** at `/opt/render/project/data` (5GB) for persistent DuckDB + SQLite
- **Python 3.11** environment with uvicorn
- **Single start command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Frontend is served by FastAPI from `frontend/public`, so one Render web service runs both frontend and backend.

---

## Deploy Frontend (Vercel)

1. Push `frontend/public/` to GitHub (or any static host)
2. Import on [vercel.com](https://vercel.com)
3. Add env var: `REACT_APP_API_URL = https://axilattice-backend-v2.onrender.com`
4. Deploy

The frontend is a **zero-build React SPA** using CDN-loaded React + Babel standalone. No `npm install` needed. Just serve the `public/` folder.

---

## Standalone Mode (No Backend)

Open `standalone/index.html` in any browser. Drop a CSV. Everything runs client-side:
- CSV parser (RFC-4180 compliant)
- Profiler (same logic as backend)
- Cube builder (JavaScript port of DuckDB logic)
- Query resolution (fallback NLU)
- Chart rendering (Chart.js)

**Limitations of standalone:**
- No Claude NLU (regex fallback only)
- No persistent dashboards
- No alerts
- No multi-user
- CSV only (no Excel/Parquet)

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload` | POST | Upload CSV/Excel/Parquet, build cube per session |
| `/query` | POST | NLU → cube lookup → card payload |
| `/query/stream` | POST | SSE streaming for real-time voice UX |
| `/voice` | POST | Audio base64 or text fallback → voice-optimized card |
| `/suggest` | GET | Contextual query suggestions |
| `/schema` | GET | Schema + cube build status per session |
| `/periods/{grain}` | GET | Available period keys |
| `/dashboard` | POST/GET | Save / list dashboards (persistent) |
| `/dashboard/{id}` | GET | Load specific dashboard |
| `/alerts` | POST/GET/DELETE | Threshold alert CRUD |
| `/conversations/{session_id}` | GET | Conversation history |
| `/health` | GET | Status + active session count |

---

## Voice-First Dashboard Builder Flow

```
User: "Show me revenue by region this month"
  → NLU: {insight_type: "breakdown", measure: "revenue", dimension: "region", grain: "month"}
  → Cube lookup: O(1) indexed read
  → Card rendered: bar chart + summary

User: "Add that to my dashboard"
  → Frontend: pushes card to dashboard builder state
  → User: "Make it a wide layout"
  → Frontend: switches layout to 2-column
  → User: "Save as Q3 Overview"
  → POST /dashboard → SQLite persistence

User: "Alert me when revenue drops 10% month over month"
  → POST /alerts → background poller watches cube deltas
  → Triggered: webhook/notification dispatched
```

---

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... uvicorn main:app --reload --port 8000

# Frontend (just serve static files)
cd frontend/public
python -m http.server 3000
# Open http://localhost:3000
```

### One-command start (backend + frontend)

Windows (PowerShell or CMD):

```bat
start.bat
```

macOS/Linux:

```bash
./start.sh all
```

This starts:
- Backend at `http://localhost:8000`
- Frontend at `http://localhost:3000`

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

---

## Cardinality Cutoff

Dimensions with > 50 distinct values are excluded from the cube. They remain queryable via DuckDB SQL fallback. **Why 50?** A bar chart with > 50 bars is unreadable. A cube cell for a dimension with 10,000 values wastes memory and produces noise, not insight.

---

## Roadmap → L99

- [ ] Anomaly detection on cube deltas (±2σ auto-flag)
- [ ] Incremental CDC append (`CubeEngine.append()` built, wire up `/append`)
- [ ] Multi-tenant with auth (JWT + workspace isolation)
- [ ] Semantic similarity for suggestions (embed query history, cosine search)
- [ ] Embedded iframe mode (drop into any BI tool)
- [ ] Real-time collaboration (WebSocket shared cursors on dashboards)
- [ ] Natural language DAX/SQL generation for high-cardinality dims
- [ ] Mobile-native voice app (React Native + Whisper on-device)
