#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Massive CME futures fetcher and normalizer for Metrixx Basis Sentiment.

This script fills the CME futures leg that the checked-in Basis Sentiment
samples previously represented only as static data. It discovers active
front contracts through Massive Futures Contracts, fetches 1-session aggregate
bars, normalizes settlement prices, and writes reproducible validation outputs.

Authentication:
    set MASSIVE_API_KEY=YOUR_KEY

Run:
    python Data_light_version/CME/massive_cme_futures_fetcher.py
    python Data_light_version/CME/massive_cme_futures_fetcher.py --as-of-date 2026-06-15 --history-days 5

Outputs:
    Data_light_version/CME/raw_massive_cme_futures.json
    Data_light_version/CME/massive_cme_futures_normalized.json
    Data_light_version/CME/massive_cme_futures_normalized.csv
    Data_light_version/CME/massive_cme_futures_validation_report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import requests
except ImportError as exc:  # pragma: no cover
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise exc


DEFAULT_BASE_URL = "https://api.massive.com"
DEFAULT_GATEKEEPER_ID = "LOCAL_PROTO"
DEFAULT_TARGETS = ["CL", "NG", "ZC", "ZS", "ZW", "GC", "SI"]


TARGET_CONFIG: Dict[str, Dict[str, Any]] = {
    "CL": {
        "product_code": "CL",
        "instrument": "WTI_LIGHT_SWEET_CRUDE_OIL",
        "commodity_label": "WTI Light Sweet Crude Oil",
        "exchange": "NYMEX",
        "asset_class": "ENERGY",
        "price_unit": "USD_PER_BBL",
        "common_unit": "USD_PER_BBL",
        "unit_conversion": "no_conversion_needed",
    },
    "NG": {
        "product_code": "NG",
        "instrument": "HENRY_HUB_NATURAL_GAS",
        "commodity_label": "Henry Hub Natural Gas",
        "exchange": "NYMEX",
        "asset_class": "ENERGY",
        "price_unit": "USD_PER_MMBTU",
        "common_unit": "USD_PER_MMBTU",
        "unit_conversion": "no_conversion_needed",
    },
    "ZC": {
        "product_code": "ZC",
        "instrument": "CORN",
        "commodity_label": "Corn",
        "exchange": "CBOT",
        "asset_class": "GRAIN",
        "price_unit": "US_CENTS_PER_BUSHEL",
        "common_unit": "USD_PER_BUSHEL",
        "unit_conversion": "settlement_price_cents_per_bushel / 100",
    },
    "ZS": {
        "product_code": "ZS",
        "instrument": "SOYBEANS",
        "commodity_label": "Soybeans",
        "exchange": "CBOT",
        "asset_class": "GRAIN",
        "price_unit": "US_CENTS_PER_BUSHEL",
        "common_unit": "USD_PER_BUSHEL",
        "unit_conversion": "settlement_price_cents_per_bushel / 100",
    },
    "ZW": {
        "product_code": "ZW",
        "instrument": "SRW_WHEAT",
        "commodity_label": "SRW Wheat",
        "exchange": "CBOT",
        "asset_class": "GRAIN",
        "price_unit": "US_CENTS_PER_BUSHEL",
        "common_unit": "USD_PER_BUSHEL",
        "unit_conversion": "settlement_price_cents_per_bushel / 100",
    },
    "GC": {
        "product_code": "GC",
        "instrument": "GOLD",
        "commodity_label": "Gold",
        "exchange": "COMEX",
        "asset_class": "METAL",
        "price_unit": "USD_PER_TROY_OUNCE",
        "common_unit": "USD_PER_TROY_OUNCE",
        "unit_conversion": "no_conversion_needed",
    },
    "SI": {
        "product_code": "SI",
        "instrument": "SILVER",
        "commodity_label": "Silver",
        "exchange": "COMEX",
        "asset_class": "METAL",
        "price_unit": "USD_PER_TROY_OUNCE",
        "common_unit": "USD_PER_TROY_OUNCE",
        "unit_conversion": "no_conversion_needed",
    },
}


