#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NG / ZC / ZS Narrative Sentiment LIGHT v4 Source Routing

Simplified from Step 10 Reuters/EIA Narrative Sentiment LIGHT v1.6.

Purpose
-------
Fetch only NG, ZC, and ZS narrative sentiment inputs for BASIS SENTIMENT:
  NG: Reuters proxy RSS + EIA Today in Energy RSS only
  ZC/ZS: Reuters proxy RSS + USDA RSS/proxy only
  -> headline/snippet parsing
  -> target commodity filtering
  -> event category tagging
  -> rule-based direction + confidence
  -> CSV / JSON / validation output

Policy
------
- Reuters proxy is headline/snippet event detection only.
- USDA RSS/proxy is headline/snippet event detection only.
- No full-article fetch.
- No full-article reproduction.
- Reuters output is paraphrase/summary only.
- tos_status = REVIEW_PARAPHRASE_ONLY.

Run
---
python narrative_ng_zc_zs_usda_light_v4_source_routing.py

Optional
--------
python narrative_ng_zc_zs_usda_light_v4_source_routing.py --lookback-hours 2160
python narrative_ng_zc_zs_usda_light_v4_source_routing.py --disable-eia
python narrative_ng_zc_zs_usda_light_v4_source_routing.py --disable-usda
python narrative_ng_zc_zs_usda_light_v4_source_routing.py --out-dir narrative_output/ng_zc_zs_light
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

try:
    import requests
except Exception:
    requests = None


# -----------------------------------------------------------------------------
# 1. Scope: only NG / ZC / ZS
# -----------------------------------------------------------------------------

TARGET_COMMODITIES = ["NG", "ZC", "ZS"]

DEFAULT_OUT_DIR = Path.home() / "narrative_output" / "ng_zc_zs_light"
DEFAULT_LOOKBACK_HOURS = 24 * 90
DEFAULT_MAX_ITEMS_PER_TARGET_FEED = 100
DEFAULT_MIN_ITEMS_PER_COMMODITY = 30
DEFAULT_LATEST_LIMIT = 20
DEFAULT_GATEKEEPER_ID = "LOCAL_PROTO"

DEFAULT_EIA_FEED = "https://www.eia.gov/rss/todayinenergy.xml"
DEFAULT_USDA_FEED = "https://www.usda.gov/rss/latest-releases.xml"

SOURCE_CONFIG = {
    "reuters_proxy": {
        "source_name": "Google News Reuters Commodities Proxy",
        "source_id": "GOOGLE_NEWS_REUTERS_COMMODITIES_PROXY",
        "underlying_publisher": "Reuters",
        "tos_status": "REVIEW_PARAPHRASE_ONLY",
        "gatekeeper_cleared": True,
        "policy": "Google News RSS query constrained to Reuters commodities pages; headline/snippet event detection only; no full article reproduction.",
        "is_proxy": True,
    },
    "eia": {
        "source_name": "EIA Today in Energy",
        "source_id": "EIA_TODAY_IN_ENERGY_RSS",
        "underlying_publisher": "EIA",
        "tos_status": "GO_PUBLIC_SOURCE",
        "gatekeeper_cleared": True,
        "policy": "Public EIA RSS feed.",
        "is_proxy": False,
    },
    "usda": {
        "source_name": "USDA Latest Releases RSS",
        "source_id": "USDA_LATEST_RELEASES_RSS",
        "underlying_publisher": "USDA",
        "tos_status": "GO_PUBLIC_SOURCE",
        "gatekeeper_cleared": True,
        "policy": "Public USDA RSS feed; headline/snippet event detection only.",
        "is_proxy": False,
    },
    "usda_proxy": {
        "source_name": "Google News USDA Agriculture Proxy",
        "source_id": "GOOGLE_NEWS_USDA_AGRICULTURE_PROXY",
        "underlying_publisher": "USDA",
        "tos_status": "GO_PUBLIC_SOURCE_PROXY",
        "gatekeeper_cleared": True,
        "policy": "Google News RSS query constrained to USDA.gov and agriculture report language; headline/snippet event detection only; no full article reproduction.",
        "is_proxy": True,
    },
}

COMMODITY_KEYWORDS = {
    "NG": [
        "natural gas", "natgas", "lng", "henry hub", "gas storage",
        "gas inventories", "gas output", "power burn", "feedgas",
        "working gas", "storage injection", "storage withdrawal", "gas prices"
    ],
    "ZC": [
        "corn", "maize", "yellow corn", "corn price", "corn prices",
        "corn crop", "corn exports", "corn export", "corn production",
        "corn stocks", "corn supply", "corn demand", "corn planting",
        "corn harvest", "corn condition", "ethanol", "feed grain", "feed grains",
        "coarse grain", "coarse grains", "grain exports", "crop condition",
        "planting", "harvest", "crop progress", "export sales", "wasde",
        "grain stocks", "acreage", "prospective plantings", "nass corn", "ers corn"
    ],
    "ZS": [
        "soybean", "soybeans", "soybean crop", "soybean stocks",
        "soybean export", "soybean exports", "soybean oil", "soybean meal",
        "soymeal", "soy oil", "oilseed", "oilseeds", "soy exports",
        "china soy", "china purchases", "crop condition", "planting", "harvest",
        "crop progress", "export sales", "wasde", "nass soybean", "ers soybean"
    ],
}

