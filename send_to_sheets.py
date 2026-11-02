"""
Send the cleaning outputs to their tabs in a Google Sheet.

    raw_sample      first 1,000 rows of the RAW file (so reviewers can see the mess)
    cleaning_log    output/cleaning_log.csv
    before_after    output/missing_values_report.csv
    quality_checks  output/quality_checks.csv

SETUP (once): see the steps in the chat. You need
    pip install gspread
    a service-account key file saved as  service_account.json  (next to this script)
    your Google Sheet shared with the service account's email (as Editor)

RUN
    python send_to_sheets.py --sheet "Airbnb Cleaning Portfolio"
    python send_to_sheets.py --sheet-id 1AbC...   (the long id in the Sheet's URL)
"""
import argparse
from pathlib import Path

import gspread
import pandas as pd

RAW_SAMPLE_ROWS = 1000
# tab name -> (file to read, how many rows to send; None = all)
TABS = {
    "cleaning_log": ("output/cleaning_log.csv", None),
    "before_after": ("output/missing_values_report.csv", None),
    "quality_checks": ("output/quality_checks.csv", None),
}


def to_rows(df: pd.DataFrame) -> list:
    """Header + rows as plain Python values. Empty cells become '' and whole numbers stay numbers."""
    df = df.astype(object).where(df.notna(), "")
    rows = [list(df.columns)]
    for rec in df.itertuples(index=False):
        rows.append([v.item() if hasattr(v, "item") else v for v in rec])
    return rows


def send(book, tab: str, df: pd.DataFrame) -> None:
    try:
        ws = book.worksheet(tab)
        ws.clear()  # empty the tab so reruns never leave old rows behind
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=tab, rows=len(df) + 1, cols=len(df.columns))
    rows = to_rows(df)
    ws.resize(rows=len(rows), cols=len(df.columns))
    # RAW = store exactly what we send, so "$1,020 " in the raw sample stays text
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"  sent {len(df):,} rows x {len(df.columns)} columns -> tab '{tab}'")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="the Sheet's name")
    ap.add_argument("--sheet-id", help="the long id from the Sheet's URL (safer than the name)")
    ap.add_argument("--key", default="service_account.json")
    ap.add_argument("--raw", default="data/Airbnb_Open_Data.csv")
    args = ap.parse_args()
    if not (args.sheet or args.sheet_id):
        raise SystemExit("Give --sheet 'name' or --sheet-id 'id'")

    gc = gspread.service_account(filename=args.key)
    book = gc.open_by_key(args.sheet_id) if args.sheet_id else gc.open(args.sheet)
    print(f"Connected to '{book.title}'")

    # raw sample: read everything as text so nothing is changed on the way in
    raw = pd.read_csv(args.raw, nrows=RAW_SAMPLE_ROWS, dtype=str, keep_default_na=False)
    send(book, "raw_sample", raw)

    for tab, (path, limit) in TABS.items():
        if not Path(path).exists():
            print(f"  SKIPPED '{tab}': {path} not found. Run clean_airbnb.py first.")
            continue
        df = pd.read_csv(path, nrows=limit)
        send(book, tab, df)
    print("Done.")


if __name__ == "__main__":
    main()