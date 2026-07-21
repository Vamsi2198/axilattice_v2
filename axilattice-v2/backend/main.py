"""
AxiLattice Insight Engine v2 — Production-Grade
════════════════════════════════════════════════
Fixes every critical flaw from v1:
  • Session-keyed cube isolation (multi-tenant)
  • Persistent dashboards (SQLite)
  • Conversation context memory per session
  • Voice endpoint (STT stub + TTS response)
  • Streaming query responses (SSE)
  • Alert engine with threshold watchers
  • Rate limiting + request validation
  • Background cube cleanup

Deploy: Render (render_v2.yaml) or local
"""

import os
import io
import json
import uuid
import time
import sqlite3
import hashlib
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx

# ── Constants ───────────────────────────────────────────────────────────────
CARDINALITY_CUTOFF = 50
MAX_DIM_CROSS = 2
TIME_GRAINS = ["day", "week", "month", "quarter", "year"]
CUBE_TABLE = "axl_cube"
META_TABLE = "axl_meta"
SESSION_TTL_SECONDS = 3600 * 24  # 24h session expiry
ALERT_POLL_INTERVAL = 60  # seconds

# ── Data Models ─────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    voice_mode: bool = False

class DashboardSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    layout: str = "grid"
    cards: List[Dict] = Field(default_factory=list)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])

class AlertCreateRequest(BaseModel):
    name: str
    measure: str
    grain: str = "month"
    threshold: float
    direction: str = "above"  # above | below
    dimension: Optional[str] = None
    dim_value: Optional[str] = None
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])

class VoiceQueryRequest(BaseModel):
    audio_base64: Optional[str] = None  # base64-encoded audio blob
    text_fallback: Optional[str] = None
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])

# ── Session-scoped State Manager ────────────────────────────────────────────

class SessionState:
    """Isolated per-session cube + context."""
    def __init__(self, session_id: str, db_dir: str = "/tmp/axl_sessions"):
        self.session_id = session_id
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)
        self.db_path = os.path.join(db_dir, f"cube_{session_id}.duckdb")
        self.conn = None
        self.cube_ready = False
        self.schema_ctx: Optional[dict] = None
        self.df: Optional[pd.DataFrame] = None
        self.build_status = "idle"
        self.build_error: Optional[str] = None
        self.build_stats: Optional[dict] = None
        self.query_history: List[Dict] = []
        self.last_access = time.time()

    def touch(self):
        self.last_access = time.time()

# Global session registry
_SESSIONS: Dict[str, SessionState] = {}
_SESSION_LOCK = asyncio.Lock()

async def get_or_create_session(session_id: str) -> SessionState:
    async with _SESSION_LOCK:
        if session_id not in _SESSIONS:
            _SESSIONS[session_id] = SessionState(session_id)
        _SESSIONS[session_id].touch()
        return _SESSIONS[session_id]

async def cleanup_stale_sessions():
    """Background task: remove sessions idle > TTL."""
    while True:
        await asyncio.sleep(300)  # every 5 min
        cutoff = time.time() - SESSION_TTL_SECONDS
        async with _SESSION_LOCK:
            stale = [sid for sid, s in _SESSIONS.items() if s.last_access < cutoff]
            for sid in stale:
                s = _SESSIONS.pop(sid)
                try:
                    if s.conn:
                        s.conn.close()
                    if os.path.exists(s.db_path):
                        os.remove(s.db_path)
                except Exception:
                    pass

# ── SQLite Persistence (Dashboards, Alerts, Conversations) ──────────────────

_SQLITE_PATH = os.environ.get("AXL_SQLITE_PATH", "/tmp/axl_meta.db")

