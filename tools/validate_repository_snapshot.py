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
import re
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


FETCHER_AND_BUILD_SCRIPTS = [
    "Data_light_version/EIA/eia_physical_price_fetcher.py",
    "Data_light_version/EIA/normalize_eia.py",
    "Data_light_version/USDA/usda_unified_fetcher_light_v4_commodity_split_fixed.py",
    "Data_light_version/USDA/usda_unified_fetcher_light_v2.py",
    "Data_light_version/USDA/usda_broad_search_corn_targets.py",
    "Data_light_version/CFTC/cftc_cot_fetcher_light_v5_field_quality_fixed.py",
    "Data_light_version/CFTC/cftc_cot_fetcher_light_v4_fixed.py",
    "COT_scoring_positioning/cftc_cot_scoring_v1.2.py",
    "Data_light_version/CME/massive_cme_futures_fetcher.py",
    "Data_light_version/Reuters/_reuters_eia_narrative_light_v1_6.py",
    "Data_light_version/Reuters/narrative_ng_zc_zs.py",
    "Data_light_version/Baker Hughes/baker_hughes_rig_count_light_2026.py",
    "Basis_sentiment/build_basis_sentiment_from_cme.py",
]


TEXT_SCAN_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".py", ".txt"}
SECRET_PATTERNS = [
    (
        "raw_api_key_value",
        re.compile(r'"api[_-]?key"\s*:\s*"(?!<REDACTED|REDACTED_ENV|your_key_here)[A-Za-z0-9_\-]{16,}"', re.IGNORECASE),
    ),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {"value": data}


def parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            if fmt == "%Y-%m-%dT%H:%M:%S%z":
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt)
        except Exception:
            pass
    return None