# v3: each target uses multiple source-specific proxy queries. This is especially
# important for ZC/ZS, because grain/oilseed coverage often appears under USDA,
# NASS, ERS, FAS, WASDE, Crop Progress, Grain Stocks, Export Sales, Feed Grains,
# or Oil Crops report names rather than simple headline strings like "corn".
REUTERS_PROXY_COMMODITY_QUERIES = {
    "NG": [
        "Reuters commodities natural gas LNG Henry Hub gas storage",
        "Reuters natural gas storage injection LNG feedgas power burn"
    ],
    "ZC": [
        "Reuters corn maize grains crop export sales ethanol",
        "Reuters corn futures crop progress ethanol exports grain stocks",
        "Reuters grains corn WASDE USDA export sales crop conditions"
    ],
    "ZS": [
        "Reuters soybeans soybean oil soymeal crop exports China",
        "Reuters soybeans oilseeds crop progress export sales China"
    ],
}

USDA_PROXY_COMMODITY_QUERIES = {
    # NG is not a USDA-native market, but fertilizer/energy-cost articles can
    # occasionally provide useful agriculture-demand context. EIA/Reuters remain
    # the primary NG narrative sources.
    "NG": [
        "USDA natural gas fertilizer energy farm costs",
        "USDA energy costs fertilizer natural gas agriculture",
        "site:ers.usda.gov fertilizer energy natural gas farm costs"
    ],

    # ZC coverage is deliberately broad across USDA/NASS/ERS/FAS because corn
    # articles often appear under report-family names rather than simple market
    # headlines. All items are still filtered to target relevance before output.
    "ZC": [
        "site:usda.gov corn WASDE ending stocks exports yield production",
        "site:usda.gov corn crop progress planting harvest condition",
        "site:nass.usda.gov corn crop progress condition planting harvest",
        "site:nass.usda.gov corn grain stocks acreage prospective plantings",
        "site:nass.usda.gov corn crop production yield",
        "site:ers.usda.gov corn feed grains outlook ethanol exports",
        "site:ers.usda.gov feed grains corn supply demand outlook",
        "site:fas.usda.gov corn export sales grain exports",
        "site:fas.usda.gov weekly export sales corn",
        "site:usda.gov feed grains corn crop production WASDE",
        "site:usda.gov grain stocks corn NASS",
        "site:usda.gov ethanol corn exports feed grains"
    ],

    # ZS mirrors the ZC structure but focuses on soybeans/oilseeds/soymeal and
    # export-demand language, especially China/unknown destinations.
    "ZS": [
        "site:usda.gov soybeans soybean meal oilseeds WASDE ending stocks exports",
        "site:usda.gov soybeans crop progress planting harvest condition",
        "site:nass.usda.gov soybeans crop progress condition planting harvest",
        "site:nass.usda.gov soybeans soybean stocks acreage prospective plantings",
        "site:nass.usda.gov soybeans crop production yield",
        "site:ers.usda.gov soybeans oil crops outlook soybean meal soybean oil",
        "site:ers.usda.gov oilseeds soybeans soybean meal outlook",
        "site:fas.usda.gov soybeans export sales China",
        "site:fas.usda.gov weekly export sales soybeans",
        "site:usda.gov oilseeds soybean meal soybean oil WASDE"
    ],
}


# Target-context fallback terms allow trusted source-specific query results to be
# kept even if the headline uses a report family phrase (e.g., "Feed Grains
# Outlook" or "Crop Progress") without repeating "corn" in every title.
# This is still conservative: fallback applies only after a target-specific
# query and requires report/context terms in the headline/snippet.
TARGET_CONTEXT_FALLBACK_TERMS = {
    "NG": [
        "fertilizer", "energy costs", "farm costs", "natural gas", "gas",
        "eia", "storage", "lng"
    ],
    "ZC": [
        "feed grains", "coarse grains", "grain stocks", "crop progress",
        "crop production", "acreage", "prospective plantings", "wasde",
        "export sales", "weekly export sales", "ethanol", "yield",
        "ending stocks", "outlook", "nass", "ers", "fas"
    ],
    "ZS": [
        "oil crops", "oilseeds", "soybean meal", "soybean oil",
        "crop progress", "crop production", "acreage", "prospective plantings",
        "wasde", "export sales", "weekly export sales", "china",
        "ending stocks", "outlook", "nass", "ers", "fas"
    ],
}

# Terms that make a target-query fallback too broad or likely cross-target.
# Direct keyword hits always pass; these only guard fallback matches.
TARGET_FALLBACK_FORBIDDEN_TERMS = {
    "ZC": ["soybean", "soybeans", "soybean meal", "soybean oil", "oilseeds"],
    "ZS": ["corn", "maize", "feed grains"],
    "NG": [],
}

