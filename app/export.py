"""CSV / Excel export of a job's records."""
import io

import pandas as pd


FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\uff1d", "\uff0b", "\uff0d", "\uff20")


def safe_cell(value):
    """Stop spreadsheet formula injection: text starting with = + - @ (or tab / CR) is prefixed
    with an apostrophe so Excel shows it as text instead of running it. Plain numbers stay numbers."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        try:
            float(value.replace(",", ""))
            return value          # "-12.5" is a number, not a formula
        except ValueError:
            return "'" + value
    return value


def to_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.map(safe_cell)
        df.columns = [safe_cell(str(c)) for c in df.columns]
    return df


def to_csv(records: list[dict]) -> bytes:
    # utf-8-sig so Excel shows ₹ and other non-ASCII characters correctly.
    return to_frame(records).to_csv(index=False).encode("utf-8-sig")


def to_xlsx(records: list[dict]) -> bytes:
    buf = io.BytesIO()
    df = to_frame(records)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="results")
        ws = xw.sheets["results"]
        for i, col in enumerate(df.columns, 1):
            width = min(60, max(len(str(col)), *(len(str(v)) for v in df[col].head(200))) + 2) \
                if len(df) else len(str(col)) + 2
            ws.column_dimensions[ws.cell(1, i).column_letter].width = width
        ws.freeze_panes = "A2"
    return buf.getvalue()
