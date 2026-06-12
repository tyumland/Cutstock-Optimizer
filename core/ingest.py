"""
Data ingestion for the Rack Slotting Optimizer.

Handles two inputs:
  1. Usage report  (.xls or .xlsx) - SKU master with yearly usage, safety stock,
     on-hand quantity, and standard cost.
  2. Physical audit (.xlsx) - one row per item-per-cell with fractional occupancy.

Cleaning performed:
  - Auto-detects header row in the usage report
  - Splits stacked multi-item audit entries ("123 & 456 & 789") into one record each
  - Normalizes location labels (whitespace, case)
  - Tags unknown/unreadable items ("?", "UNKNOWN", "BUNCH OF RANDOM STOCK")
    with match_status='unknown' and preserves their location for later linking
  - Ignores the summary matrix embedded in unnamed side columns of the audit
"""

import re
import pandas as pd

# Tokens that mean "we don't know what this is" rather than an item number
UNKNOWN_TOKENS = {"UNKNOWN", "?", "??", "???", "BUNCH OF RANDOM STOCK", "RANDOM", "0", ""}

USAGE_COLUMNS = {
    "Item": "category",
    "Planner": "planner",
    "Item number": "item_number",
    "Item description": "description",
    "Yearly": "yearly_usage",
    "Safety": "safety_stock",
    "Quantity": "on_hand",
    "Standard cost": "standard_cost",
}

AUDIT_COLUMNS = {
    "Rack / Area": "rack",
    "Section": "section",
    "Row / Level": "row",
    "Cell / Position": "cell_id",
    "Location Label": "location_label",
    "Item Number": "item_number_raw",
    "Estimated Cell Usage": "occupancy",
    "Total Cell Usage": "total_cell_usage",
    "Rack Cell Width (ft)": "cell_width_ft",
    "Notes": "notes",
}


def _detect_engine(path: str) -> str:
    """Pick the right pandas engine based on actual file format, not just extension."""
    with open(path, "rb") as f:
        magic = f.read(8)
    if magic[:4] == b"PK\x03\x04":          # zip container -> xlsx
        return "openpyxl"
    if magic[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE2 -> legacy xls
        return "xlrd"
    raise ValueError(f"Unrecognized Excel format for {path}")


def load_usage_report(path: str) -> pd.DataFrame:
    """Load the usage report, auto-detecting the header row.

    Returns the SKU master table with normalized column names plus a
    computed dollar_value column (on_hand * standard_cost) representing
    money currently sitting in storage per item.
    """
    engine = _detect_engine(path)
    raw = pd.read_excel(path, engine=engine, header=None)

    # Find the header row: the row containing "Item number"
    header_row = None
    for i in range(min(10, len(raw))):
        if raw.iloc[i].astype(str).str.strip().str.lower().eq("item number").any():
            header_row = i
            break
    if header_row is None:
        raise ValueError("Could not locate header row containing 'Item number'")

    df = pd.read_excel(path, engine=engine, header=header_row)
    df = df.rename(columns={k: v for k, v in USAGE_COLUMNS.items() if k in df.columns})

    keep = [c for c in USAGE_COLUMNS.values() if c in df.columns]
    df = df[keep].copy()

    df["item_number"] = df["item_number"].astype(str).str.strip().str.upper()
    df = df[df["item_number"].ne("") & df["item_number"].ne("NAN")]

    for col in ("yearly_usage", "safety_stock", "on_hand", "standard_cost"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Money sitting on the rack right now, per item
    df["dollar_value"] = (df["on_hand"] * df["standard_cost"]).round(2)

    df = df.drop_duplicates(subset="item_number", keep="first").reset_index(drop=True)
    return df


def _split_item_tokens(raw_value: str) -> list[str]:
    """Split a raw audit item field into individual item tokens.

    Handles '&'-joined stacks, embedded whitespace lists, and unknown markers.
    Returns a list of (token, is_unknown) preserving unknowns as '?' entries.
    """
    text = str(raw_value).strip().upper()
    if text in UNKNOWN_TOKENS or text == "NAN":
        return ["?"]
    # Split on '&' first, then whitespace within each chunk
    tokens = []
    for chunk in text.split("&"):
        for tok in chunk.split():
            tok = tok.strip()
            if not tok:
                continue
            tokens.append("?" if tok in UNKNOWN_TOKENS else tok)
    return tokens if tokens else ["?"]


def load_physical_audit(path: str) -> pd.DataFrame:
    """Load the physical audit and explode stacked items into one row per item.

    Occupancy for stacked entries is divided equally among the items that
    share the original entry (the audit records occupancy per entry, not
    per stacked item).
    """
    engine = _detect_engine(path)
    df = pd.read_excel(path, engine=engine, sheet_name=0)

    # Drop the embedded summary matrix (unnamed side columns)
    df = df[[c for c in df.columns if not str(c).startswith("Unnamed")]]
    df = df.rename(columns={k: v for k, v in AUDIT_COLUMNS.items() if k in df.columns})

    df = df.dropna(subset=["rack", "cell_id"]).copy()

    # Normalize text fields
    for col in ("rack", "section", "row", "cell_id", "location_label"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    df["row_num"] = df["row"].str.extract(r"(\d+)").astype(int)
    df["occupancy"] = pd.to_numeric(df["occupancy"], errors="coerce").fillna(0)

    # Explode stacked entries
    records = []
    for _, r in df.iterrows():
        tokens = _split_item_tokens(r.get("item_number_raw", ""))
        share = r["occupancy"] / len(tokens) if tokens else 0
        for tok in tokens:
            rec = r.to_dict()
            rec["item_number"] = tok
            rec["occupancy_share"] = round(share, 4)
            rec["is_unknown"] = tok == "?"
            records.append(rec)

    out = pd.DataFrame(records)

    # An entry with no identifiable item AND zero occupancy is an EMPTY cell
    # (available space for move targets), not unknown stock sitting there.
    out["is_empty"] = out["is_unknown"] & (out["occupancy"] <= 0)
    out.loc[out["is_empty"], "is_unknown"] = False

    keep = ["rack", "section", "row", "row_num", "cell_id", "location_label",
            "item_number", "occupancy", "occupancy_share", "is_unknown",
            "is_empty", "cell_width_ft", "notes"]
    return out[[c for c in keep if c in out.columns]].reset_index(drop=True)


def join_audit_to_usage(audit: pd.DataFrame, usage: pd.DataFrame) -> pd.DataFrame:
    """Left-join audit records to the SKU master and tag match status.

    match_status values:
      matched   - item number found in the usage report
      unmatched - a real item number with no usage record (typo, different
                  planner code, or new part)
      unknown   - item could not be identified during the audit ('?')
    """
    merged = audit.merge(usage, on="item_number", how="left", indicator=True)
    merged["match_status"] = "matched"
    merged.loc[merged["_merge"] == "left_only", "match_status"] = "unmatched"
    merged.loc[merged["is_unknown"], "match_status"] = "unknown"
    merged.loc[merged["is_empty"], "match_status"] = "empty"
    return merged.drop(columns="_merge")


def items_not_audited(audit: pd.DataFrame, usage: pd.DataFrame) -> pd.DataFrame:
    """Usage-report items that never appear in the physical audit.

    These are candidates for linking to '?' cells, or they live somewhere
    outside the audited racks.
    """
    audited = set(audit["item_number"])
    return usage[~usage["item_number"].isin(audited)].reset_index(drop=True)