EVENT_KEYWORDS = {
    "INVENTORY_DRAW": ["draw", "drawdown", "stocks fell", "stockpiles fell", "inventories fell", "inventory draw", "storage draw"],
    "INVENTORY_BUILD": ["build", "stocks rose", "stockpiles rose", "inventories rose", "inventory build", "storage build"],
    "SUPPLY_DISRUPTION": ["outage", "shutdown", "disruption", "strike", "sanction", "halt", "attack", "storm", "force majeure"],
    "SUPPLY_INCREASE": ["output rises", "production rises", "production increase", "supply increase", "higher output", "record output"],
    "DEMAND_STRENGTH": ["strong demand", "demand rises", "demand growth", "higher demand", "export demand", "strong exports"],
    "DEMAND_WEAKNESS": ["weak demand", "demand falls", "demand slowdown", "lower demand", "demand concern", "demand worries"],
    "EXPORT_STRENGTH": ["exports rise", "export sales", "strong exports", "export demand", "shipments rise"],
    "EXPORT_WEAKNESS": ["exports fall", "export slowdown", "weak exports", "shipments fall", "export curbs"],
    "WEATHER_RISK": ["weather", "drought", "rain", "flood", "freeze", "heat", "hurricane", "storm", "crop condition", "planting", "harvest"],
    "GEOPOLITICAL_RISK": ["geopolitical", "russia", "ukraine", "iran", "red sea", "sanctions", "conflict", "war", "tariff"],
    "MACRO_POLICY": ["fed", "federal reserve", "interest rates", "rates", "dollar", "treasury yields", "inflation", "cpi", "ppi", "tariff", "policy"],
    "EIA_REPORT": ["eia", "weekly petroleum", "storage report", "today in energy"],
    "USDA_REPORT": ["usda", "wasde", "crop progress", "export sales", "grain stocks", "crop production", "prospective plantings", "oilseeds", "feed grains", "nass", "ers", "fas", "acreage", "outlook"],
    "CROP_SUPPLY_REPORT": ["crop production", "grain stocks", "acreage", "prospective plantings", "yield", "ending stocks", "supply and demand", "outlook"],
}

BULLISH_HINTS = [
    "draw", "drawdown", "fell", "falls", "decline", "lower inventories",
    "shortage", "tight", "outage", "shutdown", "disruption",
    "strong demand", "demand growth", "exports rise", "export sales rise",
    "crop damage", "drought", "freeze", "poor crop", "lower production", "lower yield", "lower ending stocks", "smaller crop", "reduced crop"
]

BEARISH_HINTS = [
    "build", "rose", "rises", "rising inventories", "surplus",
    "weak demand", "demand slowdown", "lower demand", "production rise",
    "output rises", "higher output", "record output", "stronger dollar",
    "favorable weather", "bumper crop", "large crop", "higher production", "higher yield", "larger crop", "higher ending stocks", "record crop"
]

DIRECTION_BY_EVENT = {
    "INVENTORY_DRAW": "BULLISH",
    "INVENTORY_BUILD": "BEARISH",
    "SUPPLY_DISRUPTION": "BULLISH",
    "SUPPLY_INCREASE": "BEARISH",
    "DEMAND_STRENGTH": "BULLISH",
    "DEMAND_WEAKNESS": "BEARISH",
    "EXPORT_STRENGTH": "BULLISH",
    "EXPORT_WEAKNESS": "BEARISH",
    "GEOPOLITICAL_RISK": "BULLISH",
}


# -----------------------------------------------------------------------------
# 2. Helpers
# -----------------------------------------------------------------------------

def require_dependencies() -> None:
    if requests is None:
        print("[ERROR] Missing dependency: requests. Install with: pip install requests")
        raise SystemExit(2)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    preferred = [
        "event_alert_id", "target_commodity", "source_id", "source",
        "published_at", "data_type", "primary_commodity", "commodities",
        "event_category", "direction", "sentiment_score", "confidence_score",
        "confidence_label", "headline_paraphrase", "snippet_summary", "url",
        "basis_component_role", "tos_status", "gatekeeper_cleared",
        "gatekeeper_id", "raw_source_url", "retrieved_at", "normalized_at",
    ]
    keys = [k for k in preferred if any(k in r for r in rows)]
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k)
                for k in keys
            })


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def clean_text(value: Any) -> str:
    s = html.unescape("" if value is None else str(value))
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            pass

    return None


def iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def short_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8", errors="ignore") + b"\n")
    return h.hexdigest()[:24]


def as_query_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def build_google_news_feed(query: str, default_site: Optional[str] = None) -> str:
    # If the query already contains a site: operator, do not prepend another one.
    # This lets ZC use USDA/NASS/ERS/FAS-specific queries.
    q = query if "site:" in query.lower() or not default_site else f"site:{default_site} {query}"
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def build_reuters_proxy_feed(symbol: str, query: str) -> str:
    return build_google_news_feed(query, default_site="reuters.com")


def build_usda_proxy_feed(symbol: str, query: str) -> str:
    return build_google_news_feed(query, default_site="usda.gov")


def http_get_text(url: str) -> str:
    headers = {
        "User-Agent": "Metrixx-NG-ZC-ZS-Narrative-Light/1.0",
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
    }
    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=45)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.5 * (2 ** attempt))
                continue
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (2 ** attempt))

    raise RuntimeError(f"GET failed: {url}; error={last_error}")


def child_text(node: ET.Element, names: List[str]) -> str:
    wanted = {n.lower() for n in names}
    for child in node:
        tag = child.tag.split("}")[-1].lower()
        if tag in wanted:
            return child.text or ""
    return ""


def child_attr(node: ET.Element, tag_name: str, attr_name: str) -> str:
    for child in node:
        if child.tag.split("}")[-1].lower() == tag_name.lower():
            return child.attrib.get(attr_name, "")
    return ""


