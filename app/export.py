"""CSV / Excel export of a job's records."""
import io

import pandas as pd


def to_frame(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records)


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
