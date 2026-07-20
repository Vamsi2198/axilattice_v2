"""
AxiLattice NLU — Claude-powered intent parser
──────────────────────────────────────────────
Converts free-form natural language analytical queries into
structured intent dicts that CubeEngine can execute directly.

Key improvement over regex classifiers:
  - Handles paraphrases: "how's revenue doing" → trend
  - Handles column name variance: "top line" → revenue (with schema context)
  - Handles compound queries: "revenue by region vs last quarter"
  - Maintains 5-message conversation context for follow-ups
"""

import os
import json
import re
import httpx
from typing import Optional, List, Dict

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-20250514"
MAX_TOKENS        = 400


def _build_system_prompt(schema_ctx: dict) -> str:
    dims     = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]
    time_col = schema_ctx.get("time_col", "")
    excl     = [d["col"] for d in schema_ctx.get("excluded_dims", [])]

    return f"""You are an analytical query intent parser for a pre-computed BI cube engine.

SCHEMA:
- Measures (numeric): {measures}
- Cube dimensions (low-cardinality): {dims}
- High-cardinality dimensions (SQL-only): {excl}
- Time column: {time_col or "none"}

Parse the user's query into EXACTLY this JSON structure. No other text.

{{
  "insight_type": "breakdown" | "trend" | "topk" | "total" | "cross" | "comparison" | "anomaly" | "growth" | "seasonality" | "ranking" | "distribution" | "correlation",
  "measure": "<one of the measure column names>",
  "dimension": "<one of the cube dimension column names or null>",
  "dimension2": "<second dimension for cross-type or null>",
  "filter_dimension": "<dimension to filter by, e.g. 'region' or null>",
  "filter_value": "<value to filter by, e.g. 'South' or null>",
  "grain": "day" | "week" | "month" | "quarter" | "year",
  "k": <integer for topk, default 5>,
  "period_key": "<specific period string like '2024-01' or null for latest>",
  "title": "<concise human-readable title for this insight card, max 8 words>"
}}

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
12. If user says "compare/vs/versus/last quarter/prior", set insight_type=comparison
13. If user says "anomaly/outlier/why did this drop/why did this spike", set insight_type=anomaly
14. If user says "growth/CAGR/trajectory", set insight_type=growth
15. If user says "seasonal/seasonality/recurring pattern", set insight_type=seasonality
16. If user says "rank/ranking/position", set insight_type=ranking
17. Cross-filter: "categories in South Region" → dimension="category", filter_dimension="region", filter_value="South"
18. "Why" questions → anomaly type: "why did revenue drop", "why is X so low"
19. Dimension must be one from the cube dimensions list above, or null
"""