def parse_feed(
    xml_text: str,
    source_key: str,
    feed_url: str,
    retrieved_at: str,
    max_items: int,
    target_commodity: Optional[str],
    target_keyword_query: Optional[str],
) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    nodes = [n for n in root.iter() if n.tag.split("}")[-1].lower() == "item"]
    if not nodes:
        nodes = [n for n in root.iter() if n.tag.split("}")[-1].lower() == "entry"]

    cfg = SOURCE_CONFIG[source_key]
    rows: List[Dict[str, Any]] = []

    for node in nodes[:max_items]:
        is_atom = node.tag.split("}")[-1].lower() == "entry"
        title = clean_text(child_text(node, ["title"]))
        summary = clean_text(child_text(node, ["description", "summary", "content", "content:encoded"]))
        link = clean_text(child_text(node, ["link"]))
        if is_atom:
            link = child_attr(node, "link", "href") or link

        published_raw = child_text(node, ["pubDate", "published", "updated", "date"])
        published_at = parse_dt(published_raw) or retrieved_at

        if not title and not summary:
            continue

        raw_text_hash = short_hash(cfg["source_id"], title, summary, link, published_at)
        rows.append({
            "source_key": source_key,
            "source_id": cfg["source_id"],
            "source": cfg["source_name"],
            "target_commodity": target_commodity,
            "target_keyword_query": target_keyword_query,
            "title": title,
            "summary": summary,
            "link": link,
            "published_at": published_at,
            "published_raw": published_raw,
            "raw_text_hash": raw_text_hash,
            "raw_source_url": feed_url,
            "retrieved_at": retrieved_at,
        })

    return rows


