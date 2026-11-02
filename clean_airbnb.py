"""
Airbnb Open Data (NYC) - complete data cleaning script

Every cleaning step is one small function. It fixes one kind of problem, writes what it did
to the cleaning log, and hands the table (df) to the next step.

PHASES
    2  Structure       tidy column names, remove duplicate listings, drop useless columns
    3  Types           price and fee as numbers, review dates as dates
    4  Text            fix misspelled boroughs, standardise casing, tidy names
    5  Impossible      availability, minimum nights, construction year, review dates
    6  Logic           reviews per month for listings with no reviews; flag what stays inconsistent
    7  Checks          missing values before/after, and pass/fail quality checks

HOW TO RUN
    python clean_airbnb.py --input data/Airbnb_Open_Data.csv
    (the input can also be the .zip file)

OUTPUT (in output/)
    airbnb_clean.csv            the cleaned table
    cleaning_log.csv            one row per fix: what was wrong, how many rows, what we did
    missing_values_report.csv   missing cells per column, before and after cleaning
    quality_checks.csv          pass/fail checks on the finished table
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# The file was published in August 2022, so no review can be dated after this.
DEFAULT_SNAPSHOT_DATE = "2022-08-01"

BOROUGHS = {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"}
BOROUGH_TYPOS = {"brookln": "Brooklyn", "manhatan": "Manhattan"}
ROOM_TYPES = {"entire home/apt", "private room", "shared room", "hotel room"}
PSEUDO_NULLS = {"", "none", "nan", "n/a", "null"}

# =====================================================================
# THE CLEANING LOG
# Every fix writes one line here. It is saved as cleaning_log.csv and pasted into the
# "cleaning_log" tab of the Google Sheet. rows_affected is always a number, so it can be sorted and summed.
# =====================================================================
LOG = []
MISSING_BEFORE = {}  # filled at the end of Phase 2


def log(step: str, problem: str, column: str, rows_affected: int, what_i_did: str, method: str, detail: str = "") -> None:
    LOG.append(
        {
            "step": step,
            "problem": problem,
            "column": column,
            "rows_affected": int(rows_affected),
            "detail": detail,
            "what_i_did": what_i_did,
            "method": method,
        }
    )
    print(f"[{step}] {problem} ({column}): {int(rows_affected):,} rows -> {what_i_did}")


# =====================================================================
# PHASE 2: STRUCTURE
# =====================================================================
def to_snake_case(name: str) -> str:
    """'host id' -> 'host_id',  'Construction year' -> 'construction_year'"""
    return name.strip().lower().replace(" ", "_")


def step_structure(df: pd.DataFrame) -> pd.DataFrame:
    # 2a. Tidy column names
    old_names = list(df.columns)
    df.columns = [to_snake_case(c) for c in old_names]
    renamed = sum(1 for o, n in zip(old_names, df.columns) if o != n)
    log("2a", "Messy column names (capitals and spaces)", "all columns", renamed,
        "Renamed to lowercase snake_case, e.g. 'host id' -> 'host_id'",
        "str.lower() and str.replace(' ', '_')")

    # 2b. Remove duplicate listings (rows identical in every column)
    before = len(df)
    df = df.drop_duplicates(keep="first")
    log("2b", "Duplicate listings (identical rows)", "all columns", before - len(df),
        f"Removed the repeats and kept the first copy ({before:,} -> {len(df):,} rows)",
        "drop_duplicates(keep='first')")

    # Safety check: after removing exact duplicates, every listing id should appear once.
    still_repeated = int(df["id"].duplicated().sum())
    if still_repeated > 0:
        df = df.drop_duplicates(subset=["id"], keep="first")
        log("2b-2", "Same id, different details", "id", still_repeated,
            "Kept the first listing for each id", "drop_duplicates(subset=['id'], keep='first')")

    # 2c. Drop columns that carry no useful information
    drops = {
        "license": "Almost empty: only 2 listings have a value",
        "country": "Constant: every listing is in the United States",
        "country_code": "Constant: every listing is US",
        "house_rules": "Long free text, half empty, not needed for the price question",
    }
    for col, reason in drops.items():
        if col in df.columns:
            filled = int(df[col].notna().sum())
            log("2c", f"Column not useful: {reason}", col, 0, "Dropped the column",
                "drop(columns=...)", detail=f"{filled:,} filled cells removed")
    df = df.drop(columns=[c for c in drops if c in df.columns]).reset_index(drop=True)

    # Remember how many cells were empty now, so Phase 7 can show what the value fixes changed.
    MISSING_BEFORE.update(df.isna().sum().to_dict())
    return df


# =====================================================================
# PHASE 3: TYPES
# =====================================================================
def step_types(df: pd.DataFrame) -> pd.DataFrame:
    # 3a. Price and service fee are text like "$1,020 " -> numbers
    for col in ["price", "service_fee"]:
        text = df[col].astype("string")
        dirty = int(text.str.contains(r"[\$, ]", regex=True, na=False).sum())
        nulls_before = int(df[col].isna().sum())
        cleaned = text.str.replace(r"[\$, ]", "", regex=True).replace({"": pd.NA})
        df[col] = pd.to_numeric(cleaned, errors="coerce")
        new_nulls = int(df[col].isna().sum()) - nulls_before
        log("3a", "Currency stored as text ($, commas, spaces)", col, dirty,
            "Removed the symbols and converted to a number",
            "str.replace() and pd.to_numeric()",
            detail=f"values that could not be read: {new_nulls}")

    # 3b. last_review is text like "10/19/2021" -> a real date
    text = df["last_review"].astype("string")
    present = int(text.notna().sum())
    df["last_review"] = pd.to_datetime(text, format="%m/%d/%Y", errors="coerce")
    unreadable = present - int(df["last_review"].notna().sum())
    log("3b", "Dates stored as text", "last_review", present,
        "Converted to real dates", "pd.to_datetime(format='%m/%d/%Y')",
        detail=f"dates that could not be read: {unreadable}")

    # 3c. instant_bookable is a mix of True/False and blanks -> a true/false column
    before = df["instant_bookable"]
    mapped = before.astype("string").str.strip().str.lower().map({"true": True, "false": False})
    df["instant_bookable"] = mapped.astype("boolean")
    log("3c", "True/False stored as mixed text", "instant_bookable", int(before.notna().sum()),
        "Converted to a true/false column", "map({'true': True, 'false': False})")
    return df


# =====================================================================
# PHASE 4: TEXT
# =====================================================================
def step_text(df: pd.DataFrame) -> pd.DataFrame:
    # 4a. Borough names: fix misspellings, trim spaces, unify casing
    original = df["neighbourhood_group"].astype("string")
    lowered = original.str.strip().str.lower()
    typo_rows = int(lowered.isin(BOROUGH_TYPOS).sum())
    fixed = lowered.map(BOROUGH_TYPOS).fillna(lowered.str.title())
    changed = int((original.notna() & (original != fixed)).sum())
    df["neighbourhood_group"] = fixed
    unknown = set(df["neighbourhood_group"].dropna().unique()) - BOROUGHS
    log("4a", "Misspelled boroughs ('brookln', 'manhatan')", "neighbourhood_group", changed,
        "Fixed the spelling and unified the casing", "map(typo_map) and str.title()",
        detail=f"{typo_rows} misspellings; values outside the 5 boroughs: {len(unknown)}")

    # 4b. Other category columns: trim spaces, lowercase, turn fake blanks ('none', 'nan') into real blanks
    for col in ["host_identity_verified", "cancellation_policy", "room_type"]:
        original = df[col].astype("string")
        lowered = original.str.strip().str.lower()
        pseudo = int(lowered.isin(PSEUDO_NULLS).sum())
        new = lowered.mask(lowered.isin(PSEUDO_NULLS))
        changed = int(((original != new) & ~(original.isna() & new.isna())).fillna(False).sum())
        df[col] = new
        log("4b", "Inconsistent casing or fake blanks", col, changed,
            "Trimmed, lowercased, fake blanks made real blanks", "str.strip().str.lower()",
            detail=f"fake blanks found: {pseudo}")

    # 4c. Listing and host names: trim and collapse repeated spaces
    for col in ["name", "host_name"]:
        original = df[col].astype("string")
        tidy = original.str.strip().str.replace(r"\s+", " ", regex=True)
        changed = int((original.notna() & (original != tidy)).sum())
        df[col] = tidy
        log("4c", "Extra spaces in text", col, changed,
            "Trimmed the ends and collapsed repeated spaces", "str.strip() and str.replace(r'\\s+', ' ')")
    return df


# =====================================================================
# PHASE 5: IMPOSSIBLE VALUES
# =====================================================================
def step_impossible(df: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    # 5a. A listing can be available 0 to 365 days a year
    bad = (df["availability_365"] < 0) | (df["availability_365"] > 365)
    below, above = int((df["availability_365"] < 0).sum()), int((df["availability_365"] > 365).sum())
    df.loc[bad, "availability_365"] = np.nan
    log("5a", "Availability outside 0-365 days", "availability_365", bad.sum(),
        "Set the impossible values to blank (not capped at 365)", "df.loc[mask] = np.nan",
        detail=f"{below} below 0, {above} above 365")

    # 5b. Minimum nights must be 1 to 365
    bad = (df["minimum_nights"] <= 0) | (df["minimum_nights"] > 365)
    below, above = int((df["minimum_nights"] <= 0).sum()), int((df["minimum_nights"] > 365).sum())
    df.loc[bad, "minimum_nights"] = np.nan
    log("5b", "Minimum nights below 1 or above a year", "minimum_nights", bad.sum(),
        "Set the impossible values to blank", "df.loc[mask] = np.nan",
        detail=f"{below} below 1, {above} above 365")

    # 5c. Construction year: after 1900 and not after the year the data was published
    bad = (df["construction_year"] < 1900) | (df["construction_year"] > snapshot.year)
    df.loc[bad, "construction_year"] = np.nan
    log("5c", f"Construction year before 1900 or after {snapshot.year}", "construction_year", bad.sum(),
        "Set the impossible values to blank", "df.loc[mask] = np.nan")

    # 5d. A review cannot be dated after the file was published (not "after today")
    bad = df["last_review"] > snapshot
    latest = df["last_review"][bad].max()
    df.loc[bad, "last_review"] = pd.NaT
    log("5d", f"Review dates after {snapshot.date()} (e.g. 2058)", "last_review", bad.sum(),
        "Set the impossible dates to blank", "df.loc[mask] = pd.NaT",
        detail=f"latest impossible date: {latest.date() if pd.notna(latest) else 'none'}")

    # 5e. Whole-number columns -> whole numbers (no more 2020.0)
    whole = ["construction_year", "minimum_nights", "number_of_reviews", "review_rate_number",
             "calculated_host_listings_count", "availability_365"]
    for col in whole:
        not_whole = int((df[col].dropna() % 1 != 0).sum())
        assert not_whole == 0, f"{col} has {not_whole} non-whole numbers"
        df[col] = df[col].astype("Int64")
    log("5e", "Counts stored as decimals (e.g. 2020.0)", ", ".join(whole), len(df),
        "Converted to whole numbers", "astype('Int64')")
    return df


# =====================================================================
# PHASE 6: LOGIC
# =====================================================================
def step_logic(df: pd.DataFrame) -> pd.DataFrame:
    # 6a. No reviews means zero reviews per month
    mask = (df["number_of_reviews"] == 0) & df["reviews_per_month"].isna()
    df.loc[mask, "reviews_per_month"] = 0.0
    log("6a", "Reviews per month blank on listings with zero reviews", "reviews_per_month", mask.sum(),
        "Filled with 0.0", "df.loc[mask, 'reviews_per_month'] = 0.0")

    # 6b. Things that look wrong but cannot be fixed from the data: flagged, left blank
    reviewed = df["number_of_reviews"] > 0
    no_date = int((reviewed & df["last_review"].isna()).sum())
    no_rate = int((reviewed & df["reviews_per_month"].isna()).sum())
    log("6b", "Listing has reviews but no last-review date", "last_review", no_date,
        "Flagged only: left blank because the true date is unknown", "flag, no change")
    log("6b", "Listing has reviews but no reviews-per-month", "reviews_per_month", no_rate,
        "Flagged only: left blank because the rate cannot be rebuilt", "flag, no change")
    busy = int((df["reviews_per_month"] > 30).sum())
    log("6b", "More than 30 reviews a month (more than one a day)", "reviews_per_month", busy,
        "Flagged only: unusual but possible, so kept", "flag, no change")
    return df


# =====================================================================
# PHASE 7: CHECKS
# =====================================================================
def missing_report(df: pd.DataFrame) -> pd.DataFrame:
    after = df.isna().sum()
    out = pd.DataFrame({"column": after.index})
    out["missing_before"] = out["column"].map(MISSING_BEFORE).astype(int)
    out["missing_after"] = after.values
    out["change"] = out["missing_after"] - out["missing_before"]
    out["pct_missing_after"] = (out["missing_after"] / len(df) * 100).round(2)
    return out


def run_checks(df: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    text_cols = ["name", "host_name"]
    tidy = all(
        (df[c].dropna() == df[c].dropna().str.strip().str.replace(r"\s+", " ", regex=True)).all() for c in text_cols
    )
    checks = [
        ("Every listing id appears once", bool(df["id"].is_unique)),
        ("No fully duplicated rows", not df.duplicated().any()),
        ("All column names are lowercase with underscores", all(c == to_snake_case(c) for c in df.columns)),
        ("price is numeric and positive", pd.api.types.is_numeric_dtype(df["price"]) and bool((df["price"].dropna() > 0).all())),
        ("service_fee is numeric", pd.api.types.is_numeric_dtype(df["service_fee"])),
        ("availability_365 is between 0 and 365", bool(df["availability_365"].dropna().between(0, 365).all())),
        ("minimum_nights is between 1 and 365", bool(df["minimum_nights"].dropna().between(1, 365).all())),
        (f"No review dated after {snapshot.date()}", bool((df["last_review"].dropna() <= snapshot).all())),
        ("last_review is a date column", pd.api.types.is_datetime64_any_dtype(df["last_review"])),
        ("Only the 5 real boroughs remain", set(df["neighbourhood_group"].dropna().unique()) <= BOROUGHS),
        ("Only the 4 known room types remain", set(df["room_type"].dropna().unique()) <= ROOM_TYPES),
        ("No stray spaces in name or host_name", tidy),
        ("Listings with zero reviews have 0 reviews per month",
         bool((df.loc[df["number_of_reviews"] == 0, "reviews_per_month"] == 0).all())),
    ]
    result = pd.DataFrame(checks, columns=["check", "passed"])
    result["result"] = np.where(result["passed"], "PASS", "FAIL")
    for r in result.itertuples():
        print(f"   [{r.result}] {r.check}")
    return result[["check", "result"]]


# =====================================================================
# MAIN
# =====================================================================
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/Airbnb_Open_Data.csv", help="the CSV (or a .zip containing it)")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT_DATE,
                    help="the date the data was published; reviews after it are impossible")
    args = ap.parse_args()
    snapshot = pd.Timestamp(args.snapshot_date)

    df = pd.read_csv(args.input, low_memory=False)
    rows_in, cols_in = df.shape
    print(f"Loaded {rows_in:,} rows and {cols_in} columns\n")

    df = step_structure(df)
    df = step_types(df)
    df = step_text(df)
    df = step_impossible(df, snapshot)
    df = step_logic(df)

    print("\nQuality checks:")
    checks = run_checks(df, snapshot)
    missing = missing_report(df)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "airbnb_clean.csv", index=False)
    pd.DataFrame(LOG).to_csv(out / "cleaning_log.csv", index=False)
    missing.to_csv(out / "missing_values_report.csv", index=False)
    checks.to_csv(out / "quality_checks.csv", index=False)

    print(f"\nRows            : {rows_in:,} -> {len(df):,}")
    print(f"Columns         : {cols_in} -> {df.shape[1]}")
    print(f"Logged steps    : {len(LOG)}")
    print(f"Checks passed   : {int((checks['result'] == 'PASS').sum())} of {len(checks)}")
    print(f"Saved in        : {out}/")
    if (checks["result"] == "FAIL").any():
        raise SystemExit("Some quality checks failed. Read the FAIL lines above.")


if __name__ == "__main__":
    main()