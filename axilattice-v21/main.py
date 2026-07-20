"""
AxiLattice FastAPI Backend v2 (Fixed)
───────────────────────────────────────
Fixes applied:
  1. Cross-dimensional filtering via query_filtered_breakdown
  2. Deep insight types: anomaly, comparison, growth, seasonality, ranking
  3. "Why" generation: reads z_score, delta_pct, val_mean, val_stddev
  4. Time hierarchy: deltas at every grain, not just latest
  5. Rich card payloads with anomaly flags, change arrows, suggested followups
"""

import os
import io
import json
import uuid
import time
from typing import Optional, List, Dict, Any
from collections import Counter

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from profiler import DataProfiler
from cube import CubeEngine
from nlu import parse_intent

# ── App init ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AxiLattice Engine v2",
    description="Pre-computed insight engine with deep cube exploitation",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ─────────────────────────────────────────────────────────────
_STATE: Dict[str, Any] = {
    "cube":         None,
    "profiler":     None,
    "schema_ctx":   None,
    "df":           None,
    "build_status": "idle",
    "build_error":  None,
    "dashboards":   {},
    "query_history": [],
}


# ── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    text:       str
    session_id: Optional[str] = "default"

class DashboardSaveRequest(BaseModel):
    name:   str
    layout: str = "grid"
    cards:  List[Dict]  = []


# ── Upload & cube build ───────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    content = await file.read()
    fname   = file.filename or "data.csv"

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
                except (UnicodeDecodeError, Exception):
                    continue
            if df is None:
                raise ValueError("Could not decode file")
        if df.empty:
            raise ValueError("File contains no data")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File read error: {str(e)}")

    profiler    = DataProfiler(df)
    schema_ctx  = profiler.result()
    df_parsed   = profiler.parsed_df()

    _STATE["profiler"]     = profiler
    _STATE["schema_ctx"]   = schema_ctx
    _STATE["df"]           = df_parsed
    _STATE["build_status"] = "building"
    _STATE["build_error"]  = None

    background_tasks.add_task(_build_cube_bg, df_parsed, schema_ctx)

    return {
        "status":    "building",
        "schema":    schema_ctx,
        "file_name": fname,
        "rows":      len(df),
        "cols":      len(df.columns),
    }


def _build_cube_bg(df: pd.DataFrame, schema_ctx: dict):
    try:
        db_path = os.environ.get("DUCKDB_PATH", ":memory:")
        cube    = CubeEngine(db_path=db_path)
        stats   = cube.build(df, schema_ctx)
        _STATE["cube"]         = cube
        _STATE["build_status"] = "ready"
        _STATE["build_stats"]  = stats
    except Exception as e:
        _STATE["build_status"] = "error"
        _STATE["build_error"]  = str(e)


# ── Schema & status ───────────────────────────────────────────────────────────

@app.get("/schema")
def get_schema():
    if not _STATE["schema_ctx"]:
        raise HTTPException(status_code=404, detail="No data loaded")
    return {
        "build_status": _STATE["build_status"],
        "schema":       _STATE["schema_ctx"],
        "cube_stats":   _STATE["cube"].stats() if _STATE["cube"] else None,
    }


@app.get("/health")
def health():
    return {
        "status":       "ok",
        "build_status": _STATE["build_status"],
        "has_data":     _STATE["df"] is not None,
    }


# ── Query ─────────────────────────────────────────────────────────────────────

@app.post("/query")
async def query(req: QueryRequest):
    if not _STATE["cube"] or _STATE["build_status"] != "ready":
        raise HTTPException(status_code=503, detail="Cube not ready. Check /health.")

    cube       = _STATE["cube"]
    schema_ctx = _STATE["schema_ctx"]
    hist = _STATE["query_history"][-4:] if _STATE["query_history"] else []

    intent = await parse_intent(
        text       = req.text,
        schema_ctx = schema_ctx,
        history    = hist,
        api_key    = os.environ.get("ANTHROPIC_API_KEY"),
    )

    _STATE["query_history"].append({"role": "user", "content": req.text})
    _STATE["query_history"] = _STATE["query_history"][-20:]

    card = await _resolve_to_card(intent, cube, schema_ctx)
    return card


async def _resolve_to_card(intent: dict, cube: CubeEngine, schema_ctx: dict) -> dict:
    """Map intent → cube query → rich card payload with deep insights."""
    itype    = intent["insight_type"]
    measure  = intent["measure"]
    dim      = intent.get("dimension")
    dim2     = intent.get("dimension2")
    grain    = intent.get("grain", "month")
    k        = intent.get("k", 5)
    period   = intent.get("period_key")
    filter_dim = intent.get("filter_dimension")
    filter_val = intent.get("filter_value")

    chart_data = []
    kpi        = None
    delta      = None
    period_key = period
    chart_type = "bar"
    insights   = []
    suggested_followups = []

    # ═══════════════════════════════════════════════════════════════════════
    # ROUTE BY INSIGHT TYPE
    # ═══════════════════════════════════════════════════════════════════════

    if itype == "trend":
        n = {"day": 30, "week": 16, "month": 12, "quarter": 8, "year": 5}.get(grain, 12)
        rows = cube.query_trend(measure, grain, n)
        chart_data = [{"period": r["period"], "value": r["value"],
                       "delta": r.get("delta"), "anomaly": r.get("anomaly")} for r in rows]
        chart_type = "area"
        if rows:
            kpi   = rows[-1]["value"]
            delta = rows[-1].get("delta")
            period_key = rows[-1]["period"]
        # Detect growth
        growth = cube.query_growth(measure, grain)
        if growth:
            g = growth[0]
            insights.append(f"CAGR: {g['cagr']:.1f}% over {g['periods']} periods")
        # Detect seasonality
        season = cube.query_seasonality(measure, grain)
        if season:
            s = season[0]
            insights.append(f"Seasonality detected: period={s['period']}, correlation={s['correlation']}")

    elif itype == "total":
        result = cube.query_total(measure, grain, period)
        kpi        = result.get("value")
        delta      = result.get("delta")
        period_key = result.get("period")
        z_score    = result.get("z_score")
        if z_score and abs(z_score) > 2:
            insights.append(f"Anomaly: total is {abs(z_score):.1f}σ {'above' if z_score > 0 else 'below'} mean")
        rows = cube.query_trend(measure, grain, 12)
        chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
        chart_type = "area"

    elif itype == "topk":
        if not dim:
            itype = "trend"
            rows = cube.query_trend(measure, grain, 10)
            chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
            chart_type = "area"
            if rows:
                kpi   = rows[-1]["value"]
                delta = rows[-1].get("delta")
                period_key = rows[-1]["period"]
        else:
            rows = cube.query_topk(dim, measure, grain, k, period)
            chart_data = [{"label": r["label"], "value": r["value"],
                           "delta": r.get("delta"), "rank": r.get("rank"),
                           "z_score": r.get("z_score")} for r in rows]
            chart_type = "bar"
            if rows:
                kpi   = rows[0]["value"]
                delta = rows[0].get("delta")
                # Check for concentration
                total = sum(r["value"] for r in rows)
                if total > 0 and rows[0]["value"] / total > 0.5:
                    ratio = rows[0]["value"] / (rows[1]["value"] if len(rows) > 1 else 1)
                    insights.append(f"{rows[0]['label']} is {ratio:.1f}× the next — significant concentration")
                    suggested_followups.append(f"Why is {rows[0]['label']} so dominant?")

    elif itype == "cross":
        if dim and dim2:
            rows = cube.query_cross(dim, dim2, measure, grain, period)
            chart_data = [{"label": f"{r['d1']} × {r['d2']}", "value": r["value"],
                           "d1": r["d1"], "d2": r["d2"]} for r in rows[:20]]
            chart_type = "bar"
            if chart_data:
                kpi = sum(r["value"] for r in chart_data)
        else:
            rows = cube.query_trend(measure, grain, 12)
            chart_data = [{"period": r["period"], "value": r["value"]} for r in rows]
            chart_type = "area"

    elif itype == "breakdown":
        if filter_dim and filter_val and dim:
            # FIX #1: Cross-dimensional filter — "categories in South Region"
            rows = cube.query_filtered_breakdown(dim, measure, filter_dim, filter_val, grain, period)
            chart_type = "bar"
        elif dim:
            rows = cube.query_breakdown(dim, measure, grain, period)
            n_vals = len(rows)
            chart_type = "pie" if n_vals <= 5 else "bar"
        else:
            result = cube.query_total(measure, grain, period)
            kpi        = result.get("value")
            delta      = result.get("delta")
            period_key = result.get("period")
            chart_data = []
            chart_type = "kpi"
            rows = []

        if dim and rows:
            chart_data = [{"label": r["label"], "value": r["value"],
                           "delta": r.get("delta"), "rank": r.get("rank"),
                           "z_score": r.get("z_score")} for r in rows]
            if rows:
                kpi   = sum(r["value"] for r in rows)
                delta = rows[0].get("delta")
                # Anomaly detection on breakdown
                anomalies = [r for r in rows if r.get("z_score") and abs(r["z_score"]) > 2]
                for a in anomalies[:3]:
                    insights.append(f"Anomaly: {a['label']} is {abs(a['z_score']):.1f}σ {'above' if a['z_score'] > 0 else 'below'} mean")

    elif itype == "comparison":
        # FIX #3: Period comparison with change arrows
        if not dim:
            dim = schema_ctx.get("dims", [{}])[0].get("col") if schema_ctx.get("dims") else None
        if dim:
            rows = cube.query_comparison(dim, measure, grain, period)
            chart_data = [{"label": r["label"], "value": r["current_val"],
                           "prior_val": r["prior_val"], "delta": r["delta"],
                           "rank": r["rank"], "anomaly": r.get("anomaly")} for r in rows]
            chart_type = "bar"
            if rows:
                kpi = sum(r["current_val"] for r in rows)
                # Find identical values
                vals = [r["current_val"] for r in rows]
                dupes = {v: c for v, c in Counter(vals).items() if c > 1}
                for v, c in dupes.items():
                    labels = [r["label"] for r in rows if r["current_val"] == v]
                    insights.append(f"{', '.join(labels)} have identical values ({v:.1f}) — check data quality")
        else:
            result = cube.query_total(measure, grain, period)
            kpi = result.get("value")
            delta = result.get("delta")
            period_key = result.get("period")
            chart_type = "kpi"

    elif itype == "anomaly":
        # FIX #2: Anomaly detection
        if not dim:
            dim = schema_ctx.get("dims", [{}])[0].get("col") if schema_ctx.get("dims") else None
        if dim:
            rows = cube.query_anomaly(dim, measure, grain, period, threshold=2.0)
            chart_data = [{"label": r["label"], "value": r["value"],
                           "z_score": r["z_score"], "severity": r["severity"],
                           "direction": r["direction"], "delta": r["delta"]} for r in rows]
            chart_type = "heatmap"
            if rows:
                kpi = len(rows)
                insights.append(f"{len(rows)} anomalies detected (threshold: 2σ)")
                for r in rows[:3]:
                    why = ""
                    if r["delta"] and abs(r["delta"]) > 0.2:
                        why = f" Driven by {r['delta']*100:.1f}% change from prior."
                    insights.append(f"  {r['label']}: {abs(r['z_score']):.1f}σ {r['direction']} mean.{why}")
                suggested_followups = ["Drill down to sub-categories", "Compare with similar segments"]
        else:
            chart_type = "kpi"
            kpi = 0

    elif itype == "growth":
        # FIX #3: Growth analysis
        growth = cube.query_growth(measure, grain)
        if growth:
            g = growth[0]
            chart_data = [{"metric": "CAGR", "value": g["cagr"]},
                          {"metric": "Total Growth", "value": g["total_growth_pct"]}]
            chart_type = "bar"
            kpi = g["cagr"]
            insights.append(f"CAGR: {g['cagr']:.1f}% from {g['first_period']} to {g['last_period']}")
            if g["cagr"] > 20:
                insights.append("Strong growth trajectory")
            elif g["cagr"] < 0:
                insights.append("Declining trend — investigate root causes")
        else:
            chart_type = "kpi"
            kpi = 0

    elif itype == "seasonality":
        # FIX #4: Seasonality
        season = cube.query_seasonality(measure, grain)
        if season:
            s = season[0]
            chart_data = [{"period": p, "type": "peak"} for p in s["peaks"]]
            chart_data += [{"period": p, "type": "dip"} for p in s["dips"]]
            chart_type = "line"
            kpi = s["correlation"]
            insights.append(f"Seasonal period: {s['period']} {grain}s (correlation: {s['correlation']})")
            if s["dips"]:
                insights.append(f"Recurring dips at: {', '.join(s['dips'][:3])}")
            suggested_followups = ["Forecast next period", "Compare year-over-year"]
        else:
            chart_type = "kpi"
            kpi = 0
            insights.append("No significant seasonality detected")

    elif itype == "ranking":
        if not dim:
            dim = schema_ctx.get("dims", [{}])[0].get("col") if schema_ctx.get("dims") else None
        if dim:
            rows = cube.query_ranking(dim, measure, grain, period)
            chart_data = [{"label": r["label"], "value": r["value"],
                           "rank": r["rank"]} for r in rows]
            chart_type = "bar"
            if rows:
                kpi = rows[0]["value"] if rows[0]["rank"] == 1 else None
        else:
            chart_type = "kpi"

    # Generate natural language summary
    summary = _generate_summary(itype, measure, dim, grain, chart_data, kpi, delta, insights)

    return {
        "id":           str(uuid.uuid4())[:8],
        "title":        intent.get("title", req_text_fallback(intent)),
        "insight_type": itype,
        "measure":      measure,
        "dimension":    dim,
        "grain":        grain,
        "chart_type":   chart_type,
        "chart_data":   chart_data,
        "kpi":          kpi,
        "delta":        delta,
        "period":       period_key,
        "summary":      summary,
        "insights":     insights,
        "suggested_followups": suggested_followups,
    }


