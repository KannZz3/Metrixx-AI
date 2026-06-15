# Metrixx-AI (MXXI)

[![Language](https://img.shields.io/badge/Language-English%20%2F%20Chinese-blue.svg)](#)
[![Status](https://img.shields.io/badge/Status-Public%20Research%20Subset-orange.svg)](#)
[![Gatekeeper](https://img.shields.io/badge/Gatekeeper-catalog.json-green.svg)](catalog.json)

Metrixx-AI is a compliance-aware commodity market intelligence prototype. It ingests permitted public/reference data, normalizes it into auditable records, validates data quality, and fuses futures, physical prices, COT positioning, and narrative sentiment into Basis Sentiment outputs.

中文简述：本仓库是 METRIXX / MY DESK 平台的公开研究子集，重点展示商品市场数据接入、标准化、合规标记、COT 打分、新闻叙事和基差情绪复现流程。

## What This Repo Does

```text
EIA / USDA / CFTC / CME(Massive) / Reuters RSS / Baker Hughes / FRED
        -> fetch / parse
        -> normalize with gatekeeper fields
        -> validate source quality and freshness
        -> score COT and narrative signals
        -> build Basis Sentiment records
        -> feed prompt, WATOS, and VAULT-style intelligence workflows
```

This is not the full production platform. It excludes private subscriber services, proprietary databases, and production MY DESK / VAULT infrastructure.

## Core Outputs

| Output | Location | Notes |
|---|---|---|
| CME futures leg | `Data_light_version/CME/` | Massive Futures REST session settlements for CL, NG, ZC, ZS, ZW, GC, SI. |
| Physical prices | `Data_light_version/EIA/`, `Data_light_version/USDA/` | EIA energy spot prices and USDA AMS grain prices, including explicit proxy/fallback labels. |
| COT positioning | `Data_light_version/CFTC/` | CFTC disaggregated and legacy futures-only positioning with field-quality checks. |
| COT scoring | `COT_scoring_positioning/` | Managed Money, Producer/Merchant, impulse, and legacy confirmation gate. |
| Narrative events | `Data_light_version/Reuters/` | Headline/snippet-only event tagging and rule-based sentiment. |
| Macro and rig overlays | `Data_light_version/FRED/`, `Data_light_version/Baker Hughes/` | Macro context and rig-count supply signal. |
| Basis Sentiment | `Basis_sentiment/` | Fused futures, physical, COT, narrative, term-structure, and quality flags. |
| Reproducibility audit | `tools/validate_repository_snapshot.py` | Checks committed validation reports and key CSV outputs without API calls. |

## Repository Map

| Path | Purpose |
|---|---|
| `catalog.json` | Minimal source-compliance catalog and status definitions. |
| `requirements.txt` | Runtime dependencies for local fetchers and parsers. |
| `artifacts/repository_snapshot.json` | Latest checked-in reproducibility snapshot. |
| `Data_light_version/` | Lightweight source-specific ingestion and normalization modules. |
| `Basis_sentiment/` | Historical hand-merged samples plus reproducible Massive CME-driven basis outputs. |
| `Data_backend/` | Provider-adapter architecture guidance. |
| `Docs/` | Product, sprint, prompt, onboarding, and gatekeeper reference documents. |

## Current Validation Snapshot

| Layer | Current checked-in result |
|---|---|
| EIA | 6 energy series, 180 records, latest physical date `2026-05-18`. |
| USDA | 180 grain physical records; corn Decatur/Gulf use explicit proxy/fallback mappings. |
| CFTC positioning | 2,184 records across CL, NG, ZC, ZS, ZW, GC, SI. |
| COT scoring | 1,092 scored rows; latest COT report date `2026-05-19`. |
| Narrative | 57 headline/snippet event records; valid with coverage warnings. |
| Baker Hughes | 125 weekly U.S. rig-count records through `2026-05-22`. |
| FRED | 3,194 macro overlay records across DFF, T10Y2Y, CPIAUCSL, PPIACO. |
| Massive CME | 104 futures session records; latest front trade date mostly `2026-06-15`. |
| CME-driven Basis | 9 fused rows, 7 basis-ready rows, 2 futures-only metal rows. |

Run the audit:

```bash
python tools/validate_repository_snapshot.py
```

Expected result:

```text
overall_ok=True
```

## Basis Sentiment Logic

The reproducible basis builder is:

```bash
python Basis_sentiment/build_basis_sentiment_from_cme.py
```

It merges:

- Massive CME futures settlements from `Data_light_version/CME/massive_cme_futures_normalized.json`
- EIA / USDA latest available physical prices
- Latest COT scores
- Narrative sentiment aggregates
- Front/next futures spread and term-structure signal

Formula:

```text
basis_value_asof = physical_price_converted - futures_settlement_converted
```

Important quality behavior:

- Grain futures are converted from cents/bushel to USD/bushel.
- Soybean meal is treated as a related proxy for ZS, matching the existing project sample logic.
- GC and SI are emitted as futures-only rows because no public physical leg exists in this subset.
- Stale physical inputs are not hidden; they are flagged as `STALE_PHYSICAL_*_REVIEW`.

## Run Key Pipelines

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Fetch Massive CME futures:

```bash
set MASSIVE_API_KEY=your_key_here
python Data_light_version/CME/massive_cme_futures_fetcher.py --as-of-date 2026-06-15 --history-days 7 --contracts-per-product 3
```

PowerShell:

```powershell
$env:MASSIVE_API_KEY="your_key_here"
python Data_light_version/CME/massive_cme_futures_fetcher.py --as-of-date 2026-06-15 --history-days 7 --contracts-per-product 3
```

Other source modules are standalone:

```bash
python Data_light_version/EIA/normalize_eia.py
python Data_light_version/CFTC/cftc_cot_fetcher_light_v5_field_quality_fixed.py --limit-per-symbol 156 --query-limit 5000
python COT_scoring_positioning/cftc_cot_scoring_v1.2.py
python Data_light_version/Reuters/_reuters_eia_narrative_light_v1_6.py
python "Data_light_version/Baker Hughes/baker_hughes_rig_count_light_2026.py" --local-file "Data_light_version/Baker Hughes/05-22-2026 North_America Rig_Count Report.xlsx"
```

## Gatekeeper Policy

Every normalized record should carry `tos_status`, `gatekeeper_cleared`, `gatekeeper_id`, and `raw_source_url`.

| Status | Meaning |
|---|---|
| `GO` | Full ingestion and derived analytics allowed. |
| `GO_INTERNAL_ANALYTICS` | Internal analytics only; do not redistribute licensed raw data. |
| `REVIEW_PARAPHRASE_ONLY` | Headline/snippet event detection only; no full article storage. |
| `HOLD` | Blocked pending licensing or AI/data-use approval. |
| `RED` | Hard blocked for ingestion. |

See `catalog.json` for the source-level mapping.

## Known Boundaries

- No production database schema or CI pipeline is included.
- FRED outputs are included, but the FRED fetcher is not included in this snapshot.
- CME/Massive API keys must stay in environment variables and must not be committed.
- `Data_light_version/CME/raw_massive_cme_futures.json` is intentionally ignored.
- This repository is for research and internal development documentation, not investment advice.
