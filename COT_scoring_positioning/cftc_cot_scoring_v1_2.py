#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
CFTC COT Step 6 Scoring v1.2

Purpose
-------
Build COT scoring from the validated CFTC v4 normalized positioning data.

This version updates the Legacy COT logic:

v1.1:
    final score = 2.0*MM + 1.5*PM + 1.0*Impulse + 0.5*Legacy

v1.2:
    base score = 2.0*MM + 1.5*PM + 1.0*Impulse
    Legacy COT is NOT added into the score.
    Legacy COT is used only as a confirmation / gate.

Reason
------
Disaggregated COT is the more detailed version of Legacy COT. Since the main score
already uses Disaggregated categories:
    - Managed Money
    - Producer / Merchant / Processor / User
adding Legacy Commercial / Noncommercial as another weighted alpha component risks
double counting.

Therefore:
    - Disaggregated COT builds cot_score_base.
    - Legacy COT confirms, stays neutral, or rejects the base score.
    - If strong base direction conflicts with Legacy direction, final score is set to 0.

Input default:
    C:\Users\<YOU>\cftc_output\step6_cot_light_v4\combined\cftc_step6_cot_positioning_normalized.jsonl

Output default:
    C:\Users\<YOU>\cftc_output\step6_cot_scoring_v1_2

Run:
    python cftc_cot_scoring_v1_2.py

Optional:
    python cftc_cot_scoring_v1_2.py --input-jsonl C:\Users\78432\cftc_output\step6_cot_light_v4\combined\cftc_step6_cot_positioning_normalized.jsonl

Scoring dimensions
------------------
1. Managed Money Crowding Score
2. Producer / Merchant Hedge Pressure Score
3. Positioning Impulse Score
4. Legacy Confirmation Gate, not a weighted score component

Base score formula
------------------
cot_score_base =
    2.0 * managed_money_crowding_score
  + 1.5 * producer_merchant_pressure_score
  + 1.0 * positioning_impulse_score

cot_score_base = clamp(cot_score_base, -5, +5)

Legacy gate
-----------
base_direction:
    bullish if cot_score_base >= +1
    bearish if cot_score_base <= -1
    neutral otherwise

legacy_direction:
    bullish / bearish / neutral, derived from Legacy Noncommercial and Commercial percentiles.

legacy_confirmation_status:
    CONFIRMED          -> base direction and legacy direction agree
    NEUTRAL            -> legacy direction is neutral, base score accepted but unconfirmed
    CONFLICT           -> base direction and legacy direction conflict
    NOT_APPLICABLE     -> base direction is neutral

Final score:
    if strong conflict and abs(cot_score_base) >= conflict_threshold:
        cot_score = 0
        cot_signal = neutral
        score_status = REJECTED_BY_LEGACY_CONFLICT
    else:
        cot_score = cot_score_base
        score_status = ACCEPTED_...

Default conflict_threshold = 3.0

Outputs
-------
combined/
    cftc_step6_cot_scores.json
    cftc_step6_cot_scores.jsonl
    cftc_step6_cot_scores.csv
    cftc_step6_cot_scores_latest.json
    cftc_step6_cot_scores_latest.csv
    cftc_step6_cot_scoring_validation_report.json
    cftc_step6_cot_scoring_validation_report.txt

commodities/<SYMBOL>/
    cftc_step6_<SYMBOL>_cot_scores.json
    cftc_step6_<SYMBOL>_cot_scores.jsonl
    cftc_step6_<SYMBOL>_cot_scores.csv
    cftc_step6_<SYMBOL>_cot_score_latest.json
    cftc_step6_<SYMBOL>_cot_scoring_validation_report.json