PREFERRED_CSV_FIELDS = [
    "source_id",
    "trade_date",
    "symbol",
    "instrument",
    "commodity_label",
    "exchange",
    "product_code",
    "futures_contract",
    "contract_rank",
    "is_front_month",
    "contract_name",
    "contract_first_trade_date",
    "contract_last_trade_date",
    "contract_days_to_maturity",
    "open",
    "high",
    "low",
    "close",
    "settlement_price",
    "settlement_price_source",
    "settlement_common_unit_value",
    "price_unit",
    "common_unit",
    "unit_conversion",
    "volume",
    "transactions",
    "window_start_ns",
    "data_type",
    "futures_data_role",
    "tos_status",
    "gatekeeper_cleared",
    "gatekeeper_id",
    "raw_source_url",
    "normalized_at",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_str(value: date) -> str:
    return value.isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    keys = list(PREFERRED_CSV_FIELDS)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            out = {}
            for key in keys:
                value = row.get(key)
                out[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            writer.writerow(out)


def request_json(base_url: str, path: str, api_key: str, params: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    request_params = dict(params)
    request_params["apiKey"] = api_key
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=request_params, timeout=30)
            if response.status_code >= 500 and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code != 200:
                try:
                    body = response.json()
                except Exception:
                    body = {"error": response.text[:500]}
                raise RuntimeError(f"Massive request failed {response.status_code}: {body.get('error') or body}")
            payload = response.json()
            if payload.get("status") not in (None, "OK"):
                raise RuntimeError(f"Massive response status={payload.get('status')}: {payload.get('error')}")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error))


def parse_targets(value: str) -> List[str]:
    targets = [item.strip().upper() for item in value.replace(";", ",").split(",") if item.strip()]
    unknown = [item for item in targets if item not in TARGET_CONFIG]
    if unknown:
        raise ValueError(f"Unknown target(s): {unknown}. Allowed: {sorted(TARGET_CONFIG)}")
    return targets or list(DEFAULT_TARGETS)


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def common_settlement_value(symbol: str, settlement_price: Optional[float]) -> Optional[float]:
    if settlement_price is None:
        return None
    if symbol in {"ZC", "ZS", "ZW"}:
        return round(settlement_price / 100.0, 8)
    return settlement_price


def sort_contracts(contracts: List[Dict[str, Any]], as_of: date) -> List[Dict[str, Any]]:
    as_of_text = date_str(as_of)
    filtered = []
    for contract in contracts:
        if contract.get("type") != "single":
            continue
        if contract.get("active") is not True:
            continue
        last_trade_date = contract.get("last_trade_date")
        if last_trade_date and last_trade_date < as_of_text:
            continue
        ticker = str(contract.get("ticker") or "")
        if ":" in ticker:
            continue
        filtered.append(contract)

    def key(item: Dict[str, Any]) -> Tuple[int, str, str]:
        days = item.get("days_to_maturity")
        days_key = int(days) if isinstance(days, int) and days >= 0 else 999999
        return (days_key, str(item.get("last_trade_date") or "9999-12-31"), str(item.get("ticker") or ""))

    return sorted(filtered, key=key)


def discover_contracts(
    base_url: str,
    api_key: str,
    symbol: str,
    requested_as_of: date,
    fallback_days: int,
    contracts_per_product: int,
) -> Tuple[List[Dict[str, Any]], str, List[str], Optional[Dict[str, Any]]]:
    cfg = TARGET_CONFIG[symbol]
    warnings = []
    last_payload = None
    for offset in range(fallback_days + 1):
        as_of = requested_as_of - timedelta(days=offset)
        params = {
            "product_code": cfg["product_code"],
            "date": date_str(as_of),
            "active": "true",
            "type": "single",
            "limit": 1000,
        }
        payload = request_json(base_url, "/futures/v1/contracts", api_key, params)
        last_payload = payload
        contracts = sort_contracts(payload.get("results", []), as_of)
        if contracts:
            if offset:
                warnings.append(f"Contract discovery used fallback as_of_date={date_str(as_of)} because {date_str(requested_as_of)} returned no active singles.")
            return contracts[:contracts_per_product], date_str(as_of), warnings, last_payload
    return [], date_str(requested_as_of), [f"No active single contracts found within {fallback_days} fallback days."], last_payload


def fetch_aggregates(
    base_url: str,
    api_key: str,
    ticker: str,
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    return request_json(
        base_url,
        f"/futures/v1/aggs/{ticker}",
        api_key,
        {
            "resolution": "1session",
            "window_start.gte": date_str(start_date),
            "window_start.lte": date_str(end_date),
            "limit": 50000,
            "sort": "window_start.asc",
        },
    )


def normalize_aggregate(
    symbol: str,
    contract: Dict[str, Any],
    contract_rank: int,
    aggregate: Dict[str, Any],
    gatekeeper_id: str,
    base_url: str,
) -> Dict[str, Any]:
    cfg = TARGET_CONFIG[symbol]
    settlement = safe_float(aggregate.get("settlement_price"))
    settlement_source = "settlement_price"
    if settlement is None:
        settlement = safe_float(aggregate.get("close"))
        settlement_source = "close_fallback"
    common_value = common_settlement_value(symbol, settlement)
    trade_date = aggregate.get("session_end_date")
    ticker = aggregate.get("ticker") or contract.get("ticker")
    return {
        "source_id": "MASSIVE_CME_FUTURES_SESSION",
        "trade_date": trade_date,
        "symbol": symbol,
        "instrument": cfg["instrument"],
        "commodity_label": cfg["commodity_label"],
        "exchange": cfg["exchange"],
        "product_code": cfg["product_code"],
        "futures_contract": ticker,
        "contract_rank": contract_rank,
        "is_front_month": contract_rank == 1,
        "contract_name": contract.get("name"),
        "contract_first_trade_date": contract.get("first_trade_date"),
        "contract_last_trade_date": contract.get("last_trade_date"),
        "contract_days_to_maturity": contract.get("days_to_maturity"),
        "open": aggregate.get("open"),
        "high": aggregate.get("high"),
        "low": aggregate.get("low"),
        "close": aggregate.get("close"),
        "settlement_price": settlement,
        "settlement_price_source": settlement_source,
        "settlement_common_unit_value": common_value,
        "price_unit": cfg["price_unit"],
        "common_unit": cfg["common_unit"],
        "unit_conversion": cfg["unit_conversion"],
        "volume": aggregate.get("volume"),
        "transactions": aggregate.get("transactions"),
        "window_start_ns": aggregate.get("window_start"),
        "data_type": "FUTURES_SESSION_SETTLEMENT",
        "futures_data_role": "FUTURES_LEG_OF_BASIS",
        "tos_status": "GO_INTERNAL_ANALYTICS",
        "gatekeeper_cleared": True,
        "gatekeeper_id": gatekeeper_id,
        "raw_source_url": f"{base_url.rstrip()}/futures/v1/aggs/{ticker}",
        "normalized_at": utc_now(),
    }


def latest_trade_date(rows: Iterable[Dict[str, Any]], symbol: str) -> Optional[str]:
    dates = [str(row.get("trade_date")) for row in rows if row.get("symbol") == symbol and row.get("trade_date")]
    return max(dates) if dates else None


def validate_records(
    records: List[Dict[str, Any]],
    targets: List[str],
    per_symbol: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    group_results = []
    all_ok = True
    for symbol in targets:
        rows = [row for row in records if row.get("symbol") == symbol]
        front_rows = [row for row in rows if row.get("is_front_month")]
        errors = []
        warnings = list(per_symbol.get(symbol, {}).get("warnings", []))
        if not per_symbol.get(symbol, {}).get("contracts"):
            errors.append("No active contracts discovered.")
        latest_front_trade_date = latest_trade_date(front_rows, symbol)
        if not rows:
            errors.append("No aggregate rows normalized.")
        if not front_rows:
            errors.append("No front-month aggregate rows normalized.")
        if any(row.get("settlement_common_unit_value") is None for row in front_rows):
            errors.append("Front-month rows contain no settlement/common value.")
        ok = not errors
        all_ok = all_ok and ok
        group_results.append(
            {
                "symbol": symbol,
                "ok": ok,
                "record_count": len(rows),
                "front_record_count": len(front_rows),
                "latest_trade_date": latest_trade_date(rows, symbol),
                "latest_front_trade_date": latest_front_trade_date,
                "front_contract": front_rows[-1].get("futures_contract") if front_rows else None,
                "contract_as_of_date": per_symbol.get(symbol, {}).get("contract_as_of_date"),
                "warnings": warnings,
                "errors": errors,
            }
        )

    return {
        "validated_at": utc_now(),
        "stage": "massive_cme_futures_fetch_normalize",
        "all_required_ok": all_ok,
        "record_count": len(records),
        "target_symbols": targets,
        "source_id": "MASSIVE_CME_FUTURES_SESSION",
        "tos_status": "GO_INTERNAL_ANALYTICS",
        "validation_rules": [
            "At least one active single contract per target.",
            "At least one 1-session aggregate bar per target.",
            "Front-month rows must have settlement_price or close fallback.",
            "Grain futures are converted from cents per bushel to USD_PER_BUSHEL.",
        ],
        "group_results": group_results,
    }


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY"))
    parser.add_argument("--base-url", default=os.environ.get("MASSIVE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--as-of-date", default=date_str(datetime.now(timezone.utc).date()))
    parser.add_argument("--contract-fallback-days", type=int, default=10)
    parser.add_argument("--history-days", type=int, default=7)
    parser.add_argument("--contracts-per-product", type=int, default=3)
    parser.add_argument("--gatekeeper-id", default=DEFAULT_GATEKEEPER_ID)
    parser.add_argument("--out-dir", type=Path, default=script_dir)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print("Missing MASSIVE_API_KEY. Set it in the environment or pass --api-key.", file=sys.stderr)
        return 2

    targets = parse_targets(args.targets)
    requested_as_of = parse_date(args.as_of_date)
    start_date = requested_as_of - timedelta(days=args.history_days)
    raw_payload: Dict[str, Any] = {
        "fetched_at": utc_now(),
        "base_url": args.base_url,
        "requested_as_of_date": date_str(requested_as_of),
        "history_start_date": date_str(start_date),
        "targets": targets,
        "symbols": {},
    }
    records: List[Dict[str, Any]] = []
    per_symbol: Dict[str, Dict[str, Any]] = {}

    for symbol in targets:
        contracts, contract_as_of_date, warnings, contract_payload = discover_contracts(
            args.base_url,
            args.api_key,
            symbol,
            requested_as_of,
            args.contract_fallback_days,
            args.contracts_per_product,
        )
        per_symbol[symbol] = {
            "contracts": contracts,
            "contract_as_of_date": contract_as_of_date,
            "warnings": warnings,
        }
        raw_payload["symbols"][symbol] = {
            "contract_as_of_date": contract_as_of_date,
            "contract_discovery": contract_payload,
            "contracts_selected": contracts,
            "aggregates": {},
            "warnings": warnings,
        }
        for rank, contract in enumerate(contracts, start=1):
            ticker = contract.get("ticker")
            if not ticker:
                continue
            aggregate_payload = fetch_aggregates(args.base_url, args.api_key, ticker, start_date, requested_as_of)
            raw_payload["symbols"][symbol]["aggregates"][ticker] = aggregate_payload
            for aggregate in aggregate_payload.get("results", []):
                records.append(
                    normalize_aggregate(
                        symbol,
                        contract,
                        rank,
                        aggregate,
                        args.gatekeeper_id,
                        args.base_url,
                    )
                )

    records.sort(key=lambda row: (row.get("symbol") or "", row.get("contract_rank") or 99, row.get("trade_date") or ""))
    out_dir = args.out_dir.resolve()
    save_json(out_dir / "raw_massive_cme_futures.json", raw_payload)
    save_json(
        out_dir / "massive_cme_futures_normalized.json",
        {
            "generated_at": utc_now(),
            "source": "MASSIVE_CME_FUTURES",
            "stage": "fetch_contracts_aggs_normalize",
            "records": records,
        },
    )
    write_csv(out_dir / "massive_cme_futures_normalized.csv", records)
    validation = validate_records(records, targets, per_symbol)
    save_json(out_dir / "massive_cme_futures_validation_report.json", validation)

    print(f"records={len(records)}")
    print(f"validation_ok={validation['all_required_ok']}")
    print(f"out_dir={out_dir}")
    return 0 if validation["all_required_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
