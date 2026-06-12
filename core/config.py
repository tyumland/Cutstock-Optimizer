"""
Rack configuration for the Rack Slotting Optimizer.

The rack structure (racks, sections, rows) and the tier-to-row slotting
rules live in config/racks.yaml. Adding a rack, section, or row later is
a config edit only - no code changes.

generate_config_from_audit() bootstraps the yaml from a physical audit
file so the structure always matches reality on first run. After that the
yaml is the source of truth and is editable from the dashboard settings.
"""

import yaml
import pandas as pd

DEFAULT_RULES = {
    # Tier -> list of row numbers where that tier belongs.
    # Row 1 is the floor (most accessible); numbers increase going up.
    "A": [1, 2, 3],
    "A_overstock": [4],   # must be the same section (column) as the primary A item
    "B": [4, 5],          # only where row 4 is not occupied by A overstock
    "C": [6, 7],
    "idle_flag_days": 60,           # user-facing label; see idle logic notes
    "idle_min_snapshots": 2,        # consecutive monthly snapshots above safety stock
    "abc_a_pct": 0.80,              # cumulative usage share boundary for A
    "abc_b_pct": 0.95,              # cumulative usage share boundary for B
}


def generate_config_from_audit(audit: pd.DataFrame, out_path: str) -> dict:
    """Derive the physical rack structure from an audit dataframe and save it."""
    racks = []
    for rack_name, grp in audit.groupby("rack", sort=True):
        sections = []
        for section_name, sgrp in grp.groupby("section", sort=True):
            sections.append({
                "name": str(section_name),
                "rows": sorted(sgrp["row_num"].unique().tolist()),
            })
        racks.append({"name": str(rack_name), "sections": sections})

    config = {"racks": racks, "rules": DEFAULT_RULES}
    with open(out_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    return config


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def save_config(config: dict, path: str) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)