async def parse_intent(
    text: str,
    schema_ctx: dict,
    history: Optional[List[Dict]] = None,
    api_key: Optional[str] = None,
) -> dict:
    """
    Parse a natural language query into a structured intent dict.
    
    Args:
        text:       Raw user query
        schema_ctx: Profiler result dict (has dims, measures, time_col)
        history:    Last N message pairs for context (list of {role, content})
        api_key:    Anthropic API key (falls back to env var)
    
    Returns:
        Intent dict with insight_type, measure, dimension, grain, etc.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return _fallback_parse(text, schema_ctx)

    system = _build_system_prompt(schema_ctx)
    messages = []

    # Include up to 4 prior turns for context
    if history:
        for h in history[-4:]:
            messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": text})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key":         key,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      MODEL,
                    "max_tokens": MAX_TOKENS,
                    "system":     system,
                    "messages":   messages,
                },
            )
            data = resp.json()
    except Exception as e:
        return _fallback_parse(text, schema_ctx)

    raw = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            raw += block["text"]

    raw = raw.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                intent = json.loads(m.group())
            except Exception:
                intent = {}
        else:
            intent = {}

    return _validate_and_fill(intent, schema_ctx, text)


def _validate_and_fill(intent: dict, schema_ctx: dict, raw_text: str) -> dict:
    """Ensure all required fields are present and valid."""
    dims     = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]

    # Defaults
    defaults = {
        "insight_type": "breakdown" if dims else "total",
        "measure":      measures[0] if measures else "value",
        "dimension":    dims[0] if dims else None,
        "dimension2":   None,
        "grain":        "month",
        "k":            5,
        "period_key":   None,
        "title":        raw_text[:60],
    }

    for k, v in defaults.items():
        if k not in intent or intent[k] is None:
            intent[k] = v

    # Validate measure exists
    if intent["measure"] not in measures and measures:
        intent["measure"] = measures[0]

    # Validate dimension exists in cube dims
    if intent["dimension"] and intent["dimension"] not in dims:
        intent["dimension"] = dims[0] if dims else None

    # Validate grain
    if intent["grain"] not in ("day", "week", "month", "quarter", "year"):
        intent["grain"] = "month"

    # Ensure topk has dimension
    if intent["insight_type"] == "topk" and not intent["dimension"] and dims:
        intent["dimension"] = dims[0]

    # Ensure cross has two dimensions
    if intent["insight_type"] == "cross":
        if not intent["dimension"] and dims:
            intent["dimension"] = dims[0]
        if not intent["dimension2"] and len(dims) > 1:
            intent["dimension2"] = dims[1]
        if intent["dimension"] == intent["dimension2"]:
            intent["dimension2"] = dims[1] if len(dims) > 1 else None

    # "Why" questions default to anomaly
    why_words = ["why", "what happened", "what caused", "reason", "explain"]
    if any(w in raw_text.lower() for w in why_words) and intent["insight_type"] == "breakdown":
        intent["insight_type"] = "anomaly"

    return intent


def _fallback_parse(text: str, schema_ctx: dict) -> dict:
    """
    Regex-based fallback when Claude API is unavailable.
    Better than nothing, worse than Claude.
    """
    dims     = [d["col"] for d in schema_ctx.get("dims", [])]
    measures = [m["col"] for m in schema_ctx.get("measures", [])]
    ql       = text.lower()

    # Insight type
    if any(w in ql for w in ["anomaly", "outlier", "why did", "what happened", "what caused"]):
        itype = "anomaly"
    elif any(w in ql for w in ["compare", "vs", "versus", "prior", "last quarter", "qoq", "yoy"]):
        itype = "comparison"
    elif any(w in ql for w in ["growth", "cagr", "grew", "growing"]):
        itype = "growth"
    elif any(w in ql for w in ["seasonal", "seasonality", "recurring", "pattern", "cycle"]):
        itype = "seasonality"
    elif any(w in ql for w in ["rank", "ranking", "position", "standing"]):
        itype = "ranking"
    elif any(w in ql for w in ["trend", "over time", "trajectory", "direction", "history"]):
        itype = "trend"
    elif any(w in ql for w in ["top", "best", "worst", "highest", "lowest"]):
        itype = "topk"
    elif any(w in ql for w in ["total", "overall", "grand", "aggregate", "sum"]):
        itype = "total"
    elif any(w in ql for w in ["cross", "heatmap", "matrix"]):
        itype = "cross"
    else:
        itype = "breakdown"

    # Measure
    measure = measures[0] if measures else "value"
    for m in measures:
        if m.lower().replace("_", " ") in ql or m.lower() in ql:
            measure = m
            break

    # Dimension
    dimension = dims[0] if dims else None
    for d in dims:
        if d.lower().replace("_", " ") in ql or d.lower() in ql:
            dimension = d
            break

    # Grain
    grain = "month"
    grain_map = {
        "day": ["daily", "day", "yesterday", "today"],
        "week": ["week", "weekly"],
        "month": ["month", "monthly"],
        "quarter": ["quarter", "quarterly", "qoq"],
        "year": ["year", "yearly", "annual", "yoy"],
    }
    for g, words in grain_map.items():
        if any(w in ql for w in words):
            grain = g
            break

    # k for topk
    k = 5
    m = re.search(r"\btop\s+(\d+)\b", ql)
    if m:
        k = int(m.group(1))

    # Cross-filter detection
    filter_dim = None
    filter_val = None
    for d in dims:
        pattern = rf"(?:in|within|for|by)\s+({d})\s*[=:]?\s*(\w+)"
        m = re.search(pattern, ql)
        if m:
            filter_dim = d
            filter_val = m.group(2)
            break

    return {
        "insight_type": itype,
        "measure":      measure,
        "dimension":    dimension,
        "dimension2":   dims[1] if len(dims) > 1 else None,
        "filter_dimension": filter_dim,
        "filter_value": filter_val,
        "grain":        grain,
        "k":            k,
        "period_key":   None,
        "title":        text[:60],
    }
