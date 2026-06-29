"""Convert the ground-truth benchmarking CSV datasets into JSON.

The manual test datasets in ``Test_Datasets/`` (``;``-delimited CSV) share the
exact column set defined in ``data-format/phase3_analysis.json``. This module
maps each CSV column to its schema key and writes a JSON file shaped like the
Phase 3 output, so the analyzer's results can be compared JSON-to-JSON against
the ground truth.

In these CSVs the DTx-level columns are repeated on every study row, so studies
are grouped by consecutive equal ``DTx Name``.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_PATH = Path("data-format/phase3_analysis.json")
DEFAULT_FILES = [
    Path("Test_Datasets/test_dataset_benchmarking_numbers.csv"),
    Path("Test_Datasets/test_dataset_benchmarking_analysis.csv"),
]

_EMPTY_TOKENS = {"", "n/a", "na", "none", "null", "not available", "not found", "-"}


def _load_schema_columns() -> List[Dict[str, Any]]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)["columns"]


def _norm(value: Any) -> str:
    """Normalize a cell: strip, and map NA-like tokens to ''."""
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in _EMPTY_TOKENS else s


def convert_csv(csv_path: Path, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert one ground-truth CSV into the Phase 3-shaped JSON structure."""
    keys = [c["key"] for c in columns]
    # Map by stripped label so trailing-space differences don't matter.
    label_to_key = {c["label"].strip(): c["key"] for c in columns}
    name_key = columns[0]["key"]  # first column is the DTx name

    rows: List[Dict[str, str]] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        header = next(reader)
        # Position -> schema key (None for unknown/extra columns)
        col_keys = [label_to_key.get((h or "").strip()) for h in header]

        for raw in reader:
            if not any((c or "").strip() for c in raw):
                continue  # skip fully blank lines
            row = {k: "" for k in keys}
            for idx, cell in enumerate(raw):
                if idx < len(col_keys) and col_keys[idx]:
                    row[col_keys[idx]] = _norm(cell)
            rows.append(row)

    # Group consecutive rows by DTx name.
    by_dtx: List[Dict[str, Any]] = []
    for row in rows:
        name = row.get(name_key, "")
        if by_dtx and by_dtx[-1]["dtx_name"] == name:
            by_dtx[-1]["studies"].append(row)
        else:
            by_dtx.append({"dtx_name": name, "studies": [row]})

    return {
        "metadata": {
            "source_csv": str(csv_path),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_rows": len(rows),
            "dtx_count": len(by_dtx),
            "columns": keys,
        },
        "rows": rows,
        "by_dtx": by_dtx,
    }


def convert_all(files: List[Path] = None) -> List[Tuple[Path, Dict[str, Any]]]:
    """Convert each CSV and write a sibling .json. Returns (out_path, payload)."""
    columns = _load_schema_columns()
    results: List[Tuple[Path, Dict[str, Any]]] = []
    for csv_path in (files or DEFAULT_FILES):
        if not csv_path.exists():
            raise FileNotFoundError(f"Test dataset CSV not found: {csv_path}")
        payload = convert_csv(csv_path, columns)
        out_path = csv_path.with_suffix(".json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        results.append((out_path, payload))
    return results
