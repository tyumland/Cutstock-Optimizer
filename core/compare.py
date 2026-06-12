"""
Snapshot storage and month-over-month comparison.

Every dashboard run saves a snapshot of item state + recommendations to
storage/snapshots/ as JSON. On the next monthly upload the tool:

  1. Diffs recommendations against the previous snapshot - which items
     changed recommendation, which are newly flagged, which were resolved,
     and which items appeared/disappeared.

  2. Upgrades idle detection from the first-upload approximation
     (yearly_usage == 0) to the snapshot-based rule (Option A):
     an item is idle when its on-hand quantity has stayed at or above its
     safety stock level across N consecutive monthly snapshots including
     the current one (default N=2, i.e. roughly 60 days). An unchanged
     on-hand quantity between snapshots is noted as supporting evidence
     of zero movement.

Snapshots are keyed by a label (default: upload year-month) so re-running
the same month overwrites rather than double-counting.
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "storage" / "snapshots"

ITEM_FIELDS = ["item_number", "description", "tier", "yearly_usage",
               "safety_stock", "on_hand", "dollar_value"]
REC_FIELDS = ["item_number", "current_cell", "recommendation", "target_cell"]


def snapshot_label(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def save_snapshot(classified: pd.DataFrame, recs: pd.DataFrame,
                  label: str | None = None) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    label = label or snapshot_label()
    payload = {
        "label": label,
        "items": classified[[c for c in ITEM_FIELDS if c in classified.columns]]
                 .to_dict(orient="records"),
        "recommendations": recs[[c for c in REC_FIELDS if c in recs.columns]]
                           .to_dict(orient="records"),
    }
    path = SNAPSHOT_DIR / f"{label}.json"
    path.write_text(json.dumps(payload))
    return path


def list_snapshots() -> list[str]:
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(p.stem for p in SNAPSHOT_DIR.glob("*.json"))


def load_snapshot(label: str) -> dict:
    return json.loads((SNAPSHOT_DIR / f"{label}.json").read_text())


def previous_snapshots(current_label: str, n: int | None = None) -> list[dict]:
    """All snapshots strictly before current_label, most recent first."""
    labels = [l for l in list_snapshots() if l < current_label]
    labels = labels[::-1] if n is None else labels[::-1][:n]
    return [load_snapshot(l) for l in labels]


# ---------------------------------------------------------------- idle (Option A)

def snapshot_idle_flags(classified: pd.DataFrame, current_label: str,
                        min_snapshots: int = 2) -> pd.DataFrame:
    """Return classified with idle_flag upgraded to the snapshot-based rule.

    History rule: an item is idle when, across the current data and the
    previous (min_snapshots - 1) consecutive snapshots:
      - on_hand > 0 and on_hand >= safety_stock (sitting fully stocked), AND
      - on_hand is IDENTICAL in every snapshot (zero movement - active
        items cycle as they're picked and replenished).
    Items with zero yearly usage and stock on hand stay flagged regardless.

    Falls back to the first-upload approximation when not enough history
    exists. Adds idle_basis ('history' or 'first-upload') and qty_unchanged.
    """
    df = classified.copy()
    history = previous_snapshots(current_label, n=min_snapshots - 1)

    if len(history) < min_snapshots - 1:
        df["idle_basis"] = "first-upload"
        df["qty_unchanged"] = pd.NA
        return df  # keep the yearly_usage==0 flag from classify_abc

    hist_frames = [pd.DataFrame(s["items"]).set_index("item_number") for s in history]

    def idle_now(r):
        # Independent clear-idle path: never picked all year, stock on hand
        if r["yearly_usage"] == 0 and r["on_hand"] > 0:
            return True
        if not (r["on_hand"] > 0 and r["on_hand"] >= r["safety_stock"]):
            return False
        for h in hist_frames:
            if r["item_number"] not in h.index:
                return False
            prev = h.loc[r["item_number"]]
            if prev["on_hand"] != r["on_hand"]:       # quantity moved -> not idle
                return False
            if not (prev["on_hand"] > 0 and prev["on_hand"] >= prev["safety_stock"]):
                return False
        return True

    last = hist_frames[0]
    df["idle_flag"] = df.apply(idle_now, axis=1)
    df["idle_basis"] = "history"
    df["qty_unchanged"] = df.apply(
        lambda r: bool(r["item_number"] in last.index and
                       last.loc[r["item_number"], "on_hand"] == r["on_hand"]),
        axis=1)
    return df


# ---------------------------------------------------------------- diff

def diff_against_previous(recs: pd.DataFrame, current_label: str) -> pd.DataFrame | None:
    """Per-item comparison with the most recent prior snapshot.

    change values:
      same        - same recommendation as last month
      changed     - recommendation differs (old -> new shown)
      new_flag    - newly flagged idle this month
      resolved    - had Move/Flag last month, Stay now
      new_item    - not present last month
      gone        - present last month, absent now (returned as extra rows)
    """
    prev_list = previous_snapshots(current_label, n=1)
    if not prev_list:
        return None
    prev = pd.DataFrame(prev_list[0]["recommendations"])
    prev_label = prev_list[0]["label"]

    cur = recs[REC_FIELDS].copy()
    key = ["item_number", "current_cell"]
    m = cur.merge(prev, on=key, how="outer", suffixes=("", "_prev"), indicator=True)

    def change(r):
        if r["_merge"] == "left_only":
            return "new_item"
        if r["_merge"] == "right_only":
            return "gone"
        if r["recommendation"] == r["recommendation_prev"]:
            return "same"
        if r["recommendation"] == "Flag":
            return "new_flag"
        if r["recommendation"] == "Stay" and r["recommendation_prev"] in ("Move", "Flag"):
            return "resolved"
        return "changed"

    m["change"] = m.apply(change, axis=1)
    m["vs_snapshot"] = prev_label
    return m.drop(columns="_merge")


# ---------------------------------------------------------------- demo helper

def make_demo_previous(classified: pd.DataFrame, recs: pd.DataFrame,
                       current_label: str) -> str:
    """Create a synthetic PRIOR month snapshot so the Changes tab has
    content for demos/presentations before real history exists.

    The synthetic month differs plausibly from the current data:
      - ~10 current Moves were 'Stay' last month (shows as changed)
      - ~5 current Stays were 'Move' last month (shows as resolved)
      - ~6 current Flags didn't exist last month (shows as new_flag)
    """
    y, m = map(int, current_label.split("-"))
    prev_label = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"

    prev_recs = recs.copy()
    moves = prev_recs[prev_recs.recommendation == "Move"].index
    stays = prev_recs[prev_recs.recommendation == "Stay"].index
    flags = prev_recs[prev_recs.recommendation == "Flag"].index

    prev_recs.loc[moves[:10], "recommendation"] = "Stay"
    prev_recs.loc[stays[:5], "recommendation"] = "Move"
    prev_recs.loc[flags[:6], "recommendation"] = "Stay"

    save_snapshot(classified, prev_recs, prev_label)
    return prev_label
