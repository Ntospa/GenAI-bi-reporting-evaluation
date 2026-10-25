"""Data layer: load/clean retail data or generate reproducible synthetic data.

Canonical schema (one row per sales line):
    date (YYYY-MM-DD), region, category, product, units, unit_price, cost, is_return
"""
from __future__ import annotations
import hashlib
import os
import numpy as np
import pandas as pd

CANON = ["date", "order_id", "region", "category", "product", "units", "unit_price",
         "cost", "is_return"]

REGIONS = ["North", "South", "East", "West"]
CATEGORIES = ["Electronics", "Home", "Apparel", "Sports"]
PRODUCTS = {
    "Electronics": ["Headphones", "Smart Speaker", "USB-C Charger", "Webcam"],
    "Home": ["Desk Lamp", "Cushion Set", "Ceramic Mug", "Wall Clock"],
    "Apparel": ["Cotton T-Shirt", "Hoodie", "Running Socks", "Cap"],
    "Sports": ["Yoga Mat", "Water Bottle", "Resistance Band", "Dumbbell 5kg"],
}


def generate_synthetic(periods, seed: int = 42) -> pd.DataFrame:
    """Deterministic synthetic retail transactions across the given YYYY-MM periods."""
    rng = np.random.default_rng(seed)
    rows = []
    for p_idx, period in enumerate(periods):
        year, month = map(int, period.split("-"))
        # mild month-over-month growth trend so KPIs are non-trivial
        base = 1.0 + 0.05 * p_idx
        n = int(rng.integers(900, 1100))
        for i in range(n):
            cat = rng.choice(CATEGORIES, p=[0.35, 0.25, 0.25, 0.15])
            prod = rng.choice(PRODUCTS[cat])
            region = rng.choice(REGIONS)
            day = int(rng.integers(1, 28))
            units = int(rng.integers(1, 6))
            unit_price = round(float(rng.uniform(5, 120)) * base, 2)
            cost = round(unit_price * float(rng.uniform(0.45, 0.75)), 2)
            is_return = bool(rng.random() < 0.04)
            order_id = f"S{year:04d}{month:02d}-{i // 2:05d}"  # ~2 lines per order
            rows.append([f"{year:04d}-{month:02d}-{day:02d}", order_id, region, cat, prod,
                         units, unit_price, cost, is_return])
    df = pd.DataFrame(rows, columns=CANON)
    return df


def load_csv(path: str, colmap: dict) -> pd.DataFrame:
    """Load a real retail CSV and rename columns to the canonical schema."""
    raw = pd.read_csv(path)
    inv = {v: k for k, v in colmap.items()}
    df = raw.rename(columns=inv)
    for c in CANON:
        if c not in df.columns:
            if c == "cost":
                df["cost"] = df.get("unit_price", 0) * 0.6  # fallback assumption
            elif c == "is_return":
                df["is_return"] = df.get("units", 0).astype(float) < 0
            else:
                df[c] = np.nan
    return df[CANON]


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["units", "unit_price", "cost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "units", "unit_price"])
    df = df[df["units"] != 0]
    df["is_return"] = df["is_return"].fillna(False).astype(bool)
    df["period"] = df["date"].dt.strftime("%Y-%m")
    df["revenue"] = df["units"].abs() * df["unit_price"]
    df["margin"] = (df["unit_price"] - df["cost"].fillna(df["unit_price"] * 0.6)) * df["units"].abs()
    return df.reset_index(drop=True)


def snapshot(df: pd.DataFrame, data_dir: str) -> str:
    """Persist a versioned snapshot and return its content hash."""
    os.makedirs(data_dir, exist_ok=True)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    h = hashlib.sha256(csv_bytes).hexdigest()[:12]
    out = os.path.join(data_dir, f"retail_snapshot_{h}.csv")
    with open(out, "wb") as f:
        f.write(csv_bytes)
    return h


def source_hash(cfg) -> str | None:
    """Stable, environment-independent hash of the source csv bytes (the fixed input).
    Preferred reproducibility anchor: unlike the post-clean snapshot hash, it does not vary
    with pandas/numpy float or date formatting across platforms."""
    d = cfg.get("data", {})
    p = d.get("csv_path")
    if d.get("source") == "csv" and p and os.path.exists(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
    return None


def build_dataset(cfg) -> pd.DataFrame:
    d = cfg["data"]
    if d["source"] == "csv" and d.get("csv_path"):
        df = load_csv(d["csv_path"], d["csv_colmap"])
    else:
        df = generate_synthetic(d["periods"], seed=d.get("seed", 42))
    return clean(df)
