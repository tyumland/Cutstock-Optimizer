"""
Run the Week 1-2 data pipeline against real files and print a summary.

Usage:
    python run_pipeline.py <usage_report.xls> <physical_audit.xlsx>
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.ingest import (load_usage_report, load_physical_audit,
                         join_audit_to_usage, items_not_audited)
from core.config import generate_config_from_audit
from core.classify import classify_abc, tier_summary


def main(usage_path: str, audit_path: str):
    usage = load_usage_report(usage_path)
    audit = load_physical_audit(audit_path)
    classified = classify_abc(usage)
    merged = join_audit_to_usage(audit, classified)

    generate_config_from_audit(audit, str(Path(__file__).resolve().parent / "config" / "racks.yaml"))

    print(f"SKUs in usage report:        {len(usage)}")
    print(f"Physical item-cell records:  {len(audit)}")
    print(f"Empty cells (move targets):  {merged[merged.match_status == 'empty'].cell_id.nunique()}")
    print(f"Unknown-stock cells:         {merged[merged.match_status == 'unknown'].cell_id.nunique()}")
    print()
    print("Tier summary (units-based ABC, dollars shown for context):")
    print(tier_summary(classified).to_string(index=False))
    print()
    print("Match status of physical records:")
    print(merged.match_status.value_counts().to_string())
    print()
    idle = classified[classified.idle_flag]
    print(f"Idle items (yearly=0, on-hand>0): {len(idle)} "
          f"worth ${idle.dollar_value.sum():,.2f} sitting in storage")
    print()
    print("Tier vs current row (matched items):")
    m = merged[merged.match_status == "matched"]
    print(m.pivot_table(index="tier", columns="row_num",
                        values="item_number", aggfunc="count",
                        fill_value=0).to_string())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