"""

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TARGETS = ["CL", "NG", "ZC", "ZS", "ZW", "GC", "SI"]
DISAGG_REPORT = "DISAGGREGATED_FUTURES_ONLY"
LEGACY_REPORT = "LEGACY_FUTURES_ONLY"

SIGNALS = {
    "bullish_positioning",
    "mildly_bullish",
    "neutral",
    "mildly_bearish",
    "bearish_positioning",
    "insufficient_data",
}

BASE_DIRECTIONS = {"bullish", "bearish", "neutral"}
LEGACY_DIRECTIONS = {"bullish", "bearish", "neutral", "unknown"}
LEGACY_STATUSES = {"CONFIRMED", "NEUTRAL", "CONFLICT", "NOT_APPLICABLE", "UNKNOWN"}

REQUIRED_DISAGG_FIELDS = [
    "producer_merchant_long",
    "producer_merchant_short",
    "producer_merchant_net",
    "managed_money_long",
    "managed_money_short",
    "managed_money_net",
    "open_interest",
    "producer_merchant_net_pct_oi",
    "managed_money_net_pct_oi",
    "producer_merchant_net_chg_1w",
    "managed_money_net_chg_1w",
    "producer_merchant_net_percentile",
    "managed_money_net_percentile",
    "producer_merchant_net_zscore",
    "managed_money_net_zscore",
]

REQUIRED_LEGACY_FIELDS = [
    "commercial_net",
    "noncommercial_net",
    "legacy_commercial_net_percentile",
    "legacy_noncommercial_net_percentile",
]

COMPONENT_BOUNDS = {
    "managed_money_crowding_score": (-2, 2),
    "producer_merchant_pressure_score": (-2, 2),
    "positioning_impulse_score": (-1, 1),
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(x: Any) -> datetime:
    if x is None:
        return datetime.min
    s = str(x).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
        try:
            if fmt == "%Y-%m-%d":
                return datetime.strptime(s[:10], fmt)
            return datetime.strptime(s[:19], fmt)
        except Exception:
            pass
    return datetime.min


def to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, bool):
            return None
        s = str(x).strip().replace(",", "")
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
    return rows


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    preferred = [
        "instrument", "commodity_label", "report_date",
        "cot_score_base", "cot_score", "cot_signal", "confidence", "score_status",
        "base_direction", "legacy_direction", "legacy_confirmation_status",
        "managed_money_crowding_score",
        "producer_merchant_pressure_score",
        "positioning_impulse_score",
        "managed_money_net", "managed_money_net_pct_oi",
        "managed_money_net_percentile", "managed_money_net_zscore",
        "producer_merchant_net", "producer_merchant_net_pct_oi",
        "producer_merchant_net_percentile", "producer_merchant_net_zscore",
        "managed_money_net_chg_1w", "producer_merchant_net_chg_1w",
        "legacy_noncommercial_net_percentile", "legacy_commercial_net_percentile",
        "summary", "source_mapping_status", "market_and_exchange_name",
    ]

    keys = []
    for k in preferred:
        if any(k in row for row in rows):
            keys.append(k)
    for row in rows:
        for k in row.keys():
            if k not in keys:
                keys.append(k)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            out = {}
            for k in keys:
                v = row.get(k)
                out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            writer.writerow(out)


def write_txt_validation(path: Path, report: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    lines = []
    lines.append("CFTC COT Scoring v1.2 Validation Report")
    lines.append("=" * 88)
    lines.append(f"Validated at: {report['validated_at']}")
    lines.append(f"All scoring valid: {report['all_scoring_valid']}")
    lines.append(f"Total score records: {report['total_score_records']}")
    lines.append("")
    lines.append("Scoring formula:")
    lines.append("  cot_score_base = clamp(2.0*MM + 1.5*PM + 1.0*Impulse, -5, +5)")
    lines.append("  Legacy is not weighted into cot_score_base; it is used as confirmation/gate.")
    lines.append("")
    for g in report["commodity_results"]:
        status = "PASS" if g["ok"] else "FAIL"
        lines.append(
            f"{status} | {g['instrument']:<2} | records={g['score_record_count']:4d} "
            f"| latest={g['latest_report_date']} | base={g['latest_cot_score_base']} "
            f"| final={g['latest_cot_score']} | signal={g['latest_cot_signal']} "
            f"| legacy={g['latest_legacy_confirmation_status']} | confidence={g['latest_confidence']}"
        )
        if g["errors"]:
            lines.append(f"      errors={g['errors']}")
        if g["warnings"]:
            lines.append(f"      warnings={g['warnings']}")
    path.write_text("\n".join(lines), encoding="utf-8")


def group_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for rec in records:
        symbol = rec.get("instrument")
        report_type = rec.get("report_type")
        if symbol not in TARGETS:
            continue
        grouped.setdefault(symbol, {DISAGG_REPORT: [], LEGACY_REPORT: []})
        if report_type in {DISAGG_REPORT, LEGACY_REPORT}:
            grouped[symbol][report_type].append(rec)

    for symbol in grouped:
        for report_type in grouped[symbol]:
            grouped[symbol][report_type].sort(key=lambda r: parse_date(r.get("report_date")))
    return grouped


def score_managed_money_crowding(p: Optional[float], z: Optional[float], pct_oi: Optional[float]) -> Tuple[int, str, List[str]]:
    drivers = []
    if p is None and z is None:
        return 0, "Managed Money crowding unavailable.", ["Managed Money percentile/z-score missing"]

    if (p is not None and p >= 0.90) or (z is not None and z >= 1.50):
        score = -2
        drivers.append("Managed Money is extremely net long, indicating crowded-long downside risk.")
    elif (p is not None and p >= 0.75) or (z is not None and z >= 0.75):
        score = -1
        drivers.append("Managed Money is moderately net long versus history.")
    elif (p is not None and p <= 0.10) or (z is not None and z <= -1.50):
        score = +2
        drivers.append("Managed Money is extremely net short, indicating short-covering upside risk.")
    elif (p is not None and p <= 0.25) or (z is not None and z <= -0.75):
        score = +1
        drivers.append("Managed Money is moderately net short versus history.")
    else:
        score = 0
        drivers.append("Managed Money positioning is near its historical middle range.")

    if pct_oi is not None:
        drivers.append(f"Managed Money net/open-interest ratio is {pct_oi:.3f}.")
    return score, drivers[0], drivers


def score_producer_merchant_pressure(p: Optional[float], z: Optional[float], pct_oi: Optional[float]) -> Tuple[int, str, List[str]]:
    drivers = []
    if p is None and z is None:
        return 0, "Producer/Merchant pressure unavailable.", ["Producer/Merchant percentile/z-score missing"]

    if (p is not None and p <= 0.10) or (z is not None and z <= -1.50):
        score = -2
        drivers.append("Producer/Merchant net position is extremely low, indicating elevated hedge-selling pressure.")
    elif (p is not None and p <= 0.25) or (z is not None and z <= -0.75):
        score = -1
        drivers.append("Producer/Merchant net position is below normal, suggesting moderate hedge pressure.")
    elif (p is not None and p >= 0.90) or (z is not None and z >= 1.50):
        score = +2
        drivers.append("Producer/Merchant net position is unusually high, indicating reduced hedge pressure or commercial support.")
    elif (p is not None and p >= 0.75) or (z is not None and z >= 0.75):
        score = +1
        drivers.append("Producer/Merchant net position is above normal, suggesting easing commercial pressure.")
    else:
        score = 0
        drivers.append("Producer/Merchant positioning is near its historical middle range.")

    if pct_oi is not None:
        drivers.append(f"Producer/Merchant net/open-interest ratio is {pct_oi:.3f}.")
    return score, drivers[0], drivers


def score_positioning_impulse(
    mm_chg: Optional[float],
    pm_chg: Optional[float],
    mm_p: Optional[float]
) -> Tuple[int, str, List[str]]:
    drivers = []
    if mm_chg is None or pm_chg is None:
        return 0, "Positioning impulse is neutral because 1-week change data is missing.", ["1-week change unavailable"]

    if mm_chg > 0 and pm_chg < 0:
        if mm_p is not None and mm_p <= 0.25:
            score = +1
            drivers.append("Managed Money net increased from a low percentile, suggesting short-covering impulse.")
        else:
            score = -1
            drivers.append("Managed Money added length while Producer/Merchant net fell, reinforcing crowded-long/hedge-pressure risk.")
    elif mm_chg < 0 and pm_chg > 0:
        if mm_p is not None and mm_p >= 0.75:
            score = -1
            drivers.append("Managed Money net fell from a high percentile, suggesting long-liquidation risk.")
        else:
            score = +1
            drivers.append("Managed Money net fell while Producer/Merchant pressure eased, reducing bearish hedge pressure.")
    elif mm_chg > 0 and pm_chg > 0:
        score = +1
        drivers.append("Both Managed Money and Producer/Merchant net positions increased, indicating bullish positioning impulse.")
    elif mm_chg < 0 and pm_chg < 0:
        score = -1
        drivers.append("Both Managed Money and Producer/Merchant net positions fell, indicating bearish positioning impulse.")
    else:
        score = 0
        drivers.append("1-week positioning changes are mixed or flat.")

    drivers.append(f"Managed Money net change 1w = {mm_chg:.0f}.")
    drivers.append(f"Producer/Merchant net change 1w = {pm_chg:.0f}.")
    return score, drivers[0], drivers


def base_direction_from_score(score: float) -> str:
    if score >= 1:
        return "bullish"
    if score <= -1:
        return "bearish"
    return "neutral"


def legacy_direction_from_percentiles(noncomm_p: Optional[float], comm_p: Optional[float]) -> Tuple[str, str, List[str]]:
    """
    Convert Legacy COT percentiles into a direction used only for confirmation.

    Bullish legacy evidence:
      - Noncommercial crowded short: noncomm percentile <= 0.15
      - Commercial supportive / reduced pressure: commercial percentile >= 0.85

    Bearish legacy evidence:
      - Noncommercial crowded long: noncomm percentile >= 0.85
      - Commercial strong short pressure: commercial percentile <= 0.15

    If evidence is balanced or not extreme, return neutral.
    """
    drivers = []
    bullish_votes = 0
    bearish_votes = 0

    if noncomm_p is None and comm_p is None:
        return "unknown", "Legacy direction cannot be assessed because both confirmation percentiles are missing.", ["Legacy percentiles missing"]

    if noncomm_p is not None:
        if noncomm_p <= 0.15:
            bullish_votes += 1
            drivers.append("Legacy Noncommercial positioning is crowded short.")
        elif noncomm_p >= 0.85:
            bearish_votes += 1
            drivers.append("Legacy Noncommercial positioning is crowded long.")
        else:
            drivers.append("Legacy Noncommercial positioning is not extreme.")
    else:
        drivers.append("Legacy Noncommercial percentile is missing.")

    if comm_p is not None:
        if comm_p >= 0.85:
            bullish_votes += 1
            drivers.append("Legacy Commercial positioning suggests reduced commercial pressure.")
        elif comm_p <= 0.15:
            bearish_votes += 1
            drivers.append("Legacy Commercial positioning suggests strong commercial short pressure.")
        else:
            drivers.append("Legacy Commercial positioning is not extreme.")
    else:
        drivers.append("Legacy Commercial percentile is missing.")

    if bullish_votes > bearish_votes:
        direction = "bullish"
        interpretation = "Legacy COT confirms bullish positioning pressure."
    elif bearish_votes > bullish_votes:
        direction = "bearish"
        interpretation = "Legacy COT confirms bearish positioning pressure."
    else:
        direction = "neutral"
        interpretation = "Legacy COT is neutral or mixed."

    return direction, interpretation, drivers


def apply_legacy_gate(
    base_score: float,
    base_direction: str,
    legacy_direction: str,
    conflict_threshold: float,
) -> Tuple[float, str, str, str, str]:
    """
    Returns:
      final_score, final_signal, legacy_confirmation_status, score_status, confidence_modifier
    """
    if base_direction == "neutral":
        return base_score, signal_from_score(base_score), "NOT_APPLICABLE", "ACCEPTED_BASE_NEUTRAL", "low"

    if legacy_direction == "unknown":
        return base_score, signal_from_score(base_score), "UNKNOWN", "ACCEPTED_LEGACY_UNKNOWN", "low"

    if legacy_direction == "neutral":
        return base_score, signal_from_score(base_score), "NEUTRAL", "ACCEPTED_LEGACY_NEUTRAL", "medium"

    if legacy_direction == base_direction:
        return base_score, signal_from_score(base_score), "CONFIRMED", "ACCEPTED_LEGACY_CONFIRMED", "high"

    # Opposite direction.
    if abs(base_score) >= conflict_threshold:
        return 0.0, "neutral", "CONFLICT", "REJECTED_BY_LEGACY_CONFLICT", "low"

    # Mild base signal conflict: keep score, but lower confidence.
    return base_score, signal_from_score(base_score), "CONFLICT", "ACCEPTED_WEAK_SIGNAL_LEGACY_CONFLICT", "low"


def signal_from_score(score: Optional[float]) -> str:
    if score is None:
        return "insufficient_data"
    if score >= 3:
        return "bullish_positioning"
    if score >= 1:
        return "mildly_bullish"
    if score <= -3:
        return "bearish_positioning"
    if score <= -1:
        return "mildly_bearish"
    return "neutral"


def confidence_from_gate(
    final_score: float,
    missing_fields: List[str],
    confidence_modifier: str,
    score_status: str,
) -> str:
    if missing_fields:
        return "low"
    if score_status == "REJECTED_BY_LEGACY_CONFLICT":
        return "low"
    if confidence_modifier == "high" and abs(final_score) >= 3:
        return "high"
    if confidence_modifier in {"high", "medium"} and abs(final_score) >= 1:
        return "medium"
    return "low"


def summarize_signal(symbol: str, signal: str, score: float, base_score: float, legacy_status: str, drivers: List[str]) -> str:
    labels = {
        "bullish_positioning": "bullish COT pressure",
        "mildly_bullish": "mild bullish COT pressure",
        "bearish_positioning": "bearish COT pressure",
        "mildly_bearish": "mild bearish COT pressure",
        "neutral": "neutral COT pressure",
    }
    direction = labels.get(signal, "insufficient COT data")
    top_driver = drivers[0] if drivers else "Positioning conditions are balanced."

    if legacy_status == "CONFLICT" and score == 0:
        return (
            f"{symbol} base COT score was {base_score:.2f}, but Legacy confirmation conflicted; "
            f"final score is neutralized. {top_driver}"
        )

    return f"{symbol} shows {direction} with final score {score:.2f} and base score {base_score:.2f}. {top_driver}"


def score_one_record(disagg: Dict[str, Any], legacy: Optional[Dict[str, Any]], conflict_threshold: float) -> Dict[str, Any]:
    missing = []
    for field in REQUIRED_DISAGG_FIELDS:
        if to_float(disagg.get(field)) is None:
            missing.append(field)

    if legacy is None:
        missing.append("legacy_record")
    else:
        for field in REQUIRED_LEGACY_FIELDS:
            if to_float(legacy.get(field)) is None:
                missing.append(f"legacy_{field}")

    symbol = disagg.get("instrument")

    mm_p = to_float(disagg.get("managed_money_net_percentile"))
    mm_z = to_float(disagg.get("managed_money_net_zscore"))
    mm_pct = to_float(disagg.get("managed_money_net_pct_oi"))
    mm_chg = to_float(disagg.get("managed_money_net_chg_1w"))

    pm_p = to_float(disagg.get("producer_merchant_net_percentile"))
    pm_z = to_float(disagg.get("producer_merchant_net_zscore"))
    pm_pct = to_float(disagg.get("producer_merchant_net_pct_oi"))
    pm_chg = to_float(disagg.get("producer_merchant_net_chg_1w"))

    legacy_noncomm_p = to_float(legacy.get("legacy_noncommercial_net_percentile")) if legacy else None
    legacy_comm_p = to_float(legacy.get("legacy_commercial_net_percentile")) if legacy else None

    mm_score, mm_interp, mm_drivers = score_managed_money_crowding(mm_p, mm_z, mm_pct)
    pm_score, pm_interp, pm_drivers = score_producer_merchant_pressure(pm_p, pm_z, pm_pct)
    impulse_score, impulse_interp, impulse_drivers = score_positioning_impulse(mm_chg, pm_chg, mm_p)

    base_raw = 2.0 * mm_score + 1.5 * pm_score + 1.0 * impulse_score
    cot_score_base = round(clamp(base_raw, -5.0, 5.0), 4)
    base_direction = base_direction_from_score(cot_score_base)

    legacy_direction, legacy_interp, legacy_drivers = legacy_direction_from_percentiles(legacy_noncomm_p, legacy_comm_p)

    final_score, final_signal, legacy_status, score_status, confidence_modifier = apply_legacy_gate(
        cot_score_base,
        base_direction,
        legacy_direction,
        conflict_threshold,
    )
    final_score = round(final_score, 4)

    # If essential fields are missing, keep the numerical outcome but mark as partial.
    if missing and score_status != "REJECTED_BY_LEGACY_CONFLICT":
        score_status = "PARTIAL_DATA"

    confidence = confidence_from_gate(final_score, missing, confidence_modifier, score_status)

    drivers = []
    for group in [mm_drivers, pm_drivers, impulse_drivers, legacy_drivers]:
        for d in group:
            if d not in drivers:
                drivers.append(d)

    return {
        "source_id": "CFTC_COT_SCORING_V1_2",
        "instrument": symbol,
        "commodity_label": disagg.get("commodity_label"),
        "asset_class": disagg.get("asset_class"),
        "report_date": disagg.get("report_date"),

        "cot_score_base": cot_score_base,
        "cot_score_base_raw": round(base_raw, 4),
        "cot_score": final_score,
        "cot_signal": final_signal,
        "confidence": confidence,
        "score_status": score_status,

        "base_direction": base_direction,
        "legacy_direction": legacy_direction,
        "legacy_confirmation_status": legacy_status,
        "legacy_confirmation_interpretation": legacy_interp,
        "legacy_conflict_threshold": conflict_threshold,

        "managed_money_crowding_score": mm_score,
        "producer_merchant_pressure_score": pm_score,
        "positioning_impulse_score": impulse_score,

        # Kept for audit, but no longer used as weighted score component.
        "legacy_confirmation_score": None,
        "legacy_used_as": "GATE_NOT_WEIGHTED_COMPONENT",

        "managed_money_net": to_float(disagg.get("managed_money_net")),
        "managed_money_long": to_float(disagg.get("managed_money_long")),
        "managed_money_short": to_float(disagg.get("managed_money_short")),
        "managed_money_net_pct_oi": mm_pct,
        "managed_money_net_chg_1w": mm_chg,
        "managed_money_net_percentile": mm_p,
        "managed_money_net_zscore": mm_z,

        "producer_merchant_net": to_float(disagg.get("producer_merchant_net")),
        "producer_merchant_long": to_float(disagg.get("producer_merchant_long")),
        "producer_merchant_short": to_float(disagg.get("producer_merchant_short")),
        "producer_merchant_net_pct_oi": pm_pct,
        "producer_merchant_net_chg_1w": pm_chg,
        "producer_merchant_net_percentile": pm_p,
        "producer_merchant_net_zscore": pm_z,

        "open_interest": to_float(disagg.get("open_interest")),

        "legacy_commercial_net": to_float(legacy.get("commercial_net")) if legacy else None,
        "legacy_noncommercial_net": to_float(legacy.get("noncommercial_net")) if legacy else None,
        "legacy_commercial_net_percentile": legacy_comm_p,
        "legacy_noncommercial_net_percentile": legacy_noncomm_p,

        "managed_money": {
            "net": to_float(disagg.get("managed_money_net")),
            "net_pct_oi": mm_pct,
            "percentile": mm_p,
            "zscore": mm_z,
            "score": mm_score,
            "interpretation": mm_interp,
        },
        "producer_merchant": {
            "net": to_float(disagg.get("producer_merchant_net")),
            "net_pct_oi": pm_pct,
            "percentile": pm_p,
            "zscore": pm_z,
            "score": pm_score,
            "interpretation": pm_interp,
        },
        "positioning_impulse": {
            "managed_money_net_chg_1w": mm_chg,
            "producer_merchant_net_chg_1w": pm_chg,
            "score": impulse_score,
            "interpretation": impulse_interp,
        },
        "legacy_confirmation": {
            "commercial_net_percentile": legacy_comm_p,
            "noncommercial_net_percentile": legacy_noncomm_p,
            "direction": legacy_direction,
            "status": legacy_status,
            "interpretation": legacy_interp,
            "used_as": "GATE_NOT_WEIGHTED_COMPONENT",
        },

        "drivers": drivers[:10],
        "summary": summarize_signal(symbol, final_signal, final_score, cot_score_base, legacy_status, drivers),
        "missing_fields": missing,

        "market_and_exchange_name": disagg.get("market_and_exchange_name"),
        "legacy_market_and_exchange_name": legacy.get("market_and_exchange_name") if legacy else None,
        "source_mapping_status": disagg.get("source_mapping_status", "STRICT"),
        "source_mapping_note": disagg.get("source_mapping_note"),
        "raw_disagg_source_url": disagg.get("raw_source_url"),
        "raw_legacy_source_url": legacy.get("raw_source_url") if legacy else None,
        "scored_at": now_utc(),
    }


def build_scores(records: List[Dict[str, Any]], conflict_threshold: float) -> List[Dict[str, Any]]:
    grouped = group_records(records)
    scored = []

    for symbol in TARGETS:
        disagg_rows = grouped.get(symbol, {}).get(DISAGG_REPORT, [])
        legacy_rows = grouped.get(symbol, {}).get(LEGACY_REPORT, [])
        legacy_by_date = {r.get("report_date"): r for r in legacy_rows}

        for disagg in disagg_rows:
            scored.append(score_one_record(disagg, legacy_by_date.get(disagg.get("report_date")), conflict_threshold))

    scored.sort(key=lambda r: (r.get("instrument", ""), parse_date(r.get("report_date"))))
    return scored


def latest_scores(scored_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest = []
    for symbol in TARGETS:
        group = [r for r in scored_rows if r.get("instrument") == symbol]
        if group:
            latest.append(max(group, key=lambda r: parse_date(r.get("report_date"))))
    latest.sort(key=lambda r: r.get("instrument", ""))
    return latest


def validate_scores(scored_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    commodity_results = []

    for symbol in TARGETS:
        group = [r for r in scored_rows if r.get("instrument") == symbol]
        errors = []
        warnings = []
        latest = max(group, key=lambda r: parse_date(r.get("report_date"))) if group else None

        if not group:
            errors.append("No score records produced.")

        for row in group:
            score = to_float(row.get("cot_score"))
            base_score = to_float(row.get("cot_score_base"))

            if score is None or score < -5 or score > 5:
                errors.append(f"cot_score out of range or missing at {row.get('report_date')}: {score}")
                break
            if base_score is None or base_score < -5 or base_score > 5:
                errors.append(f"cot_score_base out of range or missing at {row.get('report_date')}: {base_score}")
                break
            if row.get("cot_signal") not in SIGNALS:
                errors.append(f"Invalid cot_signal at {row.get('report_date')}: {row.get('cot_signal')}")
                break
            if row.get("base_direction") not in BASE_DIRECTIONS:
                errors.append(f"Invalid base_direction at {row.get('report_date')}: {row.get('base_direction')}")
                break
            if row.get("legacy_direction") not in LEGACY_DIRECTIONS:
                errors.append(f"Invalid legacy_direction at {row.get('report_date')}: {row.get('legacy_direction')}")
                break
            if row.get("legacy_confirmation_status") not in LEGACY_STATUSES:
                errors.append(f"Invalid legacy_confirmation_status at {row.get('report_date')}: {row.get('legacy_confirmation_status')}")
                break

            for field, (lo, hi) in COMPONENT_BOUNDS.items():
                val = to_float(row.get(field))
                if val is None or val < lo or val > hi:
                    errors.append(f"{field} out of range at {row.get('report_date')}: {val}")
                    break

            if row.get("legacy_confirmation_score") is not None:
                errors.append("legacy_confirmation_score must be None in v1.2 because Legacy is a gate, not a weighted component.")
                break

            # If strong conflict rejected, final score must be exactly neutralized.
            if row.get("score_status") == "REJECTED_BY_LEGACY_CONFLICT":
                if to_float(row.get("cot_score")) != 0:
                    errors.append(f"Rejected legacy conflict did not neutralize score at {row.get('report_date')}.")
                    break
                if row.get("cot_signal") != "neutral":
                    errors.append(f"Rejected legacy conflict must have neutral signal at {row.get('report_date')}.")
                    break

        # Strict latest-row validation inherited from v1.1.
        if latest and latest.get("score_status") == "PARTIAL_DATA":
            errors.append(
                f"Latest score_status must not be PARTIAL_DATA; missing_fields={latest.get('missing_fields')}"
            )
        if latest and to_float(latest.get("managed_money_net_zscore")) is None:
            errors.append("Latest managed_money_net_zscore must be non-null and numeric.")
        if latest and to_float(latest.get("producer_merchant_net_zscore")) is None:
            errors.append("Latest producer_merchant_net_zscore must be non-null and numeric.")

        if latest and latest.get("confidence") == "low":
            warnings.append("Latest confidence is low.")

        commodity_results.append({
            "instrument": symbol,
            "ok": len(errors) == 0,
            "score_record_count": len(group),
            "latest_report_date": latest.get("report_date") if latest else None,
            "latest_cot_score_base": latest.get("cot_score_base") if latest else None,
            "latest_cot_score": latest.get("cot_score") if latest else None,
            "latest_cot_signal": latest.get("cot_signal") if latest else None,
            "latest_confidence": latest.get("confidence") if latest else None,
            "latest_base_direction": latest.get("base_direction") if latest else None,
            "latest_legacy_direction": latest.get("legacy_direction") if latest else None,
            "latest_legacy_confirmation_status": latest.get("legacy_confirmation_status") if latest else None,
            "latest_score_status": latest.get("score_status") if latest else None,
            "errors": errors,
            "warnings": warnings,
        })

    return {
        "validated_at": now_utc(),
        "stage": "CFTC_COT_SCORING_V1_2_VALIDATION",
        "all_scoring_valid": all(g["ok"] for g in commodity_results),
        "total_score_records": len(scored_rows),
        "target_symbols": TARGETS,
        "score_range": [-5, 5],
        "score_formula": "cot_score_base = clamp(2.0*MM + 1.5*PM + 1.0*Impulse, -5, +5); Legacy is gate only.",
        "legacy_policy": "Legacy COT is not weighted into the score. It confirms, remains neutral, or rejects strong conflicting base signals.",
        "signal_enum": sorted(SIGNALS),
        "base_direction_enum": sorted(BASE_DIRECTIONS),
        "legacy_direction_enum": sorted(LEGACY_DIRECTIONS),
        "legacy_status_enum": sorted(LEGACY_STATUSES),
        "commodity_results": commodity_results,
    }


def write_symbol_outputs(out_dir: Path, symbol: str, rows: List[Dict[str, Any]], validation_group: Dict[str, Any]) -> Dict[str, str]:
    sdir = out_dir / "commodities" / symbol
    ensure_dir(sdir)

    json_path = sdir / f"cftc_step6_{symbol}_cot_scores.json"
    jsonl_path = sdir / f"cftc_step6_{symbol}_cot_scores.jsonl"
    csv_path = sdir / f"cftc_step6_{symbol}_cot_scores.csv"
    latest_path = sdir / f"cftc_step6_{symbol}_cot_score_latest.json"
    validation_path = sdir / f"cftc_step6_{symbol}_cot_scoring_validation_report.json"

    latest = max(rows, key=lambda r: parse_date(r.get("report_date"))) if rows else None

    save_json(json_path, {
        "generated_at": now_utc(),
        "source": "CFTC_COT_SCORING_V1_2",
        "instrument": symbol,
        "records": rows,
        "latest": latest,
    })
    write_jsonl(jsonl_path, rows)
    write_csv(csv_path, rows)
    save_json(latest_path, latest or {})
    save_json(validation_path, validation_group)

    return {
        "json": str(json_path),
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
        "latest_json": str(latest_path),
        "validation_json": str(validation_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-jsonl",
        default=str(Path.home() / "cftc_output" / "step6_cot_light_v4" / "combined" / "cftc_step6_cot_positioning_normalized.jsonl"),
        help="Path to v4 normalized CFTC COT JSONL."
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path.home() / "cftc_output" / "step6_cot_scoring_v1_2"),
        help="Output directory for COT scoring v1.2."
    )
    parser.add_argument(
        "--conflict-threshold",
        type=float,
        default=3.0,
        help="If abs(cot_score_base) is at least this threshold and Legacy direction conflicts, set final score to 0."
    )
    args = parser.parse_args()

    input_jsonl = Path(args.input_jsonl)
    out_dir = Path(args.out_dir)
    combined_dir = out_dir / "combined"
    ensure_dir(combined_dir)

    print("=" * 88)
    print("CFTC COT Step 6 Scoring v1.2")
    print("=" * 88)
    print(f"Input JSONL:        {input_jsonl}")
    print(f"Output directory:   {out_dir}")
    print(f"Conflict threshold: {args.conflict_threshold}")

    if not input_jsonl.exists():
        print(f"[ERROR] Input file does not exist: {input_jsonl}")
        return 2

    raw_records = read_jsonl(input_jsonl)
    scored_rows = build_scores(raw_records, args.conflict_threshold)
    latest_rows = latest_scores(scored_rows)
    validation = validate_scores(scored_rows)
    validation_by_symbol = {g["instrument"]: g for g in validation["commodity_results"]}

    per_symbol_outputs = {}
    for symbol in TARGETS:
        rows = [r for r in scored_rows if r.get("instrument") == symbol]
        per_symbol_outputs[symbol] = write_symbol_outputs(out_dir, symbol, rows, validation_by_symbol[symbol])

    combined_json = combined_dir / "cftc_step6_cot_scores.json"
    combined_jsonl = combined_dir / "cftc_step6_cot_scores.jsonl"
    combined_csv = combined_dir / "cftc_step6_cot_scores.csv"
    latest_json = combined_dir / "cftc_step6_cot_scores_latest.json"
    latest_csv = combined_dir / "cftc_step6_cot_scores_latest.csv"
    validation_json = combined_dir / "cftc_step6_cot_scoring_validation_report.json"
    validation_txt = combined_dir / "cftc_step6_cot_scoring_validation_report.txt"

    save_json(combined_json, {
        "generated_at": now_utc(),
        "source": "CFTC_COT_SCORING_V1_2",
        "stage": "step6_cot_scoring_v1_2",
        "input_jsonl": str(input_jsonl),
        "score_formula": "cot_score_base = clamp(2.0*MM + 1.5*PM + 1.0*Impulse, -5, +5)",
        "legacy_policy": "Legacy COT is used as a confirmation/gate, not a weighted score component.",
        "legacy_conflict_rule": f"If abs(cot_score_base) >= {args.conflict_threshold} and Legacy direction conflicts, cot_score is set to 0.",
        "sign_convention": "Positive = bullish positioning pressure; negative = bearish positioning pressure.",
        "records": scored_rows,
        "latest": latest_rows,
        "per_symbol_outputs": per_symbol_outputs,
    })
    write_jsonl(combined_jsonl, scored_rows)
    write_csv(combined_csv, scored_rows)
    save_json(latest_json, {"generated_at": now_utc(), "latest": latest_rows})
    write_csv(latest_csv, latest_rows)
    save_json(validation_json, validation)
    write_txt_validation(validation_txt, validation)

    print("\n" + "=" * 88)
    print("CFTC COT SCORING v1.2 SUMMARY")
    print("=" * 88)
    for g in validation["commodity_results"]:
        status = "PASS" if g["ok"] else "FAIL"
        print(
            f"{status} | {g['instrument']:<2} | records={g['score_record_count']:4d} "
            f"| latest={g['latest_report_date']} | base={g['latest_cot_score_base']} "
            f"| final={g['latest_cot_score']} | signal={g['latest_cot_signal']} "
            f"| legacy={g['latest_legacy_confirmation_status']} | confidence={g['latest_confidence']}"
        )
        if g["errors"]:
            print(f"      errors={g['errors']}")
        if g["warnings"]:
            print(f"      warnings={g['warnings']}")

    print("\nSaved combined outputs:")
    print(combined_json)
    print(combined_jsonl)
    print(combined_csv)
    print(latest_json)
    print(latest_csv)
    print(validation_json)
    print(validation_txt)
    print("\nSaved per-commodity score files under:")
    print(out_dir / "commodities")

    if validation["all_scoring_valid"]:
        print("\n[DONE] CFTC COT scoring v1.2 completed and validated.")
        return 0

    print("\n[DONE WITH WARNINGS/ERRORS] Review scoring validation report.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