def req_text_fallback(intent: dict) -> str:
    m = intent.get("measure", "value")
    d = intent.get("dimension", "")
    g = intent.get("grain", "month")
    t = intent.get("insight_type", "")
    return f"{t.title()} of {m}{' by '+d if d else ''} ({g})"


def _generate_summary(itype, measure, dimension, grain, chart_data, kpi, delta, insights=None) -> str:
    if not chart_data and kpi is None:
        return "No data available for this query."

    delta_str = ""
    if delta is not None:
        pct = abs(delta * 100)
        dir_word = "up" if delta > 0 else "down"
        delta_str = f" ({dir_word} {pct:.1f}% vs prior {grain})"

    if itype == "trend" and chart_data:
        first = chart_data[0]["value"] if chart_data[0].get("value") else 1
        last  = chart_data[-1]["value"] if chart_data[-1].get("value") else 0
        chg   = ((last - first) / first * 100) if first else 0
        word  = "grew" if chg >= 0 else "declined"
        anomaly_count = sum(1 for r in chart_data if r.get("anomaly"))
        anomaly_str = f" {anomaly_count} anomaly(s) detected." if anomaly_count else ""
        return f"{measure} {word} {abs(chg):.1f}% over the period. Latest: {_fmt(last)}{delta_str}.{anomaly_str}"

    if itype == "total" and kpi is not None:
        return f"Total {measure}: {_fmt(kpi)}{delta_str}."

    if itype == "breakdown" and chart_data:
        top = chart_data[0]
        tot = sum(r["value"] for r in chart_data if r.get("value"))
        pct = (top["value"] / tot * 100) if tot else 0
        tail = (f" {chart_data[-1]['label']} is the lowest at {_fmt(chart_data[-1]['value'])}."
                if len(chart_data) > 1 else "")
        return f"{top['label']} leads with {_fmt(top['value'])} ({pct:.0f}% of total).{tail}"

    if itype == "topk" and chart_data:
        top = chart_data[0]
        return f"#1 is {top['label']} at {_fmt(top['value'])}{delta_str}."

    if itype == "cross" and chart_data:
        top = chart_data[0]
        return f"Highest intersection: {top['label']} at {_fmt(top['value'])}."

    if itype == "comparison" and chart_data:
        top = chart_data[0]
        return f"{top['label']}: {top['value']:.1f} vs {top.get('prior_val', 0):.1f} prior."

    if itype == "anomaly":
        return f"Anomaly scan: {'; '.join(insights[:2])}" if insights else "No anomalies detected."

    if itype == "growth":
        return f"Growth: {'; '.join(insights[:2])}" if insights else "Insufficient data for growth analysis."

    if itype == "seasonality":
        return f"Seasonality: {'; '.join(insights[:2])}" if insights else "No seasonality detected."

    return f"{measure} insight computed successfully."


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:.2f}"


