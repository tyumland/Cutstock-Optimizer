# Cutstock Rack Slotting Optimizer

Monthly slotting review tool for the Andersen Windows cutstock storage area.
Upload the monthly usage report and physical audit, get Stay/Move/Flag
recommendations per item, a rack map, utilization heatmap, and a
consolidation fit-check against the future layout.

## Setup (once)
    pip install -r requirements.txt

## Run the dashboard
    streamlit run app.py
Then upload both files in the sidebar.

## Run the pipeline from the command line
    python run_pipeline.py Usage_Data.XLS Cutstock_Physical_Audit.xlsx

## Project structure
    app.py              Streamlit dashboard
    run_pipeline.py     command-line pipeline check
    core/ingest.py      file parsing + cleaning (.xls and .xlsx)
    core/classify.py    ABC classification + idle flagging
    core/recommend.py   Stay/Move/Flag engine with target cells
    core/layout.py      consolidation fit analysis (linear feet)
    core/config.py      rack structure yaml generation
    ui/rack_map.py      plotly rack visualizations
    export.py           Excel export for the floor
    config/racks.yaml   rack structure + slotting rules (auto-generated)
    storage/snapshots/  saved monthly states (month-over-month diff, week 4)
