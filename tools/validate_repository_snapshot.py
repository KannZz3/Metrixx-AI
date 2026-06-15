#!/usr/bin/env python3
"""Build a compact reproducibility snapshot for the public Metrixx-AI subset.

The script intentionally uses only the Python standard library so it can run
before optional fetcher dependencies are installed. It audits checked-in
validation reports and key CSV outputs without calling external data sources.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


VALIDATION_REPORTS = [
    {
        "stage": "eia_energy_physical_prices",
        "path": "Data_light_version/EIA/eia_normalization_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "usda_grain_physical_prices",
        "path": "Data_light_version/USDA/usda_grain_physical_prices_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "cftc_cot_positioning",
        "path": "Data_light_version/CFTC/cftc_cot_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "cftc_cot_scoring",
        "path": "COT_scoring_positioning/cftc_cot_scoring_validation_report.json",
        "ok_key": "all_scoring_valid",
    },
    {
        "stage": "reuters_eia_narrative",
        "path": "Data_light_version/Reuters/narrative_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "baker_hughes_rig_count",
        "path": "Data_light_version/Baker Hughes/baker_hughes_rig_count_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "fred_macro_overlay",
        "path": "Data_light_version/FRED/fred_macro_overlay_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "massive_cme_futures",
        "path": "Data_light_version/CME/massive_cme_futures_validation_report.json",
        "ok_key": "all_required_ok",
    },
    {
        "stage": "basis_sentiment_from_massive_cme",
        "path": "Basis_sentiment/basis_sentiment_from_massive_cme_validation_report.json",
        "ok_key": "all_required_ok",
    },
]


CSV_OUTPUTS = [
    "Data_light_version/USDA/usda_grain_physical_prices_normalized.csv",
    "Data_light_version/CFTC/cftc_cot_positioning_normalized.csv",
    "COT_scoring_positioning/cftc_cot_scores.csv",
    "Data_light_version/Reuters/narrative_events_normalized.csv",
    "Data_light_version/Baker Hughes/baker_hughes_rig_count_normalized.csv",
    "Data_light_version/FRED/fred_macro_overlay_normalized.csv",
    "Data_light_version/CME/massive_cme_futures_normalized.csv",
    "Basis_sentiment/accepted_cl_2026-05-26.csv",
    "Basis_sentiment/ng_zc_zs_2026-05-28.csv",
    "Basis_sentiment/basis_sentiment_from_massive_cme.csv",
]


SAMPLE_FIELDS = [
    "source_id",
    "trade_date",
    "instrument",
    "symbol",
    "basis_label",
    "series_key",
    "futures_contract",
    "target_commodity",
    "timestamp",
    "report_date",
    "value",
    "cot_score",
    "cot_signal",
    "basis_ready",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {"value": data}


def count_csv(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        row_count = 0
        first_row: Optional[Dict[str, str]] = None
        for row in reader:
            row_count += 1
            if first_row is None:
                first_row = row

    fieldnames = reader.fieldnames or []
    sample = {
        key: first_row[key]
        for key in SAMPLE_FIELDS
        if first_row is not None and key in first_row and first_row[key] not in ("", None)
    }
    return {
        "path": path.as_posix(),
        "exists": True,
        "row_count": row_count,
        "column_count": len(fieldnames),
        "sample": sample,
    }


def summarize_validation(root: Path, spec: Dict[str, str]) -> Dict[str, Any]:
    rel_path = spec["path"]
    path = root / rel_path
    if not path.exists():
        return {
            "stage": spec["stage"],
            "path": rel_path,
            "exists": False,
            "ok": False,
            "errors": ["validation report is missing"],
        }

    data = load_json(path)
    ok_key = spec["ok_key"]
    ok = bool(data.get(ok_key))
    summary: Dict[str, Any] = {
        "stage": spec["stage"],
        "path": rel_path,
        "exists": True,
        "ok_key": ok_key,
        "ok": ok,
    }
    for key in (
        "validated_at",
        "normalized_at",
        "record_count",
        "total_records",
        "total_score_records",
        "total_normalized_records",
        "latest_date",
        "earliest_date",
    ):
        if key in data:
            summary[key] = data[key]
    if data.get("errors"):
        summary["errors"] = data["errors"]
    if data.get("warnings"):
        summary["warning_count"] = len(data["warnings"])
        summary["warnings"] = data["warnings"][:5]
    return summary


def summarize_csv_outputs(root: Path, paths: Iterable[str]) -> List[Dict[str, Any]]:
    summaries = []
    for rel_path in paths:
        path = root / rel_path
        if not path.exists():
            summaries.append({"path": rel_path, "exists": False, "row_count": 0, "column_count": 0})
            continue
        item = count_csv(path)
        item["path"] = rel_path
        summaries.append(item)
    return summaries


def build_snapshot(root: Path) -> Dict[str, Any]:
    validation = [summarize_validation(root, spec) for spec in VALIDATION_REPORTS]
    csv_outputs = summarize_csv_outputs(root, CSV_OUTPUTS)
    catalog_path = root / "catalog.json"
    catalog = load_json(catalog_path) if catalog_path.exists() else {}
    return {
        "generated_at_utc": utc_now(),
        "repository": "Metrixx-AI",
        "snapshot_scope": "checked-in public subset outputs only; no external fetches",
        "overall_ok": all(item.get("ok") for item in validation),
        "validation_reports": validation,
        "csv_outputs": csv_outputs,
        "catalog_version": catalog.get("catalog_version"),
        "catalog_source_count": len(catalog.get("sources", [])) if isinstance(catalog.get("sources"), list) else 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root, help="Repository root.")
    parser.add_argument(
        "--output",
        type=Path,
        default=default_root / "artifacts" / "repository_snapshot.json",
        help="Snapshot JSON output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    snapshot = build_snapshot(root)
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"snapshot_written={output}")
    print(f"overall_ok={snapshot['overall_ok']}")
    return 0 if snapshot["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