# ── Suggestions ───────────────────────────────────────────────────────────────

@app.get("/suggest")
def suggest():
    if not _STATE["schema_ctx"]:
        return {"suggestions": []}

    sc  = _STATE["schema_ctx"]
    m   = sc.get("measures", [{}])[0].get("col", "revenue") if sc.get("measures") else "revenue"
    dims = [d["col"] for d in sc.get("dims", [])]
    d0  = dims[0] if dims else None
    d1  = dims[1] if len(dims) > 1 else None

    suggestions = [
        f"Total {m} this month",
        f"{m} trend last year",
        f"Top 5 {d0} by {m}" if d0 else f"Overall {m}",
        f"{m} by {d0}" if d0 else f"{m} breakdown",
        f"{m} by {d1} this quarter" if d1 else f"{m} quarterly trend",
        f"Compare {m} by {d0} vs last quarter" if d0 else f"Year over year {m}",
        f"Anomalies in {m}" if d0 else f"Anomaly detection",
        f"Growth of {m} over time",
        f"Seasonality in {m}",
        f"Ranking of {d0} by {m}" if d0 else f"Rankings",
    ]
    return {"suggestions": [s for s in suggestions if s]}


# ── Periods ───────────────────────────────────────────────────────────────────

@app.get("/periods/{grain}")
def get_periods(grain: str):
    if not _STATE["cube"]:
        raise HTTPException(status_code=404, detail="No cube loaded")
    periods = _STATE["cube"].query_periods(grain)
    return {"grain": grain, "periods": periods}


# ── Dashboard CRUD ────────────────────────────────────────────────────────────

@app.post("/dashboard")
def save_dashboard(req: DashboardSaveRequest):
    dash_id = str(uuid.uuid4())[:8]
    _STATE["dashboards"][dash_id] = {
        "id":      dash_id,
        "name":    req.name,
        "layout":  req.layout,
        "cards":   req.cards,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return {"id": dash_id, "url": f"/dashboard/{dash_id}"}


@app.get("/dashboard/{dash_id}")
def load_dashboard(dash_id: str):
    d = _STATE["dashboards"].get(dash_id)
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return d


@app.get("/dashboard")
def list_dashboards():
    return {"dashboards": list(_STATE["dashboards"].values())}
