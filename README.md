# Metrixx-AI (MXXI)

[![Bilingual](https://img.shields.io/badge/Language-English%20%2F%20%E4%B8%AD%E6%96%87-blue.svg)](#)
[![Status](https://img.shields.io/badge/Status-MXXI%20Intern%20Initiative-orange.svg)](#)
[![Server](https://img.shields.io/badge/Server-illini.metrixx.ai-purple.svg)](#)

> **Important Note / 重要说明:**  
> This repository documents selected data ingestion microservices, prompt schemas, and architecture guidelines built for the **MXXI Intern Server Initiative (METRIXX-ILLINI)** running at `illini.metrixx.ai`. It represents a selected, isolated development subset of the overall platform. The full production-grade subscriber platform, proprietary databases, and core backend pipelines remain confidential and isolated.  
> 
> 本仓库仅包含为 **MXXI 研发服务器 (METRIXX-ILLINI, 部署于 `illini.metrixx.ai`)** 开发的特定数据接入微服务、提示词架构和开发规范。本仓库仅展示了整个项目中的一部分隔离开发模块。完整的生产级用户平台、专属数据库以及核心后台系统均保持高度保密与隔离。

---

## 1. Project Overview / 项目概览

**Metrixx-AI** serves as the core data intelligence and semantic generation layer for the **MY DESK** market intelligence platform (running at `chat.metrixx.ai`). 

The system processes physical cash bids, CME futures settlements, CFTC commitments of traders (COT) positioning, and news narrative sentiment into structured intelligence. It supports three subscriber-facing output formats:
1. **Short Form** (📄): ≤ 50-character ticker alerts or WATOS™ tags.
2. **Long Form** (📝): ≤ 50-word narrative briefs with supporting data context.
3. **Excel/VAULT** (📊): Watermarked `.xlsx` workbooks archived directly to the **VAULT** persistent library.

**Metrixx-AI** 构成了面向 **MY DESK** 市场情报平台的核心数据智能与语义生成层。

系统将现货商品价格、CME 期货结算价、CFTC COT 持仓结构以及新闻叙事情绪整合为结构化情报。它原生支持三种订阅用户端输出格式：
1. **短线警报** (📄): ≤ 50 字符的滚动警报或 WATOS™ 评分标签。
2. **长线简报** (📝): ≤ 50 单词的深度基本面/筹码面量化简报。
3. **Excel/VAULT** (📊): 包含订阅用户水印和法律免责声明的标准化 `.xlsx` 工作簿，自动归档至 **VAULT** 永久库。

---

## 2. Ingestion Microservices / 数据接入微服务

Ingestion modules run as modular Python microservices located under `Data_light_version/`:

* **EIA Energy Spot Prices** ([normalize_eia.py](Data_light_version/EIA/normalize_eia.py)): Daily Cushing spot prices for Crude (WTI/Brent), Gas (Henry Hub), LA RBOB, NYH Heating Oil, and USGC Jet Fuel.
* **USDA AMS Grain Bids** ([usda_unified_fetcher](Data_light_version/USDA/usda_unified_fetcher_light_v4_commodity_split_fixed.py)): Cash bids for Corn, Soybean Meal, and Wheat. Employs Illinois elevator bids (`slug 3192`) and Barge terminal bids (`slug 3043`) as proxies for Decatur and Gulf Corn.
* **CFTC COT Scoring** ([cftc_cot_scoring_v1.2.py](COT_scoring_positioning/cftc_cot_scoring_v1.2.py)): Weekly Disaggregated Managed Money crowding, Producer/Merchant pressure, and Positioning Impulse metrics with a field-quality audit (v5) and legacy COT confirmation gate.
* **Reuters & USDA RSS Narrative** ([_reuters_eia_narrative_light_v1_6.py](Data_light_version/Reuters/_reuters_eia_narrative_light_v1_6.py)): Headline/snippet event categorization (e.g. `INVENTORY_DRAW`, `SUPPLY_DISRUPTION`) and directional sentiment matching using Google News/USDA RSS proxies to maintain strict TOS compliance.
* **Baker Hughes Rig Count** ([rig_count_parser](Data_light_version/Baker%20Hughes/baker_hughes_rig_count_light_2026.py)): Weekly Rotary Rig counts. A drop of **≥ 10 WoW** triggers an `energy_scarcity_trigger` (bullish supply bias).

数据接入模块均作为容器化的 Python 微服务运行于 `Data_light_version/` 目录下：
* **EIA 能源现货价格**: 抓取 WTI、布伦特、亨利枢纽天然气、LA RBOB、NYH 取暖油及美湾航煤的每日 Cushing 现货价格。
* **USDA AMS 谷物价格**: 抓取玉米、豆粕以及小麦现货买标。使用伊利诺伊电梯（`slug 3192`）及驳船码头（`slug 3043`）价格作为迪凯特与美湾玉米现货的代理指标（Proxy）。
* **CFTC COT 持仓打分**: 抓取每周持仓数据。通过基金拥挤度、商业套保压力及持仓冲量计算综合得分，具备字段质量审计（v5）及历史持仓确认机制。
* **路透/USDA 叙事分类**: 通过谷歌新闻与 USDA RSS 代理服务器抓取新闻片段，在确保合规的前提下进行事件归类（如库存减少、供应中断等）与情绪识别。
* **贝克休斯钻井统计**: 自动监控每周旋转钻井总数，当单周降幅达 **10口及以上** 时触发“能源稀缺警报”（看涨供应短缺）。

---

## 3. Analytics & Prompt Catalog / 分析矩阵与提示词目录

The **Prompt Catalog v2.0** (Rev 8) defines the platform's analytical catalog:
* **WATOS™ Composite Score**: Integrates: (1) Directional probability, (2) Options GEX weight, and (3) MIXX regime confidence. Employs the naming format: `WATOS_{SUBSCRIBER_ID}_{UNDERLYING}_{STRATEGY_CODE}_{YYYYMMDD}_{SESSION}.xlsx`.
* **Gamma Playbook (GP)**: Evaluates dealer GEX, Put/Call Walls, net gamma, and weekly pin risk (SPX, QQQ, NVDA, TSLA, BTC).
* **0DTE Options Strategy**: Same-day expiry setups overlaid with Market Profile POC and high-volume nodes (HVN).
* **Market Profile & Order Flow**: Value Area (VAH/VAL/POC) auction theory, day type, and footprint delta divergence.

**Prompt Catalog v2.0 (Rev 8)** 规范了平台的提示词与分析矩阵：
* **WATOS™ 综合评分**: 整合：(1) 方向性概率, (2) 期权 GEX 敞口权重, 以及 (3) MIXX 波动率体制置信度。采用命名规范：`WATOS_{SUBSCRIBER_ID}_{UNDERLYING}_{STRATEGY_CODE}_{YYYYMMDD}_{SESSION}.xlsx`。
* **Gamma 策略手册 (GP)**: 评估期权做市商 GEX、行权墙、净 Gamma 敞口及到期钉住风险。
* **0DTE 日内期权策略**: 提供 0DTE 日内开盘/破位交易，强制关联 Market Profile 控制点 (POC) 与高筹码区 (HVN)。
* **Market Profile 与订单流**: 提供基于拍卖市场理论的价值区间 (VAH/VAL/POC) 研判、日内行情类型分类及足迹图 Delta 背离分析。

---

## 4. Gatekeeper & TOS Compliance / 数据准入与合规矩阵

All sources are monitored via a master `catalog.json` compliance log:
* **GO (Cleared)**: EIA API, USDA AMS, CFTC, FRED, Baker Hughes. Full ingestion allowed.
* **GO (Internal Only)**: CME Daily Settlements. Valid for internal analytics only.
* **REVIEW (Paraphrase Only)**: Reuters RSS, ICIS. Snippet/headline event detection only; no full article body is stored.
* **HOLD (Blocked)**: Barchart cmdtyView, S&P Global Platts. Pending licensing.
* **RED (Hard Blocked)**: Argus Media. Assessed prices are strictly prohibited due to explicit AI ingestion limits.

所有集成的数据源均通过 `catalog.json` 准入清单进行严格合规审查：
* **GO (放行)**: EIA、USDA AMS、CFTC、FRED、贝克休斯。完全放行。
* **GO (仅限内部)**: CME 每日结算价。仅适用于内部计算分析，限制分发。
* **REVIEW (限改写)**: 路透社 RSS、ICIS。仅支持抓取标题/片段用于事件标记，禁止复制正文。
* **HOLD (暂停)**: Barchart cmdtyView、标普全球普氏。等待商业合同或 AI 授权协议签署。
* **RED (禁止)**: 阿格斯媒体。因明确禁止 AI 摄取，严禁导入其评估价格。

---

## 5. Architecture Standards / 接口与开发规范

The codebase adheres to a strict **downward dependency clean architecture** (see [adding-polygon-provider.md](Data_backend/adding-polygon-provider.md)):
1. **Adapter Isolation**: Provider-specific shapes are sealed in adapter classes (extending `BaseAdapter`). Upstream services remain completely provider-agnostic.
2. **Capability Interfaces**: Services narrow adapter interfaces dynamically via type-guards (e.g. `supportsOptions(adapter)`) rather than checking provider names.

本仓库的开发遵循**下向依赖清洁架构**（详见 [adding-polygon-provider.md](Data_backend/adding-polygon-provider.md)）：
1. **数据源适配器隔离**: 提供商特定的参数、认证及解析完全隔离在继承自 `BaseAdapter` 的适配器中，服务层完全独立。
2. **能力 Narrowing 机制**: 严禁在服务层通过名称进行硬编码分支。若提供商支持附加能力，通过类型守卫（如 `supportsOptions`）在运行时进行动态收窄。

---

## 6. Timeline & Milestones / 时间线与阶段目标

* **Phase 1 (Demo Day - June 12, 2026)**: Core futures prompts, Gamma Playbook alerts, and ingestion microservices operational on METRIXX-ILLINI.
* **Phase 2 (June 25, 2026)**: MIXX Composite Index, Futures Basis Synthesis, Macro/Rates models, and Mortgage Reference Service (MRS) integrations.

* **阶段 1 (演示日 - 2026 年 6 月 12 日)**: 核心期货提示词、做市商 GEX 警报系统及基础数据接入微服务在 METRIXX-ILLINI 投入运行。
* **阶段 2 (2026 年 6 月 25 日)**: 部署 MIXX 综合指数、期货基差合成报告、宏观利率模型及房地产 MRS 垂直服务集成。

---

## 7. Disclaimer / 免责声明

This repository is for educational and research documentation purposes only. It does not constitute investment advice, trading advice, or a recommendation to buy or sell any financial instrument.

本仓库仅用于教育、研究与项目文档记录目的，不构成投资建议、交易建议，也不构成任何金融工具的买入或卖出推荐。
