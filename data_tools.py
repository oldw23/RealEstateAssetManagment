"""
Data access layer for the real-estate ledger dataset.

The LangGraph agents never touch the raw DataFrame directly — they call
these functions. That keeps LLM behaviour deterministic for anything
that is really just arithmetic, and gives us one place to fix data
quirks (missing property_name on corporate-level rows, string years,
etc).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from rapidfuzz import process, fuzz

_HERE = os.path.dirname(__file__)
_CANDIDATES = [
    os.path.join(_HERE, "data", "ledger.parquet"),
    os.path.join(_HERE, "ledger.parquet"),
]
DATA_PATH = next((p for p in _CANDIDATES if os.path.exists(p)), _CANDIDATES[0])

_df: Optional[pd.DataFrame] = None


def load_data() -> pd.DataFrame:
    global _df
    if _df is None:
        df = pd.read_parquet(DATA_PATH)
        # normalize dtypes we rely on
        df["year"] = df["year"].astype(str)
        _df = df
    return _df


def get_properties() -> list[str]:
    df = load_data()
    return sorted(df["property_name"].dropna().unique().tolist())


def get_tenants() -> list[str]:
    df = load_data()
    return sorted(df["tenant_name"].dropna().unique().tolist())


def get_years() -> list[str]:
    df = load_data()
    return sorted(df["year"].dropna().unique().tolist())


def get_quarters() -> list[str]:
    df = load_data()
    return sorted(df["quarter"].dropna().unique().tolist())


@dataclass
class MatchResult:
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    suggestions: dict[str, str] = field(default_factory=dict)  # unmatched -> best guess


import re

_NUM_RE = re.compile(r"\d+")


def fuzzy_match(values: list[str], choices: list[str], threshold: int = 85) -> MatchResult:
    """Match user-supplied names against the real values in the dataset.

    Plain fuzzy scoring alone treats 'Building 500' and 'Building 120' as
    ~83% similar (same prefix), which would silently match a nonexistent
    property. We require any digits in the query to also appear in the
    matched choice, and use a stricter threshold on top of that.
    """
    result = MatchResult()
    for v in values:
        if v in choices:
            result.matched.append(v)
            continue
        candidates = process.extract(v, choices, scorer=fuzz.WRatio, limit=len(choices))
        query_nums = _NUM_RE.findall(v)

        def _valid(choice: str) -> bool:
            if not query_nums:
                return True
            choice_nums = _NUM_RE.findall(choice)
            return query_nums == choice_nums

        best = next(((c, s) for c, s, _ in candidates if s >= threshold and _valid(c)), None)
        if best:
            result.matched.append(best[0])
        else:
            result.unmatched.append(v)
            if candidates:
                result.suggestions[v] = candidates[0][0]
    return result


def _apply_period_filter(df: pd.DataFrame, period: Optional[dict]) -> pd.DataFrame:
    if not period:
        return df
    if period.get("year"):
        df = df[df["year"] == str(period["year"])]
    if period.get("quarter"):
        df = df[df["quarter"] == period["quarter"]]
    if period.get("month"):
        df = df[df["month"] == period["month"]]
    return df


def query_ledger(
    properties: Optional[list[str]] = None,
    tenants: Optional[list[str]] = None,
    period: Optional[dict] = None,
    ledger_type: Optional[str] = None,
    exclude_corporate: bool = False,
) -> pd.DataFrame:
    df = load_data()
    if properties:
        df = df[df["property_name"].isin(properties)]
    if tenants:
        df = df[df["tenant_name"].isin(tenants)]
    if exclude_corporate:
        df = df[df["property_name"].notna()]
    if ledger_type:
        df = df[df["ledger_type"] == ledger_type]
    df = _apply_period_filter(df, period)
    return df


def pnl_summary(properties: Optional[list[str]] = None, period: Optional[dict] = None) -> dict:
    """Revenue, expenses and net for a scope. properties=None -> all."""
    df = query_ledger(properties=properties, period=period)
    revenue = df.loc[df["ledger_type"] == "revenue", "profit"].sum()
    expenses = df.loc[df["ledger_type"] == "expenses", "profit"].sum()
    return {
        "properties": properties or "all",
        "period": period or "all available (2024-2025)",
        "revenue": round(float(revenue), 2),
        "expenses": round(float(expenses), 2),
        "net": round(float(revenue + expenses), 2),
        "row_count": len(df),
    }


def compare_properties(properties: list[str], period: Optional[dict] = None) -> list[dict]:
    return [pnl_summary(properties=[p], period=period) for p in properties]


def compare_periods(property_: Optional[str], period_a: dict, period_b: dict) -> dict:
    props = [property_] if property_ else None
    a = pnl_summary(properties=props, period=period_a)
    b = pnl_summary(properties=props, period=period_b)
    delta = round(a["net"] - b["net"], 2)
    pct = round((delta / abs(b["net"]) * 100), 1) if b["net"] else None
    return {"period_a": a, "period_b": b, "net_delta": delta, "net_delta_pct": pct}


def top_tenants(n: int = 5, period: Optional[dict] = None) -> list[dict]:
    df = query_ledger(period=period, exclude_corporate=True)
    df = df[df["tenant_name"].notna()]
    grouped = (
        df[df["ledger_type"] == "revenue"]
        .groupby("tenant_name")["profit"]
        .sum()
        .sort_values(ascending=False)
        .head(n)
    )
    return [{"tenant": t, "revenue": round(float(v), 2)} for t, v in grouped.items()]


def detect_anomalies(period: Optional[dict] = None, z_threshold: float = 3.0) -> list[dict]:
    """Flag ledger rows whose profit is a big outlier within its own category."""
    df = query_ledger(period=period)
    anomalies = []
    for category, group in df.groupby("ledger_category"):
        if len(group) < 4:
            continue
        mean, std = group["profit"].mean(), group["profit"].std()
        if not std or pd.isna(std):
            continue
        outliers = group[(group["profit"] - mean).abs() > z_threshold * std]
        for _, row in outliers.iterrows():
            prop = row["property_name"]
            tenant = row["tenant_name"]
            anomalies.append(
                {
                    "property": prop if pd.notna(prop) else "corporate-level",
                    "tenant": tenant if pd.notna(tenant) else None,
                    "category": category,
                    "amount": round(float(row["profit"]), 2),
                    "typical_range": f"{mean - std:.0f} to {mean + std:.0f}",
                    "period": row["month"],
                }
            )
    return anomalies
