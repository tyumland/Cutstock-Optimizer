# Storage & Inventory Optimizer

A guided Streamlit application for combining a physical rack audit with usage, updated safety stock, on-hand quantity, and standard cost data.

## What changed

- Guided upload and validation workflow
- Quantity-level keep and excess recommendations
- P7 zero-usage, P8 active-overstock, and P9 safety-stock review groups
- Separate Current State and New Layout views
- Optional approved/final layout upload
- Plain-English explanations and tooltips
- Finance/removal review page that avoids treating review value as an automatic write-off
- Complete implementation workbook export
- Configurable retention multiplier, P8 thresholds, slotting tolerance, and New Layout capacity

## Required files

### Physical audit
Expected columns include Rack / Area, Section, Row / Level, Cell / Position, Location Label, Item Number, Estimated Cell Usage, Rack Cell Width (ft), and Notes.

### Usage and updated safety stock
Expected columns include Item number, Item description, Yearly, Safety, Quantity, and Standard cost.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Default decision policy

- Retain up to 2x updated safety stock
- P7: zero recorded yearly usage with excess stock/value
- P8: active excess of at least 100 units by default
- P9: smaller active excess below the P8 materiality threshold

All thresholds are editable in the sidebar. Removal from the New Layout is a review recommendation; final disposition remains a separate approval decision.
