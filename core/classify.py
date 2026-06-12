"""
ABC classification for the Rack Slotting Optimizer.

Tiers are assigned by YEARLY USAGE UNITS (how often it's picked), per the
project spec. Dollar volume (on_hand x standard_cost) is computed and
carried alongside every item so the dashboard can show how much money is
sitting in each cell, but dollars never affect the tier.

Idle logic (first upload / Option B):
  An item is idle-flagged when yearly_usage == 0 and on_hand > 0 -
  it's occupying a cell but was never picked all year.

Once a previous monthly snapshot exists (Option A), idle flagging
switches to: on_hand stayed above safety_stock across N consecutive
snapshots (configurable, default 2 ~= 60 days). That logic lives in
core/compare.py.
"""

import pandas as pd


def classify_abc(usage: pd.DataFrame, a_pct: float = 0.80, b_pct: float = 0.95) -> pd.DataFrame:
    """Assign A/B/C tiers by cumulative share of yearly usage units.

    Items are sorted by yearly usage descending. Items covering the first
    `a_pct` of total units are A, up to `b_pct` are B, the rest are C.
    Items with zero yearly usage are always C regardless of boundaries.
    """
    df = usage.copy().sort_values("yearly_usage", ascending=False).reset_index(drop=True)

    total = df["yearly_usage"].sum()
    if total > 0:
        df["cum_share"] = df["yearly_usage"].cumsum() / total
    else:
        df["cum_share"] = 1.0

    def tier(row):
        if row["yearly_usage"] <= 0:
            return "C"
        if row["cum_share"] <= a_pct:
            return "A"
        if row["cum_share"] <= b_pct:
            return "B"
        return "C"

    df["tier"] = df.apply(tier, axis=1)

    # First-upload idle approximation: on the shelf, never picked all year
    df["idle_flag"] = (df["yearly_usage"] == 0) & (df["on_hand"] > 0)

    # Excess inventory value: dollars tied up above safety stock
    df["excess_units"] = (df["on_hand"] - df["safety_stock"]).clip(lower=0)
    df["excess_value"] = (df["excess_units"] * df["standard_cost"]).round(2)

    return df.drop(columns="cum_share")


def tier_summary(classified: pd.DataFrame) -> pd.DataFrame:
    """Per-tier rollup for the dashboard summary cards."""
    return (
        classified.groupby("tier")
        .agg(
            items=("item_number", "count"),
            yearly_units=("yearly_usage", "sum"),
            on_hand_value=("dollar_value", "sum"),
            idle_items=("idle_flag", "sum"),
            idle_value=("dollar_value", lambda s: s[classified.loc[s.index, "idle_flag"]].sum()),
        )
        .round(2)
        .reset_index()
    )