def day_diff(later: Any, earlier: Any) -> Optional[int]:
    later_dt = parse_dt(later)
    earlier_dt = parse_dt(earlier)
    if not later_dt or not earlier_dt:
        return None
    return (later_dt.date() - earlier_dt.date()).days


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def close_enough(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    left_f = safe_float(left)
    right_f = safe_float(right)
    if left_f is None or right_f is None:
        return left_f is None and right_f is None
    return abs(left_f - right_f) <= tolerance


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


def audit_scripts(root: Path) -> Dict[str, Any]:
    results = []
    for rel_path in FETCHER_AND_BUILD_SCRIPTS:
        path = root / rel_path
        errors = []
        if not path.exists():
            errors.append("script is missing")
        else:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                errors.append(str(exc))
        results.append({"path": rel_path, "exists": path.exists(), "ok": not errors, "errors": errors})
    return {
        "checked_at_utc": utc_now(),
        "all_ok": all(item["ok"] for item in results),
        "script_count": len(results),
        "results": results,
    }


def scan_for_secrets(root: Path) -> Dict[str, Any]:
    findings = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in TEXT_SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append(
                    {
                        "pattern": name,
                        "path": path.relative_to(root).as_posix(),
                        "line": line_no,
                    }
                )
    return {
        "checked_at_utc": utc_now(),
        "all_clear": not findings,
        "finding_count": len(findings),
        "findings": findings,
    }


def term_structure_signal(spread: Optional[float]) -> str:
    if spread is None:
        return "not_available"
    if spread > 0:
        return "contango"
    if spread < 0:
        return "backwardation"
    return "flat"


def audit_basis_consistency(root: Path) -> Dict[str, Any]:
    basis_path = root / "Basis_sentiment/basis_sentiment_from_massive_cme.json"
    cme_path = root / "Data_light_version/CME/massive_cme_futures_normalized.json"
    errors = []
    warnings = []
    row_results = []

    if not basis_path.exists():
        errors.append("basis sentiment output is missing")
    if not cme_path.exists():
        errors.append("CME normalized futures output is missing")
    if errors:
        return {"checked_at_utc": utc_now(), "all_ok": False, "errors": errors, "warnings": warnings, "row_results": row_results}

    basis_rows = load_json(basis_path).get("records", [])
    cme_rows = load_json(cme_path).get("records", [])
    cme_by_key = {
        (row.get("symbol"), row.get("trade_date"), row.get("futures_contract")): row
        for row in cme_rows
    }
    cme_by_symbol_date: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in cme_rows:
        cme_by_symbol_date.setdefault((row.get("symbol"), row.get("trade_date")), []).append(row)

    for row in basis_rows:
        label = row.get("basis_label")
        symbol = row.get("symbol")
        trade_date = row.get("trade_date")
        row_errors = []
        row_warnings = []

        future = cme_by_key.get((symbol, trade_date, row.get("futures_contract")))
        if future is None:
            row_errors.append("No matching CME futures row for symbol/trade_date/futures_contract.")
        else:
            if not future.get("is_front_month"):
                row_errors.append("Matched CME futures row is not marked front month.")
            if not close_enough(row.get("futures_settlement_common"), future.get("settlement_common_unit_value")):
                row_errors.append("futures_settlement_common does not match CME normalized settlement_common_unit_value.")

        curve_rows = sorted(
            cme_by_symbol_date.get((symbol, trade_date), []),
            key=lambda item: int(item.get("contract_rank") or 999),
        )
        front = next((item for item in curve_rows if item.get("contract_rank") == 1), None)
        second = next((item for item in curve_rows if item.get("contract_rank") == 2), None)
        if row.get("front_next_spread_common") not in (None, ""):
            if row.get("term_structure_trade_date") != trade_date:
                row_errors.append("term_structure_trade_date does not equal basis trade_date.")
            if not front or not second:
                row_errors.append("front_next_spread_common is populated but same-date front/second CME rows are unavailable.")
            else:
                expected_spread = round(
                    float(second["settlement_common_unit_value"]) - float(front["settlement_common_unit_value"]),
                    8,
                )
                if not close_enough(row.get("front_next_spread_common"), expected_spread, 1e-8):
                    row_errors.append(f"front_next_spread_common mismatch: expected {expected_spread}.")
                expected_signal = term_structure_signal(expected_spread)
                if row.get("term_structure_signal") != expected_signal:
                    row_errors.append(f"term_structure_signal mismatch: expected {expected_signal}.")

        if row.get("basis_ready"):
            physical_value = safe_float(row.get("physical_price_converted"))
            futures_value = safe_float(row.get("futures_settlement_converted"))
            basis_value = safe_float(row.get("basis_value_asof"))
            if physical_value is None or futures_value is None or basis_value is None:
                row_errors.append("basis_ready row is missing converted physical/futures/basis values.")
            else:
                expected_basis = round(physical_value - futures_value, 6)
                if not close_enough(basis_value, expected_basis):
                    row_errors.append(f"basis_value_asof mismatch: expected {expected_basis}.")

            physical_lag = day_diff(trade_date, row.get("physical_price_asof_date"))
            if physical_lag != row.get("physical_lag_days"):
                row_errors.append(f"physical_lag_days mismatch: expected {physical_lag}.")
            if physical_lag is not None and physical_lag < 0:
                row_errors.append("physical date is after futures trade date.")
            if physical_lag is not None and physical_lag > 14:
                row_warnings.append(f"physical leg is stale by {physical_lag} days.")

        if row.get("cot_report_date"):
            cot_lag = day_diff(trade_date, row.get("cot_report_date"))
            if cot_lag != row.get("cot_lag_days"):
                row_errors.append(f"cot_lag_days mismatch: expected {cot_lag}.")
            if cot_lag is not None and cot_lag < 0:
                row_errors.append("COT report date is after futures trade date.")

        if row.get("narrative_latest_event_timestamp"):
            narrative_lag = day_diff(trade_date, row.get("narrative_latest_event_timestamp"))
            if narrative_lag != row.get("narrative_lag_days"):
                row_errors.append(f"narrative_lag_days mismatch: expected {narrative_lag}.")
            if narrative_lag is not None and narrative_lag < 0:
                row_errors.append("Narrative latest event timestamp is after futures trade date.")

        row_results.append(
            {
                "basis_label": label,
                "symbol": symbol,
                "trade_date": trade_date,
                "basis_ready": row.get("basis_ready"),
                "ok": not row_errors,
                "errors": row_errors,
                "warnings": row_warnings,
            }
        )
        errors.extend([f"{label}: {error}" for error in row_errors])
        warnings.extend([f"{label}: {warning}" for warning in row_warnings])

    return {
        "checked_at_utc": utc_now(),
        "all_ok": not errors,
        "record_count": len(basis_rows),
        "basis_ready_count": sum(1 for row in basis_rows if row.get("basis_ready")),
        "errors": errors,
        "warnings": warnings,
        "row_results": row_results,
    }


def build_snapshot(root: Path) -> Dict[str, Any]:
    validation = [summarize_validation(root, spec) for spec in VALIDATION_REPORTS]
    csv_outputs = summarize_csv_outputs(root, CSV_OUTPUTS)
    script_audit = audit_scripts(root)
    secret_scan = scan_for_secrets(root)
    basis_consistency = audit_basis_consistency(root)
    catalog_path = root / "catalog.json"
    catalog = load_json(catalog_path) if catalog_path.exists() else {}
    return {
        "generated_at_utc": utc_now(),
        "repository": "Metrixx-AI",
        "snapshot_scope": "checked-in public subset outputs only; no external fetches",
        "overall_ok": (
            all(item.get("ok") for item in validation)
            and script_audit.get("all_ok")
            and secret_scan.get("all_clear")
            and basis_consistency.get("all_ok")
        ),
        "validation_reports": validation,
        "csv_outputs": csv_outputs,
        "fetcher_script_audit": script_audit,
        "secret_scan": secret_scan,
        "basis_consistency_audit": basis_consistency,
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