def fetch_feed(
    source_key: str,
    feed_url: str,
    raw_dir: Path,
    max_items: int,
    target_commodity: Optional[str],
    target_keyword_query: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    retrieved_at = now_utc()
    suffix = f"_{target_commodity}" if target_commodity else ""

    try:
        xml_text = http_get_text(feed_url)
        raw_path = raw_dir / f"{source_key}{suffix}_rss_raw.xml"
        raw_path.write_text(xml_text, encoding="utf-8")

        items = parse_feed(
            xml_text=xml_text,
            source_key=source_key,
            feed_url=feed_url,
            retrieved_at=retrieved_at,
            max_items=max_items,
            target_commodity=target_commodity,
            target_keyword_query=target_keyword_query,
        )

        return items, {
            "feed_name": source_key,
            "target_commodity": target_commodity,
            "target_keyword_query": target_keyword_query,
            "feed_url": feed_url,
            "fetched": True,
            "raw_saved": str(raw_path),
            "parsed_count": len(items),
            "latest_published_at": max([i["published_at"] for i in items], default=None),
            "errors": [],
            "warnings": [],
        }

    except Exception as exc:
        error_path = raw_dir / f"{source_key}{suffix}_fetch_error.json"
        save_json(error_path, {
            "feed_name": source_key,
            "target_commodity": target_commodity,
            "feed_url": feed_url,
            "error": str(exc),
            "retrieved_at": retrieved_at,
        })
        return [], {
            "feed_name": source_key,
            "target_commodity": target_commodity,
            "target_keyword_query": target_keyword_query,
            "feed_url": feed_url,
            "fetched": False,
            "parsed_count": 0,
            "latest_published_at": None,
            "errors": [str(exc)],
            "warnings": [],
        }


def hits(text: str, keywords: List[str]) -> List[str]:
    text_l = text.lower()
    out: List[str] = []
    for kw in keywords:
        k = kw.lower()
        if re.search(r"[^\w\s-]", k):
            if k in text_l:
                out.append(kw)
        elif re.search(r"\b" + re.escape(k) + r"\b", text_l):
            out.append(kw)
    return out


def target_match_info(item: Dict[str, Any], symbol: str) -> Tuple[bool, str, List[str]]:
    """Return whether an item is relevant for the target and why.

    Direct commodity keyword hits are preferred. Query-context fallback is used
    only for target-specific source queries and only when report/context terms
    are present. This keeps ZC/ZS coverage from disappearing when USDA/NASS/ERS
    pages are titled by report family instead of by commodity name.
    """
    text = " ".join([item.get("title", ""), item.get("summary", "")]).strip()
    direct = hits(text, COMMODITY_KEYWORDS[symbol])
    if direct:
        return True, "DIRECT_KEYWORD", direct

    query = str(item.get("target_keyword_query") or "")
    if not query:
        return False, "NO_MATCH", []

    query_text = f"{query} {text}"
    fallback_hits = hits(query_text, TARGET_CONTEXT_FALLBACK_TERMS.get(symbol, []))
    forbidden_hits = hits(text, TARGET_FALLBACK_FORBIDDEN_TERMS.get(symbol, []))

    # Require at least one source/report context term in the actual item text,
    # not only in the query, to avoid keeping unrelated Google News results.
    item_context_hits = hits(text, TARGET_CONTEXT_FALLBACK_TERMS.get(symbol, []))

    if fallback_hits and item_context_hits and not forbidden_hits:
        return True, "QUERY_CONTEXT_FALLBACK", sorted(set(item_context_hits))

    return False, "NO_MATCH", []


def item_mentions_target(item: Dict[str, Any], symbol: str) -> bool:
    ok, _, _ = target_match_info(item, symbol)
    return ok


def filter_target_items(items: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for item in items:
        ok, method, terms = target_match_info(item, symbol)
        if not ok:
            continue
        cloned = dict(item)
        cloned["target_match_method"] = method
        cloned["target_match_terms"] = terms
        matched.append(cloned)
    return matched


def tag_commodities(text: str) -> Tuple[List[str], Dict[str, List[str]]]:
    hitmap = {sym: hits(text, kws) for sym, kws in COMMODITY_KEYWORDS.items()}
    hitmap = {k: v for k, v in hitmap.items() if v}
    commodities = sorted(hitmap)
    if not commodities:
        return ["UNKNOWN_COMMODITY"], {}
    return commodities, hitmap


def tag_event(text: str) -> Tuple[str, Dict[str, List[str]]]:
    hitmap = {cat: hits(text, kws) for cat, kws in EVENT_KEYWORDS.items()}
    hitmap = {k: v for k, v in hitmap.items() if v}

    if not hitmap:
        return "OTHER", {}

    priority = [
        "INVENTORY_DRAW", "INVENTORY_BUILD", "SUPPLY_DISRUPTION", "SUPPLY_INCREASE",
        "DEMAND_STRENGTH", "DEMAND_WEAKNESS", "EXPORT_STRENGTH", "EXPORT_WEAKNESS",
        "WEATHER_RISK", "GEOPOLITICAL_RISK", "EIA_REPORT", "USDA_REPORT", "MACRO_POLICY",
    ]
    for item in priority:
        if item in hitmap:
            return item, hitmap

    return sorted(hitmap)[0], hitmap


def score_direction(text: str, event_category: str, commodities: List[str]) -> Tuple[str, Dict[str, Any]]:
    bull = hits(text, BULLISH_HINTS)
    bear = hits(text, BEARISH_HINTS)
    event_dir = DIRECTION_BY_EVENT.get(event_category, "UNKNOWN")

    if bull and bear:
        return "MIXED", {"bullish_hits": bull, "bearish_hits": bear, "event_direction": event_dir}
    if bull:
        return "BULLISH", {"bullish_hits": bull, "bearish_hits": [], "event_direction": event_dir}
    if bear:
        return "BEARISH", {"bullish_hits": [], "bearish_hits": bear, "event_direction": event_dir}
    if event_dir in {"BULLISH", "BEARISH", "MIXED"}:
        return event_dir, {"bullish_hits": [], "bearish_hits": [], "event_direction": event_dir, "reason": "Inferred from event category."}
    if "UNKNOWN_COMMODITY" not in commodities and event_category != "OTHER":
        return "NEUTRAL", {"bullish_hits": [], "bearish_hits": [], "event_direction": event_dir, "reason": "Commodity/event found but no directional evidence."}
    return "UNKNOWN", {"bullish_hits": [], "bearish_hits": [], "event_direction": event_dir, "reason": "No directional evidence."}


def confidence_score(commodities: List[str], event_category: str, direction: str, detail: Dict[str, Any], text: str) -> Tuple[float, str]:
    score = 0.20
    if "UNKNOWN_COMMODITY" not in commodities:
        score += 0.25
        if len(commodities) == 1:
            score += 0.05
    if event_category != "OTHER":
        score += 0.20
    if direction in {"BULLISH", "BEARISH"}:
        score += 0.20
    elif direction == "MIXED":
        score += 0.10
    score += min(0.10, 0.03 * (len(detail.get("bullish_hits", [])) + len(detail.get("bearish_hits", []))))
    if len(text) < 25:
        score -= 0.10

    score = round(max(0.0, min(1.0, score)), 3)
    label = "high" if score >= 0.75 else "medium" if score >= 0.50 else "low"
    return score, label


def direction_to_sentiment(direction: str, confidence: float) -> float:
    if direction == "BULLISH":
        return confidence
    if direction == "BEARISH":
        return -confidence
    return 0.0


def paraphrase(text: str, max_len: int = 180) -> str:
    s = clean_text(text)
    s = re.sub(r"\s+-\s+Reuters.*$", "", s, flags=re.I)
    s = re.sub(r"\s+\|\s+Reuters.*$", "", s, flags=re.I)
    return s if len(s) <= max_len else s[:max_len - 3].rstrip() + "..."


def summarize(text: str, max_len: int = 260) -> str:
    s = clean_text(text)
    return s if len(s) <= max_len else s[:max_len - 3].rstrip() + "..."


def build_event(item: Dict[str, Any], gatekeeper_id: str) -> Dict[str, Any]:
    cfg = SOURCE_CONFIG[item["source_key"]]
    text = " ".join([item.get("title", ""), item.get("summary", "")]).strip()

    commodities, commodity_hits = tag_commodities(text)

    # If the item passed via target-query context fallback, force the requested
    # target into the commodity list while preserving an audit trail.
    target = item.get("target_commodity")
    target_match_method = item.get("target_match_method")
    target_match_terms = item.get("target_match_terms") or []
    if target and target_match_method == "QUERY_CONTEXT_FALLBACK":
        if commodities == ["UNKNOWN_COMMODITY"]:
            commodities = [target]
            commodity_hits = {target: ["target_context_fallback"] + list(target_match_terms)}
        elif target not in commodities:
            commodities = sorted(set(commodities + [target]))
            commodity_hits[target] = ["target_context_fallback"] + list(target_match_terms)

    event_category, event_hits = tag_event(text)
    direction, detail = score_direction(text, event_category, commodities)
    conf, conf_label = confidence_score(commodities, event_category, direction, detail, text)
    sentiment = direction_to_sentiment(direction, conf)

    primary = target if target in commodities else commodities[0]
    event_id = f"{cfg['source_id']}_{short_hash(item.get('raw_text_hash', ''), primary, event_category, direction)}"

    return {
        "event_alert_id": event_id,
        "source_id": cfg["source_id"],
        "source": cfg["source_name"],
        "underlying_publisher": cfg.get("underlying_publisher"),
        "source_is_proxy": cfg.get("is_proxy", False),
        "target_commodity": target,
        "target_match_method": target_match_method,
        "target_match_terms": target_match_terms,
        "timestamp": item["published_at"],
        "published_at": item["published_at"],
        "data_type": "NARRATIVE_EVENT",
        "commodities": commodities,
        "primary_commodity": primary,
        "commodity_keyword_hits": commodity_hits,
        "event_category": event_category,
        "event_keyword_hits": event_hits,
        "direction": direction,
        "direction_detail": detail,
        "confidence_score": conf,
        "confidence_label": conf_label,
        "sentiment_score": sentiment,
        "confidence_method": "RULE_BASED",
        "headline_original": item.get("title", "")[:240],
        "headline_paraphrase": paraphrase(item.get("title", "")),
        "snippet_summary": summarize(item.get("summary", "")),
        "url": item.get("link"),
        "raw_text_hash": item.get("raw_text_hash"),
        "basis_component_role": "NARRATIVE_SENTIMENT_ONLY",
        "basis_ready": False,
        "missing_components": "physical_price;futures_settlement;cot_overlay",
        "tos_status": cfg["tos_status"],
        "gatekeeper_cleared": cfg["gatekeeper_cleared"],
        "gatekeeper_id": gatekeeper_id,
        "raw_source_url": item.get("raw_source_url"),
        "retrieved_at": item.get("retrieved_at"),
        "normalized_at": now_utc(),
        "full_article_text_included": False,
        "data_policy": cfg["policy"],
        "claude_preprocessing_payload": {
            "task": "commodity_narrative_sentiment_preprocessing",
            "target_commodity": target,
            "primary_commodity": primary,
            "event_category": event_category,
            "direction": direction,
            "confidence_score": conf,
            "sentiment_score": sentiment,
            "confidence_method": "RULE_BASED",
            "headline_paraphrase": paraphrase(item.get("title", "")),
            "snippet_summary": summarize(item.get("summary", "")),
            "source": cfg["source_name"],
            "tos_status": cfg["tos_status"],
            "do_not_quote_source_text": True,
            "full_article_text_included": False,
        },
    }


def dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        base = item.get("raw_text_hash") or short_hash(item.get("title", ""), item.get("link", ""))
        key = short_hash(item.get("target_commodity") or "NO_TARGET", base)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def filter_lookback(events: List[Dict[str, Any]], hours: int) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for event in events:
        dt = iso_to_dt(event.get("published_at"))
        if dt is None or dt >= cutoff:
            out.append(event)
    return out


def aggregate_by_target(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for symbol in TARGET_COMMODITIES:
        group = [e for e in events if e.get("target_commodity") == symbol]
        bullish = len([e for e in group if e.get("direction") == "BULLISH"])
        bearish = len([e for e in group if e.get("direction") == "BEARISH"])
        neutral = len([e for e in group if e.get("direction") == "NEUTRAL"])
        mixed = len([e for e in group if e.get("direction") == "MIXED"])
        unknown = len([e for e in group if e.get("direction") == "UNKNOWN"])

        avg_score = round(sum(float(e.get("sentiment_score") or 0.0) for e in group) / len(group), 6) if group else 0.0

        if avg_score > 0.15:
            aggregate_direction = "bullish"
        elif avg_score < -0.15:
            aggregate_direction = "bearish"
        elif bullish > bearish and avg_score > 0:
            aggregate_direction = "neutral_to_slightly_bullish"
        elif bearish > bullish and avg_score < 0:
            aggregate_direction = "neutral_to_slightly_bearish"
        else:
            aggregate_direction = "near_neutral"

        rows.append({
            "target_commodity": symbol,
            "basis_component_role": "NARRATIVE_SENTIMENT_AGGREGATE",
            "record_count": len(group),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "mixed_count": mixed,
            "unknown_count": unknown,
            "avg_sentiment_score": avg_score,
            "aggregate_direction": aggregate_direction,
            "latest_event_timestamp": max([e.get("published_at") for e in group], default=None),
            "top_event_headline": group[0].get("headline_paraphrase") if group else None,
            "tos_status": "GO_WITH_SOURCE_SPECIFIC_TOS",
            "gatekeeper_cleared": True,
            "generated_at": now_utc(),
        })
    return rows


def validate(events: List[Dict[str, Any]], feed_metas: List[Dict[str, Any]], min_items_per_commodity: int) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not any(m.get("fetched") for m in feed_metas):
        errors.append("No feed fetched successfully.")

    warnings.append("Reuters source is a Google News Reuters proxy. Use only headline/snippet event detection; no full article reproduction.")
    warnings.append("Source routing enforced: NG uses Reuters/EIA only; ZC/ZS use Reuters/USDA only. USDA sources are headline/snippet event detection only; no full article reproduction.")

    for event in events:
        if event.get("full_article_text_included") is True:
            errors.append(f"Full article included in event: {event.get('event_alert_id')}")
        if event.get("source_id") == "GOOGLE_NEWS_REUTERS_COMMODITIES_PROXY" and event.get("tos_status") != "REVIEW_PARAPHRASE_ONLY":
            errors.append(f"Reuters proxy TOS violation: {event.get('event_alert_id')}")
        conf = event.get("confidence_score")
        if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
            errors.append(f"Bad confidence score: {event.get('event_alert_id')}")

    coverage = []
    for symbol in TARGET_COMMODITIES:
        count = len([e for e in events if e.get("target_commodity") == symbol])
        status = "OK" if count >= min_items_per_commodity else "BELOW_TARGET_BUT_VALID"
        if status != "OK":
            warnings.append(f"{symbol}: {count} normalized events, below target minimum {min_items_per_commodity}. Kept all available records.")
        coverage.append({
            "target_commodity": symbol,
            "target_minimum": min_items_per_commodity,
            "normalized_count": count,
            "coverage_status": status,
        })

    return {
        "validated_at": now_utc(),
        "stage": "NG_ZC_ZS_NARRATIVE_LIGHT_VALIDATION",
        "all_required_ok": len(errors) == 0,
        "target_commodities": TARGET_COMMODITIES,
        "record_count": len(events),
        "target_coverage_summary": coverage,
        "feed_results": feed_metas,
        "errors": errors,
        "warnings": warnings,
        "validation_rules": {
            "scope": "Only NG, ZC, ZS are collected. Source routing: NG excludes USDA; ZC/ZS exclude EIA.",
            "reuters_policy": "Reuters proxy records must be REVIEW_PARAPHRASE_ONLY.",
            "usda_policy": "USDA RSS/proxy records are public-source headline/snippet event detection only.",
            "no_full_article_text": "Full article body is not fetched or stored.",
            "direction": "Direction is BULLISH / BEARISH / MIXED / NEUTRAL / UNKNOWN.",
            "confidence": "confidence_score must be numeric in [0, 1].",
            "basis_role": "Output is narrative sentiment component only; not full basis calculation.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch NG/ZC/ZS narrative sentiment from Reuters proxy + EIA RSS.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--max-items-per-target-feed", type=int, default=DEFAULT_MAX_ITEMS_PER_TARGET_FEED)
    parser.add_argument("--min-items-per-commodity", type=int, default=DEFAULT_MIN_ITEMS_PER_COMMODITY)
    parser.add_argument("--latest-limit", type=int, default=DEFAULT_LATEST_LIMIT)
    parser.add_argument("--disable-reuters", action="store_true")
    parser.add_argument("--disable-eia", action="store_true")
    parser.add_argument("--disable-usda", action="store_true")
    parser.add_argument("--disable-usda-proxy", action="store_true", help="Disable target-specific USDA Google News proxy queries.")
    parser.add_argument("--eia-feed", default=DEFAULT_EIA_FEED)
    parser.add_argument("--usda-feed", default=DEFAULT_USDA_FEED)
    parser.add_argument("--gatekeeper-id", default=DEFAULT_GATEKEEPER_ID)
    return parser.parse_args()


def main() -> int:
    require_dependencies()
    args = parse_args()

    out_dir = Path(args.out_dir).resolve()
    raw_dir = out_dir / "raw"
    combined_dir = out_dir / "combined"
    ensure_dir(raw_dir)
    ensure_dir(combined_dir)

    print("=" * 88)
    print("NG / ZC / ZS Narrative Sentiment LIGHT v4 Source Routing")
    print("=" * 88)
    print(f"Output directory:          {out_dir}")
    print(f"Targets:                   {', '.join(TARGET_COMMODITIES)}")
    print(f"Lookback hours:            {args.lookback_hours} ({round(args.lookback_hours / 24, 2)} days)")
    print(f"Max items per target feed: {args.max_items_per_target_feed}")
    print(f"Min items per commodity:   {args.min_items_per_commodity}")
    print(f"Reuters proxy enabled:     {not args.disable_reuters}")
    print(f"EIA RSS enabled:           {not args.disable_eia}")
    print(f"USDA RSS enabled:          {not args.disable_usda}")
    print(f"USDA proxy enabled:        {not args.disable_usda_proxy}")
    print("Source routing:            NG = Reuters+EIA only; ZC/ZS = Reuters+USDA only")
    print("Full article fetch:        NO")
    print()

    all_items: List[Dict[str, Any]] = []
    feed_metas: List[Dict[str, Any]] = []

    eia_cache: Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]] = None
    usda_cache: Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]] = None

    def get_eia_cache() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        nonlocal eia_cache
        if eia_cache is None:
            print("[FETCH] EIA Today in Energy RSS cache")
            items, meta = fetch_feed(
                source_key="eia",
                feed_url=args.eia_feed,
                raw_dir=raw_dir,
                max_items=args.max_items_per_target_feed,
                target_commodity=None,
                target_keyword_query="EIA Today in Energy base RSS cache",
            )
            print(f"  fetched={meta.get('fetched')} parsed={meta.get('parsed_count')} latest={meta.get('latest_published_at')}")
            eia_cache = (items, meta)
        return eia_cache

    def get_usda_cache() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        nonlocal usda_cache
        if usda_cache is None:
            print("[FETCH] USDA Latest Releases RSS cache")
            items, meta = fetch_feed(
                source_key="usda",
                feed_url=args.usda_feed,
                raw_dir=raw_dir,
                max_items=args.max_items_per_target_feed,
                target_commodity=None,
                target_keyword_query="USDA Latest Releases base RSS cache",
            )
            print(f"  fetched={meta.get('fetched')} parsed={meta.get('parsed_count')} latest={meta.get('latest_published_at')}")
            usda_cache = (items, meta)
        return usda_cache

    for rank, symbol in enumerate(TARGET_COMMODITIES, start=1):
        if not args.disable_reuters:
            for q_rank, query in enumerate(as_query_list(REUTERS_PROXY_COMMODITY_QUERIES[symbol]), start=1):
                feed_url = build_reuters_proxy_feed(symbol, query)
                print(f"[TARGET {rank}] {symbol} Reuters proxy q{q_rank}")
                items, meta = fetch_feed(
                    source_key="reuters_proxy",
                    feed_url=feed_url,
                    raw_dir=raw_dir,
                    max_items=args.max_items_per_target_feed,
                    target_commodity=symbol,
                    target_keyword_query=query,
                )
                meta["target_query_rank"] = q_rank
                matched = filter_target_items(items, symbol)
                meta["target_matched_count"] = len(matched)
                feed_metas.append(meta)
                all_items.extend(matched)
                print(f"  parsed={meta.get('parsed_count')} matched={len(matched)} latest={meta.get('latest_published_at')}")

        if (not args.disable_eia) and symbol == "NG":
            print(f"[TARGET {rank}] {symbol} EIA RSS filter")
            cached_items, cached_meta = get_eia_cache()
            items = []
            for item in cached_items:
                cloned = dict(item)
                cloned["target_commodity"] = symbol
                cloned["target_keyword_query"] = f"EIA Today in Energy filtered by {symbol} keywords"
                items.append(cloned)
            matched = filter_target_items(items, symbol)
            meta = dict(cached_meta)
            meta.update({
                "target_commodity": symbol,
                "target_keyword_query": f"EIA Today in Energy filtered by {symbol} keywords",
                "target_matched_count": len(matched),
            })
            feed_metas.append(meta)
            all_items.extend(matched)
            print(f"  parsed={meta.get('parsed_count')} matched={len(matched)} latest={meta.get('latest_published_at')}")

        if (not args.disable_usda) and symbol in {"ZC", "ZS"}:
            print(f"[TARGET {rank}] {symbol} USDA RSS filter")
            cached_items, cached_meta = get_usda_cache()
            items = []
            for item in cached_items:
                cloned = dict(item)
                cloned["target_commodity"] = symbol
                cloned["target_keyword_query"] = f"USDA Latest Releases filtered by {symbol} keywords"
                items.append(cloned)
            matched = filter_target_items(items, symbol)
            meta = dict(cached_meta)
            meta.update({
                "target_commodity": symbol,
                "target_keyword_query": f"USDA Latest Releases filtered by {symbol} keywords",
                "target_matched_count": len(matched),
            })
            feed_metas.append(meta)
            all_items.extend(matched)
            print(f"  parsed={meta.get('parsed_count')} matched={len(matched)} latest={meta.get('latest_published_at')}")

        if (not args.disable_usda_proxy) and symbol in {"ZC", "ZS"}:
            for q_rank, query in enumerate(as_query_list(USDA_PROXY_COMMODITY_QUERIES[symbol]), start=1):
                feed_url = build_usda_proxy_feed(symbol, query)
                print(f"[TARGET {rank}] {symbol} USDA proxy q{q_rank}")
                items, meta = fetch_feed(
                    source_key="usda_proxy",
                    feed_url=feed_url,
                    raw_dir=raw_dir,
                    max_items=args.max_items_per_target_feed,
                    target_commodity=symbol,
                    target_keyword_query=query,
                )
                meta["target_query_rank"] = q_rank
                matched = filter_target_items(items, symbol)
                meta["target_matched_count"] = len(matched)
                feed_metas.append(meta)
                all_items.extend(matched)
                print(f"  parsed={meta.get('parsed_count')} matched={len(matched)} latest={meta.get('latest_published_at')}")

    unique_items = dedup(all_items)
    events = [build_event(item, args.gatekeeper_id) for item in unique_items]
    events = filter_lookback(events, args.lookback_hours)
    events.sort(key=lambda e: e.get("published_at") or "", reverse=True)

    aggregate = aggregate_by_target(events)
    latest_events = events[: args.latest_limit]
    report = validate(events, feed_metas, args.min_items_per_commodity)

    save_json(combined_dir / "ng_zc_zs_narrative_events_normalized.json", {
        "generated_at": now_utc(),
        "source": "REUTERS_EIA_USDA_NARRATIVE_SENTIMENT",
        "stage": "ng_zc_zs_narrative_light_v3_ag_coverage",
        "target_commodities": TARGET_COMMODITIES,
        "lookback_hours": args.lookback_hours,
        "records": events,
        "aggregate_by_target": aggregate,
    })
    write_jsonl(combined_dir / "ng_zc_zs_narrative_events_normalized.jsonl", events)
    write_csv(combined_dir / "ng_zc_zs_narrative_events_normalized.csv", events)
    write_csv(combined_dir / "ng_zc_zs_narrative_aggregate.csv", aggregate)
    save_json(combined_dir / "ng_zc_zs_narrative_aggregate.json", {
        "generated_at": now_utc(),
        "target_commodities": TARGET_COMMODITIES,
        "records": aggregate,
    })
    write_csv(combined_dir / "ng_zc_zs_narrative_latest.csv", latest_events)
    save_json(combined_dir / "ng_zc_zs_narrative_latest.json", {
        "generated_at": now_utc(),
        "latest_limit": args.latest_limit,
        "records": latest_events,
    })
    save_json(combined_dir / "ng_zc_zs_narrative_validation_report.json", report)

    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)
    print(f"Target-matched raw items: {len(all_items)}")
    print(f"After deduplication:      {len(unique_items)}")
    print(f"Normalized events:        {len(events)}")
    print(f"Validation:               {'PASS' if report['all_required_ok'] else 'FAIL'}")
    for row in aggregate:
        print(
            f"{row['target_commodity']}: count={row['record_count']} "
            f"avg_score={row['avg_sentiment_score']} direction={row['aggregate_direction']}"
        )

    print("\nSaved outputs:")
    for name in [
        "ng_zc_zs_narrative_events_normalized.csv",
        "ng_zc_zs_narrative_aggregate.csv",
        "ng_zc_zs_narrative_latest.csv",
        "ng_zc_zs_narrative_validation_report.json",
    ]:
        print(combined_dir / name)

    if report["all_required_ok"]:
        print("\n[DONE] NG/ZC/ZS narrative sentiment completed.")
        return 0

    print("\n[DONE WITH WARNINGS/ERRORS] Review validation report.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