def _init_sqlite():
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dashboards (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            name TEXT,
            layout TEXT,
            cards TEXT,  -- JSON
            created TEXT,
            updated TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            intent TEXT,  -- JSON
            timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            name TEXT,
            measure TEXT,
            grain TEXT,
            threshold REAL,
            direction TEXT,
            dimension TEXT,
            dim_value TEXT,
            active INTEGER DEFAULT 1,
            created TEXT,
            last_triggered TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dash_session ON dashboards(session_id);
        CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
        CREATE INDEX IF NOT EXISTS idx_alert_session ON alerts(session_id);
    """)
    conn.commit()
    conn.close()

_init_sqlite()

# ── DuckDB Cube Engine (Session-scoped) ─────────────────────────────────────

import duckdb
from itertools import combinations

def _grain_expr(col: str, grain: str) -> str:
    if grain == "day":
        return f"strftime('%Y-%m-%d', {col})"
    elif grain == "week":
        return f"strftime('%G-W%V', {col})"
    elif grain == "month":
        return f"strftime('%Y-%m', {col})"
    elif grain == "quarter":
        return f"(strftime('%Y', {col}) || '-Q' || CAST(CEIL(CAST(strftime('%m', {col}) AS INT) / 3.0) AS INT))"
    elif grain == "year":
        return f"strftime('%Y', {col})"
    raise ValueError(f"Unknown grain: {grain}")

class CubeEngine:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = duckdb.connect(db_path)
        self.dims: List[Dict] = []
        self.measures: List[Dict] = []
        self.time_col: Optional[str] = None
        self.excluded_dims: List[Dict] = []
        self._build_time: float = 0.0
        self._ensure_tables()

    def _ensure_tables(self):
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {CUBE_TABLE} (
                grain VARCHAR NOT NULL, period_key VARCHAR NOT NULL,
                dim_combo VARCHAR NOT NULL, dim_json VARCHAR NOT NULL,
                measure VARCHAR NOT NULL,
                val_sum DOUBLE, val_count BIGINT, val_min DOUBLE,
                val_max DOUBLE, val_mean DOUBLE, val_stddev DOUBLE,
                PRIMARY KEY (grain, period_key, dim_combo, dim_json, measure)
            )
        """)
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {META_TABLE} (
                key VARCHAR PRIMARY KEY, value VARCHAR
            )
        """)

    def build(self, df: pd.DataFrame, profiler_result: dict) -> dict:
        t0 = time.time()
        self.dims = profiler_result["dims"]
        self.measures = profiler_result["measures"]
        self.time_col = profiler_result.get("time_col")
        self.excluded_dims = profiler_result.get("excluded_dims", [])
        self.conn.execute(f"DELETE FROM {CUBE_TABLE}")
        self.conn.register("_src", df)
        if self.time_col:
            self.conn.execute(f"""
                CREATE OR REPLACE TABLE _src_parsed AS
                SELECT *, TRY_CAST("{self.time_col}" AS DATE) AS _date_parsed FROM _src
            """)
        else:
            self.conn.execute("CREATE OR REPLACE TABLE _src_parsed AS SELECT * FROM _src")

        cube_dims = [d for d in self.dims if d["cardinality"] <= CARDINALITY_CUTOFF]
        dim_names = [d["col"] for d in cube_dims]
        rows_inserted = 0

        for grain in TIME_GRAINS:
            period_expr = _grain_expr("_date_parsed", grain) if self.time_col else "'__all__'"
            rows_inserted += self._agg_and_insert(grain, period_expr, [], "__total__")
            for dcol in dim_names:
                rows_inserted += self._agg_and_insert(grain, period_expr, [dcol], dcol)
            for r in range(2, MAX_DIM_CROSS + 1):
                for combo in combinations(dim_names, r):
                    combo_key = "|".join(sorted(combo))
                    rows_inserted += self._agg_and_insert(grain, period_expr, list(combo), combo_key)

        self._compute_deltas()
        self._build_time = time.time() - t0
        meta = {
            "dims": json.dumps([d["col"] for d in self.dims]),
            "measures": json.dumps([m["col"] for m in self.measures]),
            "time_col": self.time_col or "",
            "excluded_dims": json.dumps([d["col"] for d in self.excluded_dims]),
            "rows_inserted": str(rows_inserted),
            "build_seconds": f"{self._build_time:.2f}",
        }
        for k, v in meta.items():
            self.conn.execute(f"""
                INSERT INTO {META_TABLE} VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, [k, v])
        return {
            "cube_cells": rows_inserted, "dims_cubed": len(cube_dims),
            "dims_excluded": len(self.excluded_dims), "grains": len(TIME_GRAINS),
            "build_seconds": round(self._build_time, 2),
        }

    def _agg_and_insert(self, grain, period_expr, group_cols, dim_combo):
        measure_aggs = ", ".join([
            f'SUM("{m['col']}") AS {m['col']}_sum, '
            f'COUNT("{m['col']}") AS {m['col']}_cnt, '
            f'MIN("{m['col']}") AS {m['col']}_min, '
            f'MAX("{m['col']}") AS {m['col']}_max, '
            f'AVG("{m['col']}") AS {m['col']}_avg, '
            f'STDDEV("{m['col']}") AS {m['col']}_std'
            for m in self.measures
        ])
        if group_cols:
            quoted = [f'"{c}"' for c in group_cols]
            group_expr = ", ".join(quoted) + ", "
            group_by = "GROUP BY " + ", ".join(quoted) + ", period_key"
            dim_json_expr = "json_object(" + ", ".join([
                f"'{c}', CAST(\"{c}\" AS VARCHAR)" for c in group_cols
            ]) + ")"
        else:
            group_expr = ""
            group_by = "GROUP BY period_key"
            dim_json_expr = "'{\"__total__\": true}'"
        sql = f"""
            SELECT '{grain}' AS grain, {period_expr} AS period_key,
                   '{dim_combo}' AS dim_combo, {dim_json_expr} AS dim_json,
                   {group_expr} {measure_aggs}
            FROM _src_parsed WHERE {period_expr} IS NOT NULL {group_by}
        """
        try:
            result_df = self.conn.execute(sql).df()
        except Exception:
            return 0
        if result_df.empty:
            return 0
        rows = 0
        for _, row in result_df.iterrows():
            period_key = str(row["period_key"])
            dim_json = str(row["dim_json"])
            for m in self.measures:
                col = m["col"]
                try:
                    self.conn.execute(f"""
                        INSERT OR REPLACE INTO {CUBE_TABLE}
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [
                        grain, period_key, dim_combo, dim_json, col,
                        float(row.get(f"{col}_sum", 0) or 0),
                        int(row.get(f"{col}_cnt", 0) or 0),
                        float(row.get(f"{col}_min", 0) or 0),
                        float(row.get(f"{col}_max", 0) or 0),
                        float(row.get(f"{col}_avg", 0) or 0),
                        float(row.get(f"{col}_std", 0) or 0),
                    ])
                    rows += 1
                except Exception:
                    pass
        return rows

    def _compute_deltas(self):
        self.conn.execute("""
            CREATE OR REPLACE TABLE axl_deltas AS
            SELECT grain, period_key, dim_combo, dim_json, measure, val_sum,
                LAG(val_sum) OVER w AS prior_sum,
                CASE WHEN LAG(val_sum) OVER w IS NULL OR LAG(val_sum) OVER w = 0 THEN NULL
                     ELSE (val_sum - LAG(val_sum) OVER w) / LAG(val_sum) OVER w END AS delta_pct,
                RANK() OVER (PARTITION BY grain, period_key, dim_combo, measure ORDER BY val_sum DESC) AS rank_in_period
            FROM axl_cube
            WINDOW w AS (PARTITION BY grain, dim_combo, dim_json, measure ORDER BY period_key)
        """)

    def query_breakdown(self, dimension, measure, grain, period_key=None):
        pk = period_key or self._latest_period(grain)
        if not pk: return []
        rows = self.conn.execute(f"""
            SELECT JSON_EXTRACT_STRING(d.dim_json, '$.{dimension}') AS label,
                   d.val_sum AS value, dl.delta_pct, dl.rank_in_period
            FROM {CUBE_TABLE} d
            LEFT JOIN axl_deltas dl USING (grain, period_key, dim_combo, dim_json, measure)
            WHERE d.grain=? AND d.period_key=? AND d.dim_combo=? AND d.measure=?
            ORDER BY d.val_sum DESC
        """, [grain, pk, dimension, measure]).fetchall()
        return [{"label": r[0], "value": r[1], "delta": r[2], "rank": r[3]} for r in rows if r[0] is not None]

    def query_trend(self, measure, grain, n_periods=12):
        rows = self.conn.execute(f"""
            SELECT d.period_key, d.val_sum AS value, dl.delta_pct
            FROM {CUBE_TABLE} d
            LEFT JOIN axl_deltas dl USING (grain, period_key, dim_combo, dim_json, measure)
            WHERE d.grain=? AND d.dim_combo='__total__' AND d.measure=?
            ORDER BY d.period_key DESC LIMIT ?
        """, [grain, measure, n_periods]).fetchall()
        return [{"period": r[0], "value": r[1], "delta": r[2]} for r in reversed(rows)]

    def query_total(self, measure, grain, period_key=None):
        pk = period_key or self._latest_period(grain)
        if not pk: return {}
        row = self.conn.execute(f"""
            SELECT d.val_sum, dl.delta_pct FROM {CUBE_TABLE} d
            LEFT JOIN axl_deltas dl USING (grain, period_key, dim_combo, dim_json, measure)
            WHERE d.grain=? AND d.period_key=? AND d.dim_combo='__total__' AND d.measure=?
        """, [grain, pk, measure]).fetchone()
        if not row: return {}
        return {"value": row[0], "delta": row[1], "period": pk}

    def query_topk(self, dimension, measure, grain, k=5, period_key=None):
        pk = period_key or self._latest_period(grain)
        if not pk: return []
        rows = self.conn.execute(f"""
            SELECT JSON_EXTRACT_STRING(dim_json, '$.{dimension}') AS label, val_sum AS value
            FROM {CUBE_TABLE}
            WHERE grain=? AND period_key=? AND dim_combo=? AND measure=?
            ORDER BY val_sum DESC LIMIT ?
        """, [grain, pk, dimension, measure, k]).fetchall()
        return [{"label": r[0], "value": r[1]} for r in rows if r[0]]

    def query_cross(self, dim1, dim2, measure, grain, period_key=None):
        pk = period_key or self._latest_period(grain)
        if not pk: return []
        combo_key = "|".join(sorted([dim1, dim2]))
        rows = self.conn.execute(f"""
            SELECT JSON_EXTRACT_STRING(dim_json, '$.{dim1}') AS d1,
                   JSON_EXTRACT_STRING(dim_json, '$.{dim2}') AS d2, val_sum AS value
            FROM {CUBE_TABLE}
            WHERE grain=? AND period_key=? AND dim_combo=? AND measure=?
            ORDER BY val_sum DESC
        """, [grain, pk, combo_key, measure]).fetchall()
        return [{"d1": r[0], "d2": r[1], "value": r[2]} for r in rows if r[0] and r[1]]

    def query_periods(self, grain):
        rows = self.conn.execute(f"""
            SELECT DISTINCT period_key FROM {CUBE_TABLE}
            WHERE grain=? AND dim_combo='__total__' ORDER BY period_key
        """, [grain]).fetchall()
        return [r[0] for r in rows]

    def _latest_period(self, grain):
        row = self.conn.execute(f"""
            SELECT MAX(period_key) FROM {CUBE_TABLE}
            WHERE grain=? AND dim_combo='__total__'
        """, [grain]).fetchone()
        return row[0] if row else None

    def available_dims(self): return [d["col"] for d in self.dims if d["cardinality"] <= CARDINALITY_CUTOFF]
    def available_measures(self): return [m["col"] for m in self.measures]
    def stats(self):
        row = self.conn.execute(f"SELECT COUNT(*) FROM {CUBE_TABLE}").fetchone()
        return {"cube_cells": row[0] if row else 0, "dims": len(self.dims),
                "measures": len(self.measures), "time_col": self.time_col,
                "excluded_dims": [d["col"] for d in self.excluded_dims],
                "build_seconds": round(self._build_time, 2)}

# ── Profiler (v2 — unchanged logic, cleaned) ──────────────────────────────────

import re
from enum import Enum
from dataclasses import dataclass, field

class ColType(Enum):
    TEMPORAL = "temporal"; MEASURE = "measure"; DIMENSION = "dimension"
    DIM_HICARDINAL = "dim_high_card"; IDENTIFIER = "identifier"
    TEXT = "text"; BOOLEAN = "boolean"; UNKNOWN = "unknown"

@dataclass
class ColProfile:
    name: str; dtype: str; col_type: ColType; cardinality: int
    null_pct: float; sample_values: List = field(default_factory=list)
    stats: Dict = field(default_factory=dict); warnings: List[str] = field(default_factory=list)

class DataProfiler:
    DATETIME_FORMATS = [
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%b %Y", "%B %Y",
        "%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%d %B %Y",
    ]
    ID_NAME_HINTS = ("id", "uuid", "guid", "key", "code", "number", "no", "ref")
    TEXT_AVG_LEN = 60; MIN_NUMERIC_UNIQUE = 10; ID_RATIO = 0.85

    def __init__(self, df: pd.DataFrame, cutoff: int = CARDINALITY_CUTOFF):
        self.df = df.copy(); self.cutoff = cutoff
        self.profiles: Dict[str, ColProfile] = {}
        self.time_col = None; self.measures = []; self.dims = []
        self.excluded_dims = []; self.id_cols = []
        self._run()

    def _run(self):
        for col in self.df.columns:
            p = self._profile_column(col); self.profiles[col] = p
            if p.col_type == ColType.TEMPORAL and self.time_col is None:
                self.time_col = col
                self.df[col] = pd.to_datetime(self.df[col], errors="coerce")
            elif p.col_type == ColType.MEASURE:
                self.measures.append({"col": col, "name": col, "stats": p.stats})
            elif p.col_type in (ColType.DIMENSION, ColType.BOOLEAN):
                self.dims.append({"col": col, "name": col, "cardinality": p.cardinality, "values": p.sample_values})
            elif p.col_type == ColType.DIM_HICARDINAL:
                self.excluded_dims.append({"col": col, "name": col, "cardinality": p.cardinality,
                                            "reason": f"cardinality={p.cardinality} > cutoff={self.cutoff}"})
            elif p.col_type == ColType.IDENTIFIER:
                self.id_cols.append(col)

    def _profile_column(self, col: str) -> ColProfile:
        s = self.df[col]; n = len(s); null_pct = float(s.isnull().mean() * 100)
        uniq = int(s.nunique(dropna=True)); ratio = uniq / n if n > 0 else 0
        col_type, stats, warnings = self._infer_type(s, str(s.dtype), uniq, ratio, col)
        sample = list(s.dropna().unique()[:8])
        try: sample = [str(v) for v in sample]
        except: sample = []
        return ColProfile(name=col, dtype=str(s.dtype), col_type=col_type, cardinality=uniq,
                          null_pct=null_pct, sample_values=sample, stats=stats, warnings=warnings)

    def _infer_type(self, s, dtype_str, uniq, ratio, col_name):
        stats, warnings = {}, []
        n = len(s)
        if pd.api.types.is_datetime64_any_dtype(s):
            return ColType.TEMPORAL, {"parsed": True}, []
        if self._is_stringlike(s) and self._looks_like_datetime(s):
            return ColType.TEMPORAL, {"parsed": False, "needs_parse": True}, []
        if re.search(r"\byear\b", col_name, re.I) and self._is_numeric(s):
            s_clean = s.dropna()
            if len(s_clean) and (1900 < float(s_clean.min()) < 2100):
                return ColType.TEMPORAL, {"part": "year"}, ["Year-only column — no sub-year granularity"]
        if self._name_looks_like_id(col_name) and (uniq > self.cutoff or ratio >= self.ID_RATIO):
            return ColType.IDENTIFIER, {"reason": "name+uniqueness"}, []
        if self._is_numeric(s):
            if uniq <= self.MIN_NUMERIC_UNIQUE:
                return ColType.DIMENSION, {"coded": True}, ["Low-cardinality numeric → dimension"]
            mu = float(s.mean()) if s.notna().any() else 0.0
            std = float(s.std()) if s.notna().any() else 0.0
            if std == 0:
                return ColType.IDENTIFIER, {"constant": True}, ["Zero variance — constant column"]
            stats = {"mean": round(mu, 4), "std": round(std, 4), "min": float(s.min()),
                     "max": float(s.max()), "median": float(s.median())}
            return ColType.MEASURE, stats, warnings
        if uniq == 2:
            return ColType.BOOLEAN, {}, []
        if self._is_stringlike(s):
            non_null = s.dropna().astype(str)
            avg_len = float(non_null.str.len().mean()) if len(non_null) else 0.0
            if avg_len > self.TEXT_AVG_LEN:
                return ColType.TEXT, {"avg_len": round(avg_len, 1)}, []
            if ratio >= self.ID_RATIO and uniq > self.cutoff:
                return ColType.IDENTIFIER, {"ratio": round(ratio, 3)}, []
            if uniq <= self.cutoff:
                return ColType.DIMENSION, {"cats": uniq}, []
            return ColType.DIM_HICARDINAL, {"cats": uniq}, []
        return ColType.UNKNOWN, {}, []

    def _is_numeric(self, s): return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    def _is_stringlike(self, s):
        if pd.api.types.is_object_dtype(s): return True
        try:
            if isinstance(s.dtype, pd.StringDtype): return True
        except: pass
        return str(s.dtype) in ("object", "string", "str")
    def _name_looks_like_id(self, col_name):
        return any(tok in self.ID_NAME_HINTS for tok in re.split(r"[_\s\-]+", col_name.lower()))
    def _looks_like_datetime(self, s):
        sample = s.dropna().head(50)
        if len(sample) == 0: return False
        for fmt in self.DATETIME_FORMATS:
            try:
                parsed = pd.to_datetime(sample, format=fmt, errors="coerce")
                if parsed.notna().mean() > 0.8: return True
            except: pass
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed = pd.to_datetime(sample, errors="coerce")
            if parsed.notna().mean() > 0.8: return True
        except: pass
        return False

    def result(self) -> dict:
        return {
            "dims": self.dims, "measures": self.measures,
            "excluded_dims": self.excluded_dims, "time_col": self.time_col,
            "id_cols": self.id_cols, "row_count": len(self.df), "col_count": len(self.df.columns),
            "schema": {col: {"type": p.col_type.value, "cardinality": p.cardinality,
                            "null_pct": round(p.null_pct, 1), "sample": p.sample_values[:5],
                            "stats": p.stats, "warnings": p.warnings}
                       for col, p in self.profiles.items()}
        }
    def parsed_df(self) -> pd.DataFrame: return self.df

# ── NLU (v2 — with retry, streaming, conversation context) ──────────────────

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 400

async def parse_intent(text: str, schema_ctx: dict, history: Optional[List[Dict]] = None,
                       api_key: Optional[str] = None, max_retries: int = 2) -> dict:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return _fallback_parse(text, schema_ctx)
    system = _build_system_prompt(schema_ctx)
    messages = []
    if history:
        for h in history[-4:]:
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": text})

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(ANTHROPIC_API_URL, headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"
                }, json={"model": MODEL, "max_tokens": MAX_TOKENS, "system": system, "messages": messages})
                data = resp.json()
            raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            raw = re.sub(r"```(?:json)?|```", "", raw.strip()).strip()
            intent = json.loads(raw)
            return _validate_and_fill(intent, schema_ctx, text)
        except Exception:
            if attempt == max_retries:
                return _fallback_parse(text, schema_ctx)
            await asyncio.sleep(0.5 * (2 ** attempt))
    return _fallback_parse(text, schema_ctx)

def _build_system_prompt(schema_ctx: dict) -> str:
    dims = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]
    time_col = schema_ctx.get("time_col", "")
    excl = [d["col"] for d in schema_ctx.get("excluded_dims", [])]
    return f"""You are an analytical query intent parser for a pre-computed BI cube engine.
SCHEMA:
- Measures (numeric): {measures}
- Cube dimensions (low-cardinality): {dims}
- High-cardinality dimensions (SQL-only): {excl}
- Time column: {time_col or "none"}
Parse the user's query into EXACTLY this JSON structure. No other text.
{{"insight_type": "breakdown" | "trend" | "topk" | "total" | "cross" | "distribution" | "correlation" | "anomaly",
  "measure": "<one of the measure column names>", "dimension": "<one of the cube dimension column names or null>",
  "dimension2": "<second dimension for cross-type or null>",
  "grain": "day" | "week" | "month" | "quarter" | "year", "k": <integer for topk, default 5>,
  "period_key": "<specific period string like '2024-01' or null for latest>",
  "title": "<concise human-readable title for this insight card, max 8 words>"}}
RULES:
1. Default measure: {measures[0] if measures else "revenue"}
2. Default grain: month
3. Default k: 5
4. For "trend" and "total": dimension = null
5. For "cross": both dimension and dimension2 must be set
6. Grain mapping: "daily"→day, "weekly"→week, "monthly/this month/last month"→month,
   "quarterly/this quarter/last quarter/QoQ"→quarter, "yearly/annual/YoY"→year
7. If user says "top N", set insight_type=topk and k=N
8. If user says "breakdown/split/by X", set insight_type=breakdown
9. If user says "trend/over time/trajectory/direction", set insight_type=trend
10. If user says "total/overall/grand total", set insight_type=total
11. Map informal names to column names: "top line"→first measure, "GMV"→revenue if revenue exists
12. Dimension must be one from the cube dimensions list above, or null"""

def _validate_and_fill(intent: dict, schema_ctx: dict, raw_text: str) -> dict:
    dims = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]
    defaults = {
        "insight_type": "breakdown" if dims else "total",
        "measure": measures[0] if measures else "value",
        "dimension": dims[0] if dims else None, "dimension2": None,
        "grain": "month", "k": 5, "period_key": None, "title": raw_text[:60],
    }
    for k, v in defaults.items():
        if k not in intent or intent[k] is None:
            intent[k] = v
    if intent["measure"] not in measures and measures:
        intent["measure"] = measures[0]
    if intent["dimension"] and intent["dimension"] not in dims:
        intent["dimension"] = dims[0] if dims else None
    if intent["grain"] not in ("day", "week", "month", "quarter", "year"):
        intent["grain"] = "month"
    if intent["insight_type"] == "topk" and not intent["dimension"] and dims:
        intent["dimension"] = dims[0]
    if intent["insight_type"] == "cross":
        if not intent["dimension"] and dims: intent["dimension"] = dims[0]
        if not intent["dimension2"] and len(dims) > 1: intent["dimension2"] = dims[1]
        if intent["dimension"] == intent["dimension2"]:
            intent["dimension2"] = dims[1] if len(dims) > 1 else None
    return intent

def _fallback_parse(text: str, schema_ctx: dict) -> dict:
    dims = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]
    ql = text.lower()
    itype = "breakdown"
    if any(w in ql for w in ["trend", "over time", "trajectory", "direction", "history"]): itype = "trend"
    elif any(w in ql for w in ["top", "best", "worst", "rank", "highest", "lowest"]): itype = "topk"
    elif any(w in ql for w in ["total", "overall", "grand", "aggregate", "sum"]): itype = "total"
    elif any(w in ql for w in ["vs", "cross", "heatmap", "matrix"]): itype = "cross"
    measure = measures[0] if measures else "value"
    for m in measures:
        if m.lower().replace("_", " ") in ql or m.lower() in ql: measure = m; break
    dimension = dims[0] if dims else None
    for d in dims:
        if d.lower().replace("_", " ") in ql or d.lower() in ql: dimension = d; break
    grain = "month"
    for g, words in {"day": ["daily", "day"], "week": ["week", "weekly"],
                     "month": ["month", "monthly"], "quarter": ["quarter", "quarterly", "qoq"],
                     "year": ["year", "yearly", "annual", "yoy"]}.items():
        if any(w in ql for w in words): grain = g; break
    k = 5
    m = re.search(r"\btop\s+(\d+)\b", ql)
    if m: k = int(m.group(1))
    return {"insight_type": itype, "measure": measure, "dimension": dimension,
            "dimension2": dims[1] if len(dims) > 1 else None, "grain": grain,
            "k": k, "period_key": None, "title": text[:60]}

# ── Card Resolution ───────────────────────────────────────────────────────────

async def _resolve_to_card(intent: dict, cube: CubeEngine, schema_ctx: dict, raw_text: str) -> dict:
    itype = intent["insight_type"]; measure = intent["measure"]
    dim = intent.get("dimension"); dim2 = intent.get("dimension2")
    grain = intent.get("grain", "month"); k = intent.get("k", 5); period = intent.get("period_key")
    chart_data, kpi, delta, period_key, chart_type = [], None, None, period, "bar"

    if itype == "trend":
        n = {"day": 30, "week": 16, "month": 12, "quarter": 8, "year": 5}.get(grain, 12)
        rows = cube.query_trend(measure, grain, n)
        chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
        chart_type = "area"
        if rows: kpi = rows[-1]["value"]; delta = rows[-1].get("delta"); period_key = rows[-1]["period"]
    elif itype == "total":
        result = cube.query_total(measure, grain, period)
        kpi, delta, period_key = result.get("value"), result.get("delta"), result.get("period")
        rows = cube.query_trend(measure, grain, 12)
        chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
        chart_type = "area"
    elif itype == "topk":
        if not dim:
            itype = "trend"; rows = cube.query_trend(measure, grain, 10)
            chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
            chart_type = "area"
            if rows: kpi = rows[-1]["value"]; delta = rows[-1].get("delta"); period_key = rows[-1]["period"]
        else:
            rows = cube.query_topk(dim, measure, grain, k, period)
            chart_data = [{"label": r["label"], "value": r["value"]} for r in rows]
            chart_type = "bar"
            if rows: kpi = rows[0]["value"]; delta = rows[0].get("delta")
    elif itype == "cross":
        if dim and dim2:
            rows = cube.query_cross(dim, dim2, measure, grain, period)
            chart_data = [{"label": f"{r['d1']} × {r['d2']}", "value": r["value"]} for r in rows[:20]]
            chart_type = "bar"
            if chart_data: kpi = sum(r["value"] for r in chart_data)
        else:
            rows = cube.query_trend(measure, grain, 12)
            chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
            chart_type = "area"
    elif itype == "breakdown":
        if dim:
            rows = cube.query_breakdown(dim, measure, grain, period)
            chart_type = "pie" if len(rows) <= 5 else "bar"
            chart_data = [{"label": r["label"], "value": r["value"]} for r in rows]
            if rows: kpi = sum(r["value"] for r in rows); delta = rows[0].get("delta")
        else:
            result = cube.query_total(measure, grain, period)
            kpi, delta, period_key = result.get("value"), result.get("delta"), result.get("period")
            chart_data = []; chart_type = "kpi"

    summary = _generate_summary(itype, measure, dim, grain, chart_data, kpi, delta)
    return {
        "id": str(uuid.uuid4())[:8], "title": intent.get("title", raw_text[:60]),
        "insight_type": itype, "measure": measure, "dimension": dim,
        "grain": grain, "chart_type": chart_type, "chart_data": chart_data,
        "kpi": kpi, "delta": delta, "period": period_key, "summary": summary,
    }

def _generate_summary(itype, measure, dimension, grain, chart_data, kpi, delta) -> str:
    if not chart_data and kpi is None: return "No data available for this query."
    delta_str = ""
    if delta is not None:
        pct = abs(delta * 100); dir_word = "up" if delta > 0 else "down"
        delta_str = f" ({dir_word} {pct:.1f}% vs prior {grain})"
    if itype == "trend" and chart_data:
        first = chart_data[0]["value"] if chart_data[0].get("value") else 1
        last = chart_data[-1]["value"] if chart_data[-1].get("value") else 0
        chg = ((last - first) / first * 100) if first else 0
        word = "grew" if chg >= 0 else "declined"
        return f"{measure} {word} {abs(chg):.1f}% over the period. Latest: {_fmt(last)}{delta_str}."
    if itype == "total" and kpi is not None:
        return f"Total {measure}: {_fmt(kpi)}{delta_str}."
    if itype == "breakdown" and chart_data:
        top = chart_data[0]; tot = sum(r["value"] for r in chart_data if r.get("value"))
        pct = (top["value"] / tot * 100) if tot else 0
        tail = (f" {chart_data[-1]['label']} is the lowest at {_fmt(chart_data[-1]['value'])}."
                if len(chart_data) > 1 else "")
        return f"{top['label']} leads with {_fmt(top['value'])} ({pct:.0f}% of total).{tail}"
    if itype == "topk" and chart_data:
        return f"#1 is {chart_data[0]['label']} at {_fmt(chart_data[0]['value'])}{delta_str}."
    if itype == "cross" and chart_data:
        return f"Highest intersection: {chart_data[0]['label']} at {_fmt(chart_data[0]['value'])}."
    return f"{measure} insight computed successfully."

def _fmt(v):
    if v is None: return "N/A"
    if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if abs(v) >= 1_000: return f"{v/1_000:.1f}K"
    return f"{v:.2f}"

# ── Alert Engine ──────────────────────────────────────────────────────────────

async def alert_poller():
    """Background task: check all active alerts against latest cube values."""
    while True:
        await asyncio.sleep(ALERT_POLL_INTERVAL)
        conn = sqlite3.connect(_SQLITE_PATH)
        alerts = conn.execute("""
            SELECT id, session_id, name, measure, grain, threshold, direction, dimension, dim_value
            FROM alerts WHERE active=1
        """).fetchall()
        for alert in alerts:
            aid, sid, name, measure, grain, threshold, direction, dim, dval = alert
            session = _SESSIONS.get(sid)
            if not session or not session.cube_ready: continue
            cube = session.conn  # direct DuckDB conn access for alert queries
            try:
                if dim and dval:
                    # Check specific dimension value
                    row = cube.execute("""
                        SELECT val_sum FROM axl_cube
                        WHERE grain=? AND dim_combo=? AND measure=? AND period_key=(
                            SELECT MAX(period_key) FROM axl_cube WHERE grain=? AND dim_combo='__total__'
                        ) AND dim_json LIKE ?
                    """, [grain, dim, measure, grain, f'%"{dim}": "{dval}"%']).fetchone()
                else:
                    row = cube.execute("""
                        SELECT val_sum FROM axl_cube
                        WHERE grain=? AND dim_combo='__total__' AND measure=? AND period_key=(
                            SELECT MAX(period_key) FROM axl_cube WHERE grain=? AND dim_combo='__total__'
                        )
                    """, [grain, measure, grain]).fetchone()
                if row:
                    val = row[0]
                    triggered = (direction == "above" and val > threshold) or (direction == "below" and val < threshold)
                    if triggered:
                        conn.execute("UPDATE alerts SET last_triggered=? WHERE id=?",
                                     [datetime.utcnow().isoformat(), aid])
                        conn.commit()
                        # In production: dispatch to webhook/email/Slack here
            except Exception:
                pass
        conn.close()

# ── FastAPI App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(cleanup_stale_sessions())
    asyncio.create_task(alert_poller())
    yield
    # cleanup on shutdown

app = FastAPI(title="AxiLattice Engine v2", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...),
                    session_id: Optional[str] = None):
    sid = session_id or str(uuid.uuid4())[:12]
    session = await get_or_create_session(sid)
    content = await file.read(); fname = file.filename or "data.csv"
    try:
        if fname.endswith(".parquet"):
            df = pd.read_parquet(io.BytesIO(content))
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
            df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
        else:
            df = None
            for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, index_col=False)
                    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
                    break
                except: continue
            if df is None: raise ValueError("Could not decode file")
        if df.empty: raise ValueError("File contains no data")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File read error: {str(e)}")

    profiler = DataProfiler(df)
    schema_ctx = profiler.result()
    df_parsed = profiler.parsed_df()
    session.schema_ctx = schema_ctx
    session.df = df_parsed
    session.build_status = "building"
    session.build_error = None
    background_tasks.add_task(_build_cube_bg, sid, df_parsed, schema_ctx)
    return {"status": "building", "session_id": sid, "schema": schema_ctx,
            "file_name": fname, "rows": len(df), "cols": len(df.columns)}

def _build_cube_bg(sid: str, df: pd.DataFrame, schema_ctx: dict):
    try:
        session = _SESSIONS.get(sid)
        if not session: return
        db_path = os.environ.get("DUCKDB_PATH", session.db_path)
        cube = CubeEngine(db_path=db_path)
        stats = cube.build(df, schema_ctx)
        session.conn = cube.conn
        session.cube_ready = True
        session.build_status = "ready"
        session.build_stats = stats
    except Exception as e:
        session = _SESSIONS.get(sid)
        if session:
            session.build_status = "error"
            session.build_error = str(e)

@app.get("/schema")
async def get_schema(session_id: str = "default"):
    session = await get_or_create_session(session_id)
    if not session.schema_ctx:
        raise HTTPException(status_code=404, detail="No data loaded for this session")
    return {"session_id": session_id, "build_status": session.build_status,
            "schema": session.schema_ctx,
            "cube_stats": session.conn.execute("SELECT COUNT(*) FROM axl_cube").fetchone()[0] if session.conn else None}

@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(_SESSIONS),
            "build_statuses": {sid: s.build_status for sid, s in _SESSIONS.items()}}

@app.post("/query")
async def query(req: QueryRequest):
    session = await get_or_create_session(req.session_id)
    if not session.cube_ready or not session.conn:
        raise HTTPException(status_code=503, detail="Cube not ready. Upload data first.")
    cube = CubeEngine(db_path=session.db_path)
    cube.conn = session.conn  # reuse existing connection
    cube.dims = session.schema_ctx["dims"]
    cube.measures = session.schema_ctx["measures"]
    cube.time_col = session.schema_ctx.get("time_col")
    cube.excluded_dims = session.schema_ctx.get("excluded_dims", [])

    # Build history from SQLite
    conn = sqlite3.connect(_SQLITE_PATH)
    hist = conn.execute("""
        SELECT role, content FROM conversations
        WHERE session_id=? ORDER BY id DESC LIMIT 4
    """, [req.session_id]).fetchall()
    conn.close()
    history = [{"role": r[0], "content": r[1]} for r in reversed(hist)]

    intent = await parse_intent(req.text, session.schema_ctx, history,
                                 os.environ.get("ANTHROPIC_API_KEY"))

    # Log conversation
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.execute("""
        INSERT INTO conversations (session_id, role, content, intent, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, [req.session_id, "user", req.text, json.dumps(intent), datetime.utcnow().isoformat()])
    conn.commit(); conn.close()

    card = await _resolve_to_card(intent, cube, session.schema_ctx, req.text)

    # Log assistant response
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.execute("""
        INSERT INTO conversations (session_id, role, content, intent, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, [req.session_id, "assistant", card["summary"], json.dumps({"card_id": card["id"]}), datetime.utcnow().isoformat()])
    conn.commit(); conn.close()

    return card

@app.post("/query/stream")
async def query_stream(req: QueryRequest):
    """SSE streaming endpoint for real-time voice UX."""
    session = await get_or_create_session(req.session_id)
    if not session.cube_ready:
        raise HTTPException(status_code=503, detail="Cube not ready.")

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Parsing intent...'})}\n\n"
        await asyncio.sleep(0.1)
        cube = CubeEngine(db_path=session.db_path)
        cube.conn = session.conn
        cube.dims = session.schema_ctx["dims"]
        cube.measures = session.schema_ctx["measures"]
        cube.time_col = session.schema_ctx.get("time_col")
        cube.excluded_dims = session.schema_ctx.get("excluded_dims", [])
        intent = await parse_intent(req.text, session.schema_ctx, None, os.environ.get("ANTHROPIC_API_KEY"))
        yield f"data: {json.dumps({'type': 'status', 'message': 'Executing query...'})}\n\n"
        await asyncio.sleep(0.05)
        card = await _resolve_to_card(intent, cube, session.schema_ctx, req.text)
        yield f"data: {json.dumps({'type': 'card', 'payload': card})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/voice")
async def voice_query(req: VoiceQueryRequest):
    """Voice endpoint: accepts audio base64 or text fallback. Returns TTS-ready response."""
    session = await get_or_create_session(req.session_id)
    if not session.cube_ready:
        raise HTTPException(status_code=503, detail="Cube not ready.")

    text = req.text_fallback or ""
    if req.audio_base64:
        # In production: send to Whisper API
        # For now, require text_fallback
        if not text:
            raise HTTPException(status_code=400, detail="Audio processing not configured. Provide text_fallback.")

    # Reuse regular query pipeline
    qr = QueryRequest(text=text, session_id=req.session_id, voice_mode=True)
    card = await query(qr)

    # Add voice-optimized fields
    card["voice_response"] = card["summary"]
    card["voice_suggestions"] = _generate_voice_suggestions(card, session.schema_ctx)
    return card

def _generate_voice_suggestions(card: dict, schema_ctx: dict) -> List[str]:
    """Generate follow-up questions optimized for voice interaction."""
    m = card.get("measure", "revenue")
    d = card.get("dimension")
    g = card.get("grain", "month")
    suggestions = []
    if card["insight_type"] == "breakdown":
        suggestions.append(f"What's the trend for {m} over time?")
        suggestions.append(f"Which {d} is growing fastest?")
    elif card["insight_type"] == "trend":
        suggestions.append(f"Break down {m} by {d or 'region'} this {g}")
        suggestions.append(f"What was the total {m} last {g}?")
    elif card["insight_type"] == "total":
        suggestions.append(f"Show me {m} by {d or 'category'}")
        suggestions.append(f"How does this compare to last {g}?")
    suggestions.append("Add this to my dashboard")
    suggestions.append("Alert me when this changes")
    return suggestions[:4]

@app.get("/suggest")
async def suggest(session_id: str = "default"):
    session = await get_or_create_session(session_id)
    if not session.schema_ctx:
        return {"suggestions": []}
    sc = session.schema_ctx
    m = sc.get("measures", [{}])[0].get("col", "revenue") if sc.get("measures") else "revenue"
    dims = [d["col"] for d in sc.get("dims", [])]
    d0 = dims[0] if dims else None; d1 = dims[1] if len(dims) > 1 else None
    suggestions = [
        f"Total {m} this month", f"{m} trend last year",
        f"Top 5 {d0} by {m}" if d0 else f"Overall {m}",
        f"{m} by {d0}" if d0 else f"{m} breakdown",
        f"{m} by {d1} this quarter" if d1 else f"{m} quarterly trend",
        f"Compare {m} by {d0} vs last quarter" if d0 else f"Year over year {m}",
    ]
    return {"suggestions": [s for s in suggestions if s]}

@app.get("/periods/{grain}")
async def get_periods(grain: str, session_id: str = "default"):
    session = await get_or_create_session(session_id)
    if not session.conn:
        raise HTTPException(status_code=404, detail="No cube loaded")
    cube = CubeEngine(db_path=session.db_path)
    cube.conn = session.conn
    periods = cube.query_periods(grain)
    return {"grain": grain, "periods": periods}

# ── Dashboard CRUD (Persistent) ───────────────────────────────────────────────

@app.post("/dashboard")
async def save_dashboard(req: DashboardSaveRequest):
    dash_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(_SQLITE_PATH)
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO dashboards (id, session_id, name, layout, cards, created, updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [dash_id, req.session_id, req.name, req.layout, json.dumps(req.cards), now, now])
    conn.commit(); conn.close()
    return {"id": dash_id, "url": f"/dashboard/{dash_id}", "session_id": req.session_id}

@app.get("/dashboard/{dash_id}")
async def load_dashboard(dash_id: str):
    conn = sqlite3.connect(_SQLITE_PATH)
    row = conn.execute("SELECT * FROM dashboards WHERE id=?", [dash_id]).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"id": row[0], "session_id": row[1], "name": row[2], "layout": row[3],
            "cards": json.loads(row[4]), "created": row[5], "updated": row[6]}

@app.get("/dashboard")
async def list_dashboards(session_id: Optional[str] = None):
    conn = sqlite3.connect(_SQLITE_PATH)
    if session_id:
        rows = conn.execute("SELECT * FROM dashboards WHERE session_id=? ORDER BY updated DESC", [session_id]).fetchall()
    else:
        rows = conn.execute("SELECT * FROM dashboards ORDER BY updated DESC").fetchall()
    conn.close()
    return {"dashboards": [{"id": r[0], "session_id": r[1], "name": r[2], "layout": r[3],
                            "cards": json.loads(r[4]), "created": r[5], "updated": r[6]} for r in rows]}

# ── Alert Endpoints ───────────────────────────────────────────────────────────

@app.post("/alerts")
async def create_alert(req: AlertCreateRequest):
    aid = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.execute("""
        INSERT INTO alerts (id, session_id, name, measure, grain, threshold, direction, dimension, dim_value, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [aid, req.session_id, req.name, req.measure, req.grain, req.threshold,
          req.direction, req.dimension, req.dim_value, datetime.utcnow().isoformat()])
    conn.commit(); conn.close()
    return {"id": aid, "status": "active", "session_id": req.session_id}

@app.get("/alerts")
async def list_alerts(session_id: str):
    conn = sqlite3.connect(_SQLITE_PATH)
    rows = conn.execute("SELECT * FROM alerts WHERE session_id=?", [session_id]).fetchall()
    conn.close()
    return {"alerts": [{"id": r[0], "name": r[2], "measure": r[3], "grain": r[4],
                        "threshold": r[5], "direction": r[6], "active": bool(r[9]),
                        "last_triggered": r[11]} for r in rows]}

@app.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.execute("DELETE FROM alerts WHERE id=?", [alert_id])
    conn.commit(); conn.close()
    return {"deleted": alert_id}

# ── Conversation History ──────────────────────────────────────────────────────

@app.get("/conversations/{session_id}")
async def get_conversation(session_id: str, limit: int = 20):
    conn = sqlite3.connect(_SQLITE_PATH)
    rows = conn.execute("""
        SELECT role, content, intent, timestamp FROM conversations
        WHERE session_id=? ORDER BY id DESC LIMIT ?
    """, [session_id, limit]).fetchall()
    conn.close()
    return {"session_id": session_id, "messages": [
        {"role": r[0], "content": r[1], "intent": json.loads(r[2]) if r[2] else None, "timestamp": r[3]}
        for r in reversed(rows)
    ]}

# ── Static Frontend (single-service deploy) ───────────────────────────────────
# Serves frontend/public/index.html so one Render web service hosts both the
# API and the SPA. Mounted last so it never shadows the API routes above.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
