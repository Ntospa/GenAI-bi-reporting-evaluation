"""KPI engine: deterministic computation of KPIs -> the canonical source of truth.

Every number that ever reaches the LLM or the validator originates here.
"""
from __future__ import annotations
import json
import os
import pandas as pd


def _prev_period(period: str) -> str:
    y, m = map(int, period.split("-"))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


def _apply_injection(df: pd.DataFrame, period: str, inject) -> pd.DataFrame:
    """Apply a controlled, known change (for the S4 ground-truth scenario).

    Scales revenue/units/margin of the target category+period directly by `factor` (a known
    drop, e.g. 0.6 = -40%). NOTE: an earlier version scaled units and then .round()ed, which was
    a no-op on this dataset (line items have units=1 -> round(0.6)=1). Scaling the aggregates
    directly makes the injection effective and internally consistent (as if `factor` of the
    category's volume were present)."""
    if not inject:
        return df
    df = df.copy()
    f = float(inject["factor"])
    mask = (df["period"] == period) & (df["category"] == inject["category"])
    for col in ("units", "revenue", "margin"):
        if col in df.columns:
            df[col] = df[col].astype(float)          # avoid int64 dtype-cast warning
            df.loc[mask, col] = df.loc[mask, col] * f
    return df


def compute_kpis(df: pd.DataFrame, period: str, inject=None) -> dict:
    """Return a dict of KPI entries: {key: {value, unit, period, segment}}."""
    df = _apply_injection(df, period, inject)
    cur = df[(df["period"] == period) & (~df["is_return"])]
    prev_p = _prev_period(period)
    prev = df[(df["period"] == prev_p) & (~df["is_return"])]
    returns = df[(df["period"] == period) & (df["is_return"])]

    def kpi(value, unit, segment=None):
        return {"value": round(float(value), 2), "unit": unit, "period": period,
                "segment": segment}

    total_rev = cur["revenue"].sum()
    prev_rev = prev["revenue"].sum()
    total_units = int(cur["units"].abs().sum())
    # AOV = total revenue / number of distinct orders (order-level, not line-level).
    # Uses order_id (SalesOrderNumber) when available; otherwise falls back to line count.
    if "order_id" in cur.columns and cur["order_id"].notna().any():
        n_orders = int(cur["order_id"].nunique())
    else:
        n_orders = len(cur)
    aov = total_rev / n_orders if n_orders else 0.0
    gross_margin = (cur["margin"].sum() / total_rev * 100) if total_rev else 0.0
    growth = ((total_rev - prev_rev) / prev_rev * 100) if prev_rev else 0.0
    ret_rate = (len(returns) / (n_orders + len(returns)) * 100) if (n_orders + len(returns)) else 0.0

    kpis = {
        "total_revenue": kpi(total_rev, "EUR"),
        "revenue_growth_mom": kpi(growth, "%"),
        "total_units": kpi(total_units, "units"),
        "aov": kpi(aov, "EUR"),
        "gross_margin": kpi(gross_margin, "%"),
        "returns_rate": kpi(ret_rate, "%"),
    }

    for region, v in cur.groupby("region")["revenue"].sum().items():
        kpis[f"revenue_by_region::{region}"] = kpi(v, "EUR", segment=region)
    for cat, v in cur.groupby("category")["revenue"].sum().items():
        kpis[f"revenue_by_category::{cat}"] = kpi(v, "EUR", segment=cat)
    top = cur.groupby("product")["revenue"].sum().sort_values(ascending=False).head(3)
    for i, (prod, v) in enumerate(top.items(), 1):
        kpis[f"top_products::{i}_{prod}"] = kpi(v, "EUR", segment=prod)

    # Previous-period values (same data the human analyst receives). Labelled with the prev
    # period so they are unambiguous. Included in a scenario only if its `kpis` list requests
    # the families "revenue_previous_period (<prev>)" / "revenue_by_category (<prev>)".
    kpis[f"revenue_previous_period ({prev_p})"] = {"value": round(float(prev_rev), 2),
                                                   "unit": "EUR", "period": prev_p, "segment": None}
    for cat, v in prev.groupby("category")["revenue"].sum().items():
        kpis[f"revenue_by_category ({prev_p})::{cat}"] = {"value": round(float(v), 2),
                                                          "unit": "EUR", "period": prev_p, "segment": cat}
    return kpis


def select_kpis(all_kpis: dict, wanted: list) -> dict:
    """Filter to the KPI families requested by a scenario."""
    out = {}
    for k, v in all_kpis.items():
        fam = k.split("::")[0]
        if fam in wanted:
            out[k] = v
    return out


def save_kpis(kpis: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kpis, f, ensure_ascii=False, indent=2)


def kpi_block_text(kpis: dict) -> str:
    """Human-readable KPI block injected into the prompt (structured feed)."""
    lines = []
    for k, v in kpis.items():
        name = k.replace("::", " - ")
        val = v["value"]
        unit = v["unit"]
        if unit == "EUR":
            s = f"{val:,.2f} EUR"
        elif unit == "%":
            s = f"{val:.2f}%"
        else:
            s = f"{val:g} {unit}"
        lines.append(f"- {name}: {s}")
    return "\n".join(lines)
