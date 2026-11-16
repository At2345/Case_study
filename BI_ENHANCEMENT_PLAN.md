# BI Dashboard Enhancement Plan: Technical Development Plan

Scope: a technical development plan for evolving the workbench **BI dashboards** page from a
cycle-reporting view into an enterprise-grade analytics platform. Every requirement below is stated
twice: as a **product requirement** with measurable acceptance criteria, and as a **technical
implementation** with concrete protocols, schemas, engine trade-offs, and verification gates.

---

## 0. Current Baseline in This Repository

The plan is written against the stack that exists today, so each phase can be scoped against a known
starting point.

| Layer | Current state | File / endpoint |
| --- | --- | --- |
| Mart | Read-only SQLite mart, 12 tables, rebuilt from cycle artifacts and the audit ledger, schema declared by contract | `backend/app/bi/export_mart.py` -> `var/bi_mart.db` |
| API | One call returns every chart dataset; admin-only rebuild | `GET /api/v1/bi/dashboard`, `POST /api/v1/bi/export` |
| Charts | Dependency-free SVG components (bar, line, donut, horizontal bars) | `frontend/src/components/charts.jsx` |
| Page | KPI cards, 9 chart panels, anomaly diagnostics, write-back queue, mart card, optional Superset link | `frontend/src/pages/BiPage.jsx` |
| Optional BI tool | Apache Superset 4.1.1, native venv, dashboard `sop-overview` | `superset/` |
| Tests | 24 page tests, 84 backend BI tests, 8 visual regression tests | `frontend/src/pages/BiPage.test.jsx`, `backend/tests/test_bi.py`, `frontend/e2e/bi-visual.spec.js` |

Gaps this plan closes: no semantic layer, no persona segmentation, no accessibility conformance, no
visual regression gate, no data observability, no multi-tenant isolation, no cost attribution.

---

## 1. System Architecture Overview & Core Engineering Constraints

An enterprise-grade BI dashboard requires a cohesive five-layer stack that connects strategic decision
logic to columnar storage, unified semantics, ergonomic UI components, and AI/FinOps governance. Because
no single database engine or BI tool satisfies all concurrency, latency, modeling, and customization
requirements simultaneously, the architecture is governed by three structural constraints.

1. **Semantic grounding over raw text-to-SQL.** Connecting large language models directly to raw database
   schemas produces severe accuracy degradation on enterprise databases, falling from 86% to 91% on tidy
   benchmarks such as Spider 1.0 down to 10% to 21% on real-world enterprise tasks in Spider 2.0, and
   generates syntactically valid queries that silently return false numbers. All conversational AI and
   automated diagnostics must execute through a governed semantic layer.
2. **Workload-segmented OLAP and transport.** No real-time OLAP engine simultaneously optimizes lowest
   cost per scanned terabyte, 100,000+ concurrent queries per second (QPS), and transactional row-level
   upserts. Storage, OLAP compute, caching, and zero-copy transport protocols must be matched to user
   concurrency and freshness Service Level Agreements (SLAs).
3. **Security, privacy, and FinOps by design.** Evolving dashboards from read-only reporting into
   bidirectional ("translytical") and code-managed ("BI-as-Code") applications requires embedding
   default-deny multi-tenant isolation, Zero Data Retention (ZDR) LLM inference, and query-tagged cloud
   cost attribution directly into the core pipeline.

---

## 2. Phase 1: Decision Intelligence & Product Requirements Framework

### PR-1.1 Causal KPI Driver Tree Modeling

#### Product requirements

- **Hierarchical decomposition.** Every dashboard module must anchor to a three-tier KPI Driver Tree:
  a **Root KPI** (annual strategic outcome such as Annual Recurring Revenue or Customer Lifetime Value),
  **Branch KPIs** (2 to 5 quarterly drivers that mathematically or causally explain the root, such as
  New ARR, Expansion ARR, and Churn), and **Leaf KPIs** (daily or weekly operational inputs directly
  controllable by frontline teams, such as setup time, first-pass yield, qualified leads, win rate, or
  onboarding completion rate).
- **Signal balance and scope cap.** The tree must blend Input, Output, Outcome, and Impact metrics with
  an emphasis on leading rather than purely lagging indicators. Following Gartner's five-per-driver
  ceiling, production trees are capped at 1 root, 3 to 5 branches, and 3 to 5 leaves per branch
  (13 to 31 total metrics) to avoid unmaintained 80-node hierarchies.
- **Accountability and exception triggers.** Every metric node must carry a single named owner, explicit
  confidence ratings, and defined target health corridors that govern when teams escalate,
  course-correct, or pause initiatives.
- **Applicability boundary.** When cause-and-effect relationships are unknown or the business is
  mid-pivot, replace fixed KPI trees with an experiment backlog or lead-indicator dashboard.

#### Technical implementation

- Run a 2-to-3-hour cross-functional diagnostic workshop to define Root, Branch, and Leaf KPIs, followed
  by a 48-hour baseline sprint confirming current values, update cadences, and data sources. Record
  missing data sources as explicit engineering backlog findings rather than blank rows.
- Automate leaf-to-root metric rollups directly from warehouse source systems; manual spreadsheet
  aggregation is prohibited because it causes structural decay by year two.
- Persist structural (causal edges and org chart), temporal (plan vs. actual), operational (RACI and
  approval workflows), and strategic (OKR) metadata alongside metrics.
- Validate branch-to-root causality against historical data, treating automated correlation discoveries
  as testable hypotheses rather than proof, and schedule mandatory tree reviews on the quarterly OKR
  cadence.

### PR-1.2 Persona-Segmented Workflows & SLA Matrix

#### Product requirements

The platform must serve four distinct user personas without forcing a single crowded view across
incompatible decision horizons and freshness needs.

#### Technical implementation

Implement dedicated views, delivery channels, and query-routing tiers per persona.

| User persona | Decision horizon | Freshness and latency SLA | Delivery and interaction pattern |
| --- | --- | --- | --- |
| Executive leadership (C-suite / VP) | Quarterly / annual | Weekly / monthly / quarterly briefings | Root and Branch KPI scorecards, enhanced metric cards, automated NLG summaries pushed to Slack, Microsoft Teams, email, and mobile |
| Operations and process leaders | Daily / weekly | At least daily; under 60 s or real-time for IoT/assembly telemetry | Leaf KPI health corridors, threshold and bottleneck alerts, translytical write-back forms, approval workflows |
| Business analysts and data scientists | Ad-hoc / multi-dimensional | Live warehouse queries or unified ML/BI lakehouse tables | Multi-faceted slicing, search-led exploration, automated key-driver and RCA decomposition, large virtualized data grids |
| External SaaS tenants (embedded BI) | Operational / periodic | Few-second ingestion visibility; p95 under 100 ms query latency at high QPS | Host-app SSO authentication, strict multi-tenant RLS isolation, pre-aggregated or cached serving endpoints |

---

## 3. Phase 2: Visual Ergonomics, UX Latency & Accessibility Standards

### PR-2.1 Cognitive Ergonomics & IBCS Standardization

#### Product requirements

Eliminate cross-departmental reporting ambiguity by enforcing International Business Communication
Standards (IBCS) across all three pillars: Conceptual (`SAY`, `STRUCTURE`), Perceptual (`EXPRESS`,
`CHECK`, `CONDENSE`, `SIMPLIFY`), and Semantic (`UNIFY`, aligned with ISO/AWI 24896), alongside cognitive
design principles (readability, short-term working memory minimization, Gestalt grouping) and
Tufte/Few visual integrity standards.

#### Technical implementation

- **Storyline and structure (`SAY` / `STRUCTURE`).** Enforce deductive, collectively exhaustive block
  hierarchies per view based on Barbara Minto's pyramid principles.
- **Component separation (`EXPRESS`).** Strictly separate data tables (unbiased, high-density exact
  lookup) from charts (biased, message-focused comparisons).
- **Chart and table layout rules (`SIMPLIFY` / `CONDENSE`).** On single-series line charts, embed the
  legend inside the chart title and label only peaks (above) and valleys (below) rather than every point.
  On horizontal bar charts, place positive value and sum labels to the right and negative labels to the
  left. In tables, left-align text columns, right-align numeric columns, fix identical widths per column
  type independent of header text length, abbreviate long row headers, and replace heavy vertical
  gridlines with light white vertical spacing. Increase information density with small multiples and
  intentional whitespace.
- **Semantic notation (`UNIFY`).** Bind standardized global design tokens for revenues, costs, profits,
  time periods, variance analyses, and scenario patterns: solid for actuals, outlined for plan/budget,
  hatched for forecasts.

### PR-2.2 Perceptual Latency & Wait-Time Tolerance

#### Product requirements

Interactive filtering, cross-highlighting, and drill-downs must return responses in under 400 ms to
preserve user flow and analytical productivity.

#### Technical implementation

Target sub-400 ms round-trip latency per the Doherty Threshold (replacing the legacy 2,000 ms standard)
using columnar pre-aggregations and client-side in-memory engines. Whenever heavy ad-hoc warehouse
queries exceed this threshold, display explicit progress bars to make wait times tolerable and preserve
user trust.

### PR-2.3 Universal Accessibility (WCAG 2.2 AA, EAA & Chartability)

#### Product requirements

Achieve full W3C WCAG 2.2 Level A and Level AA conformance, satisfying the European Accessibility Act
(EAA) / EN 301 549 enforceable since June 2025, and pass visualization-specific accessibility audits.

#### Technical implementation

- **Color and contrast.** Never rely on color alone to convey status or categories (WCAG 1.4.1, Level A);
  pair Okabe-Ito colorblind-safe palettes with textures, shapes, or direct labels. Enforce at least a
  4.5:1 contrast ratio for standard text and images of text, and 3:1 for large-scale text
  (WCAG 1.4.3, Level AA).
- **Zoom and focus.** Support 200% text resizing without loss of content or functionality
  (WCAG 1.4.4, Level AA). Ensure keyboard focus indicators use prominent high-contrast borders and are
  never obscured by sticky headers, modals, or tooltips (WCAG 2.4.11 Focus Not Obscured Minimum,
  Level AA).
- **Motor and pointer controls.** Provide single-click and keyboard alternatives for all dragging
  interactions such as range brushing, slider scrubbing, or column reordering (WCAG 2.5.7 Dragging
  Movements, Level AA). Enforce a minimum 24x24 CSS pixel target size for interactive controls
  (WCAG 2.5.8 Target Size Minimum, Level AA).
- **Verification.** Audit every visualization against the Chartability evaluation framework and execute
  manual assistive-technology testing with real screen-reader and keyboard-only users.

---

## 4. Phase 3: Backend Data Architecture, OLAP Engines & Transport Layer

### PR-3.1 Open Lakehouse Storage & Freshness-Tiered Ingestion

#### Product requirements

Store analytical datasets in open, interoperable table formats supporting concurrent BI, SQL, and ML
consumption with row-level deletes, lineage, and automated physical layout optimization, while aligning
ingestion complexity to latency SLAs.

#### Technical implementation

- **Table format.** Standardize on Parquet-backed **Apache Iceberg v3** (GA on Snowflake as of
  7 May 2026, supporting deletion vectors, row lineage, and `VARIANT` types) or **Delta Lake**, enabling
  **Liquid Clustering** to adapt physical data layout automatically to clustering keys without manual
  partition tuning. Leverage cross-format physical layer convergence (Snowflake March 2026 support for
  Delta-based Iceberg tables with deletion vectors and liquid clustering; Databricks Data+AI Summit
  June 2026 alignment), while accounting for separate metadata catalogs across engines.
- **Ingestion routing by freshness SLA.**
  - *Sub-second (under 1 s) latency:* stream events via Apache Kafka + Apache Flink.
  - *Near-real-time (1 to 60 s) latency:* SQL-based streaming via Snowflake Dynamic Tables, or unify
    batch and streaming declaratively in SQL/Python using Databricks Lakeflow pipelines.
  - *Minute-level latency:* Kafka Connect micro-batch commits to reduce small-file overhead compared to
    high-frequency Spark/Flink commits.
- **Anti-pattern guardrail.** Avoid direct "Zero Copy" Kafka-to-Iceberg topic writes when operational
  millisecond responsiveness, Kafka event replayability, or in-motion Bronze-to-Silver-to-Gold
  transformations are required.

### PR-3.2 Workload-Aligned Real-Time OLAP Serving Layer

#### Product requirements

Provision an analytical query engine capable of meeting p95 sub-second latency under target QPS, join
topology, and upsert workloads at minimal Total Cost of Ownership (TCO).

#### Technical implementation

Select the serving engine according to verified engineering trade-offs.

| OLAP engine | Architecture and ops overhead | Concurrency sweet spot | Latency and performance | Upsert and join support | Cost tradeoff |
| --- | --- | --- | --- | --- | --- |
| ClickHouse | Single-binary MergeTree; Cloud separates compute and storage (low-moderate ops) | Tens to low hundreds of concurrent queries | Vectorized SIMD scans billions of rows/s; tens to hundreds of ms p95 | Weak transactional upserts; broadcast-heavy multi-table joins | 10-20x compression; lowest steady-state cost per scanned TB (3-5x cheaper than general cloud warehouses) |
| Apache Pinot | Multi-service microservices plus Star-Tree cuboid indexes (high ops) | Tens of thousands to 100,000+ concurrent QPS | Sub-100 ms p95 even at 200,000+ QPS | Native real-time upserts; star-tree, range, inverted, geo, JSON indexes | Lowest cost per query at extreme QPS via pre-aggregation |
| Apache Druid | 6 distinct node service types, time-bucketed segments, DataSketches (high ops) | Hundreds of concurrent exploratory queries | Rapid response via segment pruning and ingestion-time rollups | Optimized primarily for append-only ingestion | Mid-tier compute cost; TCO dominated by operational staffing |
| StarRocks / Apache Doris | Vectorized MPP, optional separated compute and storage, native Iceberg/Delta/Hudi/Paimon (moderate ops) | Hundreds to low thousands of mixed BI queries | Sub-second queries; materialized views over external lakehouse tables | Native primary-key real-time upserts (StarRocks 4.1 adds Iceberg SQL `DELETE`); stronger star-schema joins than ClickHouse | Doris shares the lowest cost per scanned TB tier with ClickHouse |

**Architectural guardrail.** Do not expose ClickHouse directly to 50,000+ concurrent external SaaS users
without an upstream caching layer. Choose StarRocks when multi-table star-schema joins or frequent
primary-key upserts dominate, and Apache Pinot when powering high-QPS customer-facing analytics.

### PR-3.3 Zero-Copy Columnar Transport & Client-Side Acceleration

#### Product requirements

Eliminate row-by-row serialization overhead between columnar databases and BI servers or applications,
and provide sub-second browser-side filtering and data export.

#### Technical implementation

- **Wire protocol and client API.** Replace legacy row-oriented ODBC (1992) and JDBC (1997) drivers with
  **Apache Arrow Flight SQL** (gRPC server wire protocol returning parallel endpoint tickets via
  `GetFlightInfo` and `DoGet`) paired with **ADBC (Arrow Database Connectivity)** as the standardized
  client API across C, Go, Python, and Java. Utilize ADBC's canonical configuration options (`uri`,
  `username`, `password`) and built-in query cancellation.
- **Parallel fetch requirement.** Execute Flight SQL `DoGet` endpoint fetches across a client thread pool
  rather than a sequential loop so distributed executors stream partitions in parallel. In large
  transfers, ADBC/Flight delivers dramatic speedups, measured at 38x faster than ODBC in DuckDB TPC-H
  SF1 `lineitem` benchmarks (0.724 s vs. 28.149 s on Apple M1 Max) and 20x faster in Dremio Flight
  studies, with Apache Doris 2.1 reporting tens-of-times gains, whereas small 400-row lookups show
  minimal difference.
- **In-browser analytics.** Embed **DuckDB-Wasm** inside browser Web Workers to execute asynchronous SQL
  queries over HTTP-fetched Parquet, CSV, or Arrow buffers without blocking the main UI thread.

---

## 5. Phase 4: Centralized Semantic Layer & Analytical Data Modeling

### PR-4.1 Single Source of Truth & Multi-Platform Governance

#### Product requirements

Centralize all metric formulas, dimensions, join graphs, and time granularities so every BI tool, API,
spreadsheet, and AI agent computes identical results.

#### Technical implementation

Deploy the semantic layer pattern suited to the warehouse topology.

| Semantic platform | Multi-warehouse support | Caching and materialization | AI / agent and API exposure | Licensing and architectural tradeoffs |
| --- | --- | --- | --- | --- |
| dbt MetricFlow / Semantic Layer | Yes (Snowflake, BigQuery, Databricks, Redshift, Postgres, DuckDB) | Query caching gated to dbt Enterprise and Enterprise+ plans | `list_metrics` and `query_metrics` via dbt MCP server; GraphQL and JDBC over Arrow Flight SQL | MetricFlow engine is Apache 2.0 (v0.209.0, Oct 2025); hosted APIs and connectors require paid dbt plans metered per queried metric; agents can bypass via raw SQL if unrestricted |
| Cube | Yes (single deployment spans Snowflake, Redshift, Postgres, BigQuery) | Built-in pre-aggregations via Cube Store | Hosted MCP server on all plans; Postgres SQL, REST, GraphQL, Meta API; DAX/MDX (preview) on Enterprise | Cube Core is Apache 2.0; July 2026 dbt integration syncs one way only without importing metric formulas; metrics use Cube's language |
| Snowflake Semantic Views | No (Snowflake only) | Materializations GA as of 11 November 2026 | Managed Snowflake MCP server (GA) plus native Cortex Analyst / Cortex Agents | GA Aug 2025; SQL `FROM` querying added Mar 2026 but disallows window functions, `QUALIFY`, and joins |
| Databricks Metric Views | No (Databricks only) | Materialization currently in preview | Unity Catalog governance, AI/BI Genie, dashboards, and `MEASURE()` SQL function | GA April 2026; cannot join with other tables at query time; external BI compatibility mode in beta (Power BI publish path private preview) |
| Omni | Multi-warehouse plus two-way dbt Core/Cloud Git sync | Branch-based live testing promoted to shared model via Git review | AI harness compiles every query through the semantic model (95/100 internal score) | Imports MetricFlow metrics and pushes dbt exposures; no public list pricing |
| LookML (Looker) | Multiple SQL dialects (Snowflake, Databricks, BigQuery) | `derived_analytic_model` (public preview) manages BigQuery Graphs and Snowflake semantic views | Conversational Analytics GA; Looker MCP server in preview | No official dbt integration (duplicates logic); Open SQL Interface limited to BigQuery |
| AtScale | Yes (Snowflake, Databricks SQL, BigQuery) | Enterprise-scale acceleration for Excel (XMLA) and Power BI (DAX / Live Connection) | Dedicated MCP server | SML modeling language is Apache 2.0; commercial licensing priced per deployed semantic object |

**Interoperability standard tracking.** Evaluate **Apache Ossie** (formerly Open Semantic Interchange,
supported by 60+ contributors and inducted into the Apache Incubator in June 2026) only as an exchange
format while its specification remains at v0.1.1; do not use it as a primary production source of truth
yet.

### PR-4.2 Analytical Edge-Case Modeling Safeguards

#### Product requirements

Prevent double-counting across joins, erroneous aggregation of point-in-time metrics, misaligned fiscal
periods, and historical dimension corruption.

#### Technical implementation

- **Fan-out and chasm traps.** Enforce grain-aware metric compilation via MetricFlow semantic models or
  symmetric aggregates in LookML so one-to-many or many-to-many joins never inflate sums or counts.
- **Semi-additive metrics.** Configure `non_additive_dimension` parameters (for example on time
  dimensions for ARR or balance metrics) inside MetricFlow measures so aggregations pick period-end or
  boundary snapshots instead of summing across days.
- **Time spines and custom calendars.** Implement `time_spine_daily` / `time_spine_yearly` SQL models and
  register `custom_granularities` in `_models.yml` (supported in dbt 1.9+) to model fiscal calendars
  (for example October fiscal starts) or retail 4-4-5 and 4-5-4 calendars.
- **Slowly changing dimensions (SCD Type 2).** Track historical entity attributes using `dbt snapshot`
  (`strategy='timestamp'`, exposing `dbt_valid_from` and `dbt_valid_to`) or CDC pipelines (Databricks
  Change Data Feed, Google Cloud Datastream, Debezium), and pair every SCD2 model with downstream
  hard-delete propagation routines to honor GDPR customer opt-outs and deleted records.

---

## 6. Phase 5: AI-Native Analytics, Proactive Diagnostics & Actionable BI

### PR-5.1 Grounded Conversational Analytics & Anti-Hallucination Controls

#### Product requirements

Enable natural language querying while preventing silent SQL hallucinations and ensuring high
reliability across model tiers.

#### Technical implementation

- **Empirical grounding rule.** Prohibit raw schema text-to-SQL in production. An independent 2026
  evaluation (`arXiv:2604.25149`) across Claude Opus 4.7, Claude Sonnet 4.6, and GPT-5.4 on 99 questions
  over the Cleaned Contoso Retail dataset in ClickHouse demonstrated that raw schema access achieved only
  45.5% to 50.5% first-shot accuracy, whereas injecting a 4 KB semantic-layer markdown document lifted
  accuracy by +17 to +23 percentage points to 67.7% to 68.7% (paired exact McNemar $p \le 0.0015$). All
  three frontier models spanned only a 5-percentage-point spread within each tier, proving that upgrading
  LLM models cannot substitute for a semantic layer. Vendor evaluations report higher figures (dbt Labs
  83% internal and 84.1% to 100% on ACME Insurance, AtScale 92.5% TPC-DS, Databricks Genie 53% to 80% to
  100%, Snowflake Cortex Analyst 90%+, Omni 95/100); treat these as domain-specific upper bounds and
  validate against internal golden datasets.
- **Execution guardrails.** Connect AI agents exclusively to semantic MCP endpoints (dbt MCP, Cube MCP,
  Snowflake managed MCP, Superset 6.1 MCP) and block agent permissions from falling back to raw SQL
  generation. Address the approximately 30% residual error rate concentrated in percentiles,
  correlations, threshold rankings, and complex CTEs by enriching explicit named measures and locking
  complex business logic with **Verified Query Curation** (fixed SQL overrides).

### PR-5.2 Automated Anomaly Detection, Forecasting & Root Cause Analysis

#### Product requirements

Deliver proactive metric briefings, predictive time-series forecasts, and ranked driver decomposition
with verifiable human accountability.

#### Technical implementation

- **Proactive alerting and forecasting.** Configure automated KPI digests and outlier detection pushed to
  Slack, Teams, and email (Tableau Pulse / Proactive Intelligence, ThoughtSpot Monitor/SpotIQ, Tellius
  Inspector/Concierge, Power BI line-chart anomaly bounds). Integrate universal time-series forecasting
  models such as **Moirai Forecasting** (GA in Tableau Next) directly onto analytics canvases.
- **RCA verification protocol.** Treat every automated RCA output strictly as a hypothesis, never as
  proven causality. Enforce four validation checks before surfacing actions: verify temporal precedence
  (the proposed driver moved before the KPI), confirm arithmetic reconciliation of segment contributions
  to the total variance, preserve source-level user permissions on diagnostic evidence, and route the
  proposed cause to a named approver.
- **Calibration regression testing.** Automate single-question and batch calibration regression checks
  against golden benchmark suites (following Tableau Next's November 2026 Headless Auto Calibration MCP
  pattern) on every semantic model change.

### PR-5.3 Bidirectional Write-Back ("Translytical" Task Flows)

#### Product requirements

Allow operational users to execute what-if parameter overrides, annotate exceptions, and write validated
decisions back to operational stores directly from dashboard widgets.

#### Technical implementation

Implement write-back via **Power BI Translytical Task Flows** (linking report triggers to User Data
Functions and a backing SQL database), **Sigma Computing Input Tables** (live spreadsheet-style data
augmentation with database materialization), or authenticated transactional REST/GraphQL mutation
handlers in custom web apps.

---

## 7. Phase 6: Presentation Layer Stack & Frontend Engineering

### PR-6.1 BI Platform vs. Open-Source / BI-as-Code Selection

#### Product requirements

Match the frontend stack to governance needs, seat licensing economics, dbt workflow alignment, and
self-hosting capacity.

#### Technical implementation

Evaluate commercial platforms (Power BI, Tableau Next/Pulse, Sigma, Omni, ThoughtSpot, Looker) against
open-source and BI-as-Code options using verified feature boundaries.

| Open-source / BI-as-Code tool | Core workflow and strength | Free / OSS RLS support | AI and MCP support | Operational footprint and key caveat |
| --- | --- | --- | --- | --- |
| Apache Superset (v6.1.0, May 2026) | Apache 2.0 full-featured enterprise BI and SQL Lab IDE; SQLAlchemy connectivity | Yes (dataset RLS by user/group/role included free) | Opt-in MCP server with JWT/RBAC added in v6.1; no built-in OSS chat UI | Requires Python app, metadata DB, Redis, and Celery workers; steep chart-building curve for business users |
| Lightdash | Directly parses `dbt_project.yml` and YAML metrics for single-source-of-truth BI | Yes (`sql_filter` user attributes in dbt model included free) | AI analyst features restricted to commercial Lightdash Cloud | Total dbt dependency; self-hosting requires Node.js, Postgres, and a headless browser |
| Evidence.dev | BI-as-Code: Markdown plus embedded SQL reviewed in Git PRs and compiled to fast data apps | No (RLS reserved for Enterprise tier) | No native AI analyst | Publishing model for polished narrative reports, not ad-hoc self-service exploration |
| Metabase | Single-JAR/Docker setup with best-in-class visual query builder for non-SQL users | No (row/column permissions and SSO gated to Pro/Enterprise) | Metabase AI gated to commercial tiers | dbt integration imports metadata only, not metric calculations (high metric drift risk); memory-heavy on huge warehouses |

#### Decision record

**Decision.** Keep the bespoke React workbench as the primary surface, and keep Apache Superset as the
embedded exploration surface behind it. Do not adopt a commercial platform, and do not replace the
workbench with a BI-as-Code tool.

**Why the workbench stays primary.** The decision hinges on where the metric logic lives. This repo
already has a governed semantic layer (`backend/app/bi/semantics.py`) that compiles 18 metric
definitions to SQL from one place, and every consumer — dashboard, KPI tree, diagnostics, grounded ask —
reads through it. A BI tool that imports dbt metadata without importing metric *calculations* would
create a second definition of every metric, which is the drift the semantic layer exists to prevent.
The workbench is the only surface that can call the registry directly.

**Why Superset is the embedded surface.** It is the only option in the table whose row-level security is
included in the Apache 2.0 build rather than gated to a commercial tier, and it reads the mart over
SQLAlchemy without a translation layer. Its cost is operational: a Python app, a metadata database,
Redis and Celery workers. That cost is already paid here (`docker-compose.superset.yml`), and it is
bounded because Superset is read-only against the mart.

**Why the others were not selected.**

| Option | Disqualifying boundary for this deployment |
| --- | --- |
| Power BI / Tableau Next / Sigma / Omni / ThoughtSpot / Looker | Per-seat licensing against an internal planning team, and metric logic would live in the vendor's semantic model rather than in the registry this repo already governs |
| Lightdash | Total dbt dependency; there is no dbt project here, and adopting one to get a BI tool inverts the dependency |
| Evidence.dev | No free RLS, so it cannot serve the multi-tenant requirement (PR-8.1) at all |
| Metabase | RLS and SSO are gated to Pro/Enterprise, and its dbt integration imports metadata only — the same metric-drift risk, without the free RLS that justifies Superset |

**What would reverse this.** A commercial platform becomes the right answer if the team grows past the
point where per-seat cost is cheaper than the workbench's maintenance, or if governed self-service
exploration by non-SQL users becomes the dominant workload rather than a secondary one. Lightdash
becomes viable if a dbt project is adopted for transformation reasons independent of BI.

**What this decision does not settle.** It is a stack decision, not a migration: no commercial platform
was trialled, no licensing quote was obtained, and the feature boundaries above are taken from vendor
documentation rather than from a hands-on evaluation. The sign-off the requirement asks for is a
business decision that this record informs but cannot make.

### PR-6.2 Bespoke Full-Stack Visualization & Data Grid Components

#### Product requirements

For custom React/TypeScript embedded or SaaS analytics applications, render complex dashboards smoothly
across standard charts, dense telemetry (over 100,000 points), and massive tabular grids
(300,000 to 1,000,000 rows).

#### Technical implementation

- **Charting tier.**
  - *Standard enterprise dashboards (thousands of points, geo maps):* tree-shaken **Apache ECharts**
    (around 300 KB full bundle).
  - *Custom design-system and lightweight visuals:* **Visx** (modular React + D3 primitives down to
    15 KB) or declarative **Vega-Lite**.
  - *Dense telemetry (over 100,000 points):* switch from DOM/SVG/2D canvas to GPU-accelerated **WebGL**
    (deck.gl or dedicated WebGL charting engines; note that ECharts' GL extension can feel bolted-on
    rather than core).
- **Data grid tier.**
  - *Custom Tailwind and design-system tables:* headless **TanStack Table v9** (released August 2026;
    MIT license; starts around 5 KB tree-shaken, 14.6 KB full headless core, approximately 30 KB with UI
    and virtualization).
  - *Excel-parity enterprise grids (pivoting, row grouping, inline editing, clipboard):* **AG Grid**
    (Community 338 KB MIT; Enterprise 665.4 KB starting around $999/developer/year) configured with its
    **Server-Side Row Model** (fetching 50 to 100 row windows per scroll request).
- **Scale constraint guardrail.** Do not rely on client-side DOM virtualization with **MUI X Data Grid
  (MIT)** or **TanStack Table + TanStack Virtual** for 1,000,000-row client grids: reproducible
  benchmarking by Infinite Table demonstrated that both hit browser scroll-height limits and cannot
  scroll past row 838,860 at 1M rows, whereas **AG Grid** and **Infinite Table** reach the final row. In
  those same tests (evaluated with vendor-benchmark awareness), Infinite Table used 27.2 MB memory at
  300k rows (46% lower than AG Grid, 2.37x lower than TanStack) and settled in 144 ms at 1M rows (42%
  faster than AG Grid), while AG Grid achieved 17% lower jump-to-row paint time than Infinite Table's
  24.1 ms (the simplified TanStack example painted in 7.18 ms).

---

## 8. Phase 7: Software Engineering, CI/CD, Testing & Data Observability

### PR-7.1 GitOps, Data Contracts & Virtualized CI/CD

#### Product requirements

Enforce software-grade peer review, schema contract gates, and isolated environment testing for every
metric, SQL model, and dashboard artifact without duplicating petabyte production tables.

#### Technical implementation

- **Version control everything.** Commit all dbt SQL/YAML, MetricFlow specs, Evidence Markdown/SQL, and
  Power BI **PBIP / TMDL** scripts (edited in VS Code) to Git.
- **Incremental and virtualized CI.** Configure **dbt Slim CI** (`state:modified+`, `--defer`, ephemeral
  per-PR schemas, and automated post-merge cleanup) or adopt **SQLMesh Virtual Data Environments**, which
  track state and pointers to unchanged production models to achieve roughly 9x faster execution and
  lower compute cost.
- **Contracts and unit tests.** Enforce **dbt model contracts** (requiring explicit `name` and
  `data_type` per column) in PR CI pipelines to fail breaking schema changes before downstream
  dashboards break, paired with **dbt unit tests** for complex transformation edge cases.

### PR-7.2 Automated Visual Regression & BI Artifact Validation

#### Product requirements

Automatically detect visual layout breakage, clipped axis labels, CSS regressions, and report metric
mismatches before merging to production.

#### Technical implementation

- **Playwright visual regression suite.** Implement `await expect(locator).toHaveScreenshot()` targeting
  isolated component locators rather than brittle full-page captures. Execute visual checks exclusively
  inside version-pinned Playwright Docker containers matching `package.json` so OS font and anti-aliasing
  differences never drift between local and CI runs.
- **Determinism controls.** Globally configure `animations: 'disabled'`, freeze dates with `page.clock`,
  stub API payloads via `page.route()`, apply `mask` arrays to volatile freshness timestamps and avatars,
  tune small anti-aliasing budgets (`maxDiffPixelRatio`, `threshold`), and mandate human code review of
  diff images before approving `--update-snapshots` (optionally pairing native pixelmatch diffs with
  multimodal LLM triage to filter cosmetic noise).
- **BI tool validation.** For Power BI deployments, integrate automated report and data regression checks
  using tools such as **BI Validator**.

### PR-7.3 Runtime Data Observability & UI Trust Badges

#### Product requirements

Alert engineering teams immediately upon upstream pipeline failures, schema drift, or volume anomalies,
and expose dataset freshness and lineage transparency directly to dashboard consumers.

#### Technical implementation

Deploy **Elementary** (OSS dbt package plus CLI for test tracking and Slack/Teams alerting, or Elementary
Cloud for ML-based freshness and volume anomaly detection, column-level lineage from ingestion to BI, and
catalog metadata). Render real-time freshness SLA badges and certification indicators on dashboard
headers, while excluding these dynamic timestamp elements via Playwright `mask` rules during CI runs.

---

## 9. Phase 8: Security, Multi-Tenant Isolation, AI Privacy & BI FinOps

### PR-8.1 Default-Deny Multi-Tenant Security Architecture

#### Product requirements

Guarantee zero cross-tenant data leakage across shared analytical tables, semantic APIs, caches, and
embedded dashboards under a strict default-deny security model.

#### Technical implementation

- **Trusted authentication context.** Authenticate external users via host-application SSO and derive
  tenant identities exclusively from trusted server sessions, never from client request parameters or URL
  parameters.
- **Engine-specific RLS / OLS implementation.**
  - *ClickHouse:* define `CREATE ROW POLICY ... USING tenant_id = getSetting('SQL_tenant_id')` bound to
    read-only roles with `SETTINGS SQL_tenant_id CHANGEABLE_IN_READONLY`.
  - *Cube:* embed tenant claims inside signed JWTs, enforce tenant filters inside `queryRewrite` and
    `COMPILE_CONTEXT`, and ensure `/meta` responses are tenant-scoped and never publicly cached.
  - *Power BI / Databricks:* implement Power BI RLS (row filtering) separately from Object-Level
    Security (OLS, hiding tables and columns) alongside per-tenant workspaces and service principals; in
    Databricks AI/BI dashboards, combine group-based RLS, dynamic masking, and default-deny rules.
  - *Apache Superset embedded guardrails:* when using guest tokens, enforce these mandatory controls:
    enable `EMBEDDED_SUPERSET` (required in Superset 5.0+); attach an explicit integer `dataset` ID to
    every `rls_rules` item so clauses never fail globally; keep `tenant_id` in the output grain of every
    virtual dataset; disable guest role access to SQL Lab and standalone chart APIs; keep guest token
    lifetimes short (5-minute default); include RLS clauses in query cache keys; never interpolate
    `url_param` into Jinja RLS templates; audit against known regressions (`GLOBAL_ASYNC_QUERIES`
    bypassing guest RLS in 5.0.0; virtual dataset double-RLS issue #37359); and do not rely on Superset
    semantic views for guest-token RLS.

#### Implementation in this repo

`backend/app/bi/tenancy.py` installs the filter once per connection rather than trusting each query to
remember a predicate. The mart is a single SQLite file shared by every tenant and SQLite has no
row-level security, so a scoped connection shadows each mart table with a `TEMP VIEW` of the same name
that selects only one tenant's rows; SQLite resolves an unqualified name to the temp schema before
`main`, so the existing SQL in `semantics`, `anomaly`, `kpi_tree` and `bi` is unchanged and cannot
reach another tenant by forgetting a `WHERE`. Two properties make it fail closed: a table with no
`tenant_id` column is refused outright instead of being served unscoped, and the views are read-only,
so the mart cannot be written through a tenant's read path. Tenant ids are validated against
`^[a-z0-9][a-z0-9_-]{0,63}$` before being interpolated into the view definition, and case is
significant, so `Acme` cannot alias `acme`.

The tenant comes only from the verified session: `UserStore` records it, `make_token` puts it in the
JWT, `decode_token` requires the claim, and `current_user` returns it. No endpoint reads a tenant from
a query parameter, header or path segment. `tenant_id` is prepended to every table by
`contracts._cols`, so a table cannot be declared without one, and `build_mart` replaces one tenant's
rows with a scoped `DELETE` plus append instead of dropping the file, so a second tenant's build
leaves the first tenant's rows intact. The dashboard cache is keyed by tenant — keying only on the
file would serve the first caller's payload to everyone else, a leak no query-level filter can catch.
The write-back log gains a `tenant_id` column and every read is filtered, while the hash chain stays
global; rows written before this change are bound to the default tenant and still verify, because the
exemption is conditional on that binding.

### PR-8.2 AI Security & Regulatory Compliance (GDPR, SOC 2, EU AI Act, HIPAA)

#### Product requirements

Prevent sensitive PII or corporate secrets from leaking into external LLM providers, logs, or
prompt-injected agent outputs.

#### Technical implementation

Mitigate **OWASP LLM01 (Prompt Injection)** and **OWASP LLM02 (Sensitive Data Leakage)** by masking and
tokenizing sensitive data before payloads reach the prompt or development layer. Route all LLM inference
through **Zero Data Retention (ZDR)** endpoints compliant with GDPR data minimization, EU AI Act, HIPAA,
and SOC 2 frameworks.

#### Implementation in this repo

`backend/app/ai/privacy.py` redacts at one choke point instead of at each call site. `client_from_env()`
wraps whatever provider stack it builds — a single client or the `FailoverClient` — in a `PrivacyClient`,
so the narrative, grounded-ask and assistant paths are covered by construction and a new call site cannot
leak by forgetting to mask. The reconnaissance that set the scope: `narrative/telemetry.py` already
whitelists its fields and drops identifiers, so the only free text in a narrative prompt is
`prior_decisions[].comment`; the assistant prompt is the broad one, carrying the context JSON, retrieval
snippets, MCP evidence, the last eight turns of history and the question itself.

Seven rules run in a fixed order — email, IBAN, payment card, NI number, UK phone (national and `+44`
form), UK postcode, IPv4 — and a value is claimed by the first rule that matches it. A value is replaced
by a stable placeholder (`[[EMAIL_1]]`) for the duration of one request, so the model can still tell that
two mentions are the same person, and `restore()` puts the original back before the planner sees it.
Streaming is restored through a `StreamRestorer` that holds back an unterminated placeholder, because a
provider may split `[[EMA` and `IL_1]]` across chunks. Findings are recorded as counts per kind and never
as values: a redaction log that quotes what it redacted is not a redaction log.

ZDR is a declaration, not an attestation. `AI_ZDR_PROVIDERS` names the providers whose zero-retention
terms were verified out of band, and `AI_REQUIRE_ZDR` makes an undeclared provider a refusal rather than a
warning; the refused provider is recorded in `PROVIDER_STATUS` and, if nothing is left, `client_from_env()`
returns `None`, so no external call is made and the deterministic text is used. `GET /api/v1/ai/privacy`
reports the active rules, the declaration, the counters and the limitations, and its `zdr.attested` is
`False` unless every configured provider is declared — which is the honest answer with nothing configured.

The same redaction covers the paths that never touch a prompt: `FailoverClient._note` stores a provider's
error message and `/api/v1/assistant/providers` serves that registry to the browser, so a 400 that echoes
the request body would leak through the status endpoint. `scrub()` redacts those messages, and the
narrator's failure log, with a throwaway map that is never restored.

### PR-8.3 BI FinOps & Sprawl Lifecycle Governance

#### Product requirements

Attribute 100% of analytical compute spend to owning teams and workloads, cap runaway AI agent query
expenses, and prevent dashboard and metric sprawl.

#### Technical implementation

- **Query tagging and attribution.** Enforce a standardized query tag taxonomy (`team`, `pipeline`, `env`,
  `workload:bi`) on every BI and AI agent query. Build a semantic cost intelligence dashboard over
  `ACCOUNT_USAGE` (`QUERY_HISTORY`, `WAREHOUSE_METERING_HISTORY`) for credit attribution, storage
  intelligence, and ML spend anomaly alerting.
- **Spend governance thresholds.** Trigger mandatory query attribution at roughly $15,000/month cloud
  warehouse spend, evaluate 1-to-3-year capacity pre-purchases (15% to 25% discount) at $250,000/year, and
  introduce paid third-party FinOps observability tools only above $30,000/month when internal
  `ACCOUNT_USAGE` expertise is insufficient.
- **Agent cost and sprawl controls.** Audit compiled SQL generated by semantic layers for top dashboards
  prior to production launch. Apply caching and rate quotas before connecting continuous AI agents to
  per-metric metered APIs (such as dbt Semantic Layer). Instrument adoption telemetry modeled on Tableau
  Pulse Enhanced Usage Tracking to archive unused dashboards and prune uncertified metrics quarterly.

#### Implementation in this repo

There is no cloud warehouse here, so `ACCOUNT_USAGE` has no analogue and no dollar figure can be
computed. What can be measured is measured. `backend/app/bi/finops.py` defines the tag taxonomy as a
`QueryTag` of team, pipeline, environment and workload, and makes it a **required argument of
`semantics.evaluate`** — a metric query that cannot be attributed to a team, a pipeline and a workload is
a query nobody is paying for, so it cannot be issued. That is the same default-deny shape as the tenant
filter and the prompt redactor: enforcement at one choke point rather than at each call site. The team
comes from the role through a declared map, and a role with no declared team is refused rather than filed
under a catch-all bucket that would hide it.

Every tag is appended to `var/finops.db` in two kinds. A `metric` row carries the metric ids and the
number of SQL statements the evaluation actually issued; a `request` row carries the wall time of a BI
read surface that does not go through the registry (dashboard, diagnostics, contracts, status, FinOps).
Keeping the kinds apart is what stops the report double-counting a request and the statements inside it.
`record()` logs and continues on failure: attribution is observability, and observability must never fail
the query it is measuring.

Storage intelligence reads the mart read-only and reports the file size plus per-table row counts. Per-table
bytes come from `dbstat` when that compile-time option is present and are `null` when it is not, rather than
being estimated from a proportional guess. The three spend tiers from the plan are declared with their
obligations, and `active` and `measured_usd` are both `null` with a stated reason — inventing a cost model
would be the one thing this increment must not do. Sprawl comes from the same ledger: a metric with no
evaluation in the window is a prune candidate, and a read surface nobody called is an archive candidate.
`check_quota` caps AI calls per tenant per window and `POST /api/v1/bi/ask` returns 429 when the allowance
is spent; `AI_ASK_QUOTA` and `AI_ASK_WINDOW_S` set it, and it is off by default because there is no metered
provider cost here to protect.

`GET /api/v1/bi/finops` returns attribution, storage, tiers and sprawl in one call, and the Analyst persona
renders them in a FinOps panel.

---

## 10. Phased Implementation & Verification Roadmap

| Stage | Weeks | Deliverables | Verification gate |
| --- | --- | --- | --- |
| 1. Foundation, contracts, storage | 1-4 | KPI Driver Tree workshop (13-31 metrics, owners, health corridors) and 48-hour data-source audit; Parquet/Iceberg v3 or Delta Lake tables with Liquid Clustering; freshness-tiered ingestion (Kafka/Flink, Dynamic Tables, or Lakeflow); dbt/SQLMesh GitOps with model contracts and unit tests; Elementary pipeline monitoring | Tree review signed off; contract tests fail on breaking schema change; Elementary alerts fire on a seeded anomaly |
| 2. Semantic layer and high-speed serving | 5-8 | Centralized semantic layer (dbt MetricFlow, Cube, Omni, or platform-native Snowflake/Databricks views) with semi-additive rules, `time_spine` fiscal calendars, and SCD Type 2 snapshots; workload-matched OLAP engine (ClickHouse, StarRocks/Doris, or Pinot); ADBC plus parallel Arrow Flight SQL transport; DuckDB-Wasm Web Workers for local browser interactivity | Golden-query suite returns identical results across BI tool, API, and agent; p95 latency meets the persona SLA matrix |
| 3. Accessible UI, translytical flows, AI grounding | 9-12 | Persona-tailored views enforcing IBCS SUCCESS notation, Okabe-Ito palettes, WCAG 2.2 AA compliance (4.5:1 contrast, 24x24 CSS px targets, keyboard and non-drag alternatives), Chartability audits, sub-400 ms Doherty interaction pacing; ECharts/WebGL and virtualized grids (AG Grid Server-Side Row Model or Infinite Table for 300k-1M row tables); translytical write-back flows; semantic-MCP-grounded AI agents blocking raw SQL bypass; Moirai forecasting; hypothesis-gated RCA | Accessibility audit passes AA; interaction latency under 400 ms; agent accuracy measured against the internal golden dataset; RCA outputs routed to a named approver |
| 4. Security hardening, visual CI, FinOps launch | 13-16 | Default-deny RLS/OLS across database, semantic layer, and caches (including Superset guest-token safeguards if embedded); pre-prompt PII masking and tokenization with ZDR LLM inference; Docker-pinned Playwright visual regression and golden-query AI regression gates; `ACCOUNT_USAGE` FinOps query-tag dashboards with quarterly adoption telemetry reviews | Cross-tenant penetration test returns zero leakage; visual regression suite blocks a seeded layout regression; 100% of queries carry a cost tag |

---

## 11. Requirement Index

| ID | Area | Primary verification gate |
| --- | --- | --- |
| PR-1.1 | Causal KPI driver tree | Tree review sign-off; automated leaf-to-root rollup reconciliation |
| PR-1.2 | Persona workflows and SLA matrix | Per-persona latency SLA measured in load test |
| PR-2.1 | IBCS standardization | Design-token lint plus visual review checklist |
| PR-2.2 | Perceptual latency | p95 interaction round-trip under 400 ms |
| PR-2.3 | Accessibility | WCAG 2.2 AA audit plus Chartability review |
| PR-3.1 | Lakehouse storage and ingestion | Freshness SLA per tier; no small-file regression |
| PR-3.2 | OLAP serving layer | p95 latency under target QPS; TCO per scanned TB |
| PR-3.3 | Columnar transport | Large-transfer benchmark versus ODBC baseline |
| PR-4.1 | Semantic layer | Golden-query parity across consumers |
| PR-4.2 | Edge-case modeling | Fan-out, semi-additive, fiscal calendar, and SCD2 unit tests |
| PR-5.1 | Grounded conversational analytics | Golden-dataset accuracy; zero raw-SQL agent fallback |
| PR-5.2 | Anomaly detection and RCA | Four-check RCA protocol enforced before action |
| PR-5.3 | Write-back flows | Authenticated mutation test with audit trail |
| PR-6.1 | BI platform selection | Feature-boundary matrix signed off |
| PR-6.2 | Visualization and grid components | 1M-row scroll test reaches the final row |
| PR-7.1 | GitOps and CI | Contract and unit tests gate every PR |
| PR-7.2 | Visual regression | Docker-pinned screenshot suite blocks layout drift |
| PR-7.3 | Data observability | Freshness badge reflects the latest pipeline run |
| PR-8.1 | Multi-tenant isolation | Cross-tenant leakage test returns zero rows |
| PR-8.2 | AI privacy and compliance | PII masking verified pre-prompt; ZDR endpoint attested |
| PR-8.3 | FinOps governance | 100% query cost attribution; quarterly sprawl review |

---

## 12. Implementation Status

Increments delivered against this plan, newest first.

| Requirement | Status | What shipped | Verification |
| --- | --- | --- | --- |
| PR-8.3 FinOps and sprawl | Partial | `backend/app/bi/finops.py`: the tag taxonomy is a `QueryTag` of team, pipeline, environment and workload, and it is a required argument of `semantics.evaluate`, so a metric query cannot be issued untagged — the team comes from the role through a declared map and a role with no team is refused; every tag is appended to `var/finops.db` as either a `metric` row (metric ids plus the statements actually issued) or a `request` row (wall time of a surface that does not go through the registry), and the kinds are kept apart so nothing is double-counted; `record()` logs and continues on failure because attribution must never fail the query it measures; storage intelligence reports the mart file size and per-table row counts, with per-table bytes from `dbstat` or `null` when that option is absent; the three spend tiers are declared with their obligations and `active`/`measured_usd` are `null` with a stated reason, because there is no warehouse to price; sprawl derives prune candidates from metrics with no evaluation in the window and archive candidates from read surfaces nobody called; `check_quota` caps AI calls per tenant per window and `POST /api/v1/bi/ask` returns 429 when spent; `GET /api/v1/bi/finops` returns all four sections and the Analyst persona renders a FinOps panel | `test_finops.py` (20); `test_bi.py::TestBiFinOpsApi` (3); `BiPage.test.jsx` FinOps tests (4) |
| PR-8.2 AI privacy | Partial | `backend/app/ai/privacy.py`: redaction runs at one choke point — `client_from_env()` wraps the whole provider stack in a `PrivacyClient`, so the narrative, grounded-ask and assistant paths are covered by construction and a new call site cannot leak by forgetting to mask; seven ordered rules (email, IBAN, payment card, NI number, UK phone in national and `+44` form, UK postcode, IPv4) replace a value with a stable per-request placeholder (`[[EMAIL_1]]`) so the model can still tell two mentions are one person, and `restore()` puts the original back before the planner sees it; `StreamRestorer` holds back an unterminated placeholder so a chunk boundary cannot break the restore; findings are counts per kind, never values; `AI_ZDR_PROVIDERS` declares which providers have verified zero-retention terms and `AI_REQUIRE_ZDR` turns an undeclared provider into a refusal recorded in `PROVIDER_STATUS`, returning `None` rather than calling out when nothing is left; `scrub()` redacts provider error messages before they reach `PROVIDER_STATUS` (served to the browser by `/api/v1/assistant/providers`) and the narrator's failure log; `GET /api/v1/ai/privacy` reports the rules, the declaration, the counters and the limitations, with `zdr.attested` false unless every configured provider is declared | `test_ai_privacy.py` (33); `test_extended_api.py` privacy-endpoint assertions |
| PR-8.1 Multi-tenant isolation | Partial | `backend/app/bi/tenancy.py`: the tenant filter is installed once per connection as a `TEMP VIEW` that shadows each mart table by name, so every existing query keeps its SQL and still sees one tenant's rows — a query cannot leak by forgetting a predicate because there is no predicate to forget; a table with no `tenant_id` column is refused rather than served unscoped, and the views are read-only, so the mart cannot be written through a read path; tenant ids are validated against a strict character set before being interpolated into the view DDL, and case is significant; the tenant is derived only from the verified session (`UserStore` → JWT claim → `current_user`), never from a request parameter; `contracts._cols` prepends `tenant_id` to every table so one cannot be declared without it; `build_mart` replaces one tenant's rows with a scoped `DELETE` plus append instead of dropping the file; the dashboard cache is keyed by tenant; the write-back log gains a `tenant_id` column with every read filtered, and pre-tenancy rows are bound to the default tenant and still verify | `test_bi.py::TestTenantIsolation` (11), `TestTenantSession` (4); `test_auth.py` tenant-claim tests |
| PR-7.2 Visual regression | Partial | `frontend/e2e/bi-visual.spec.js`: 8 Playwright tests capturing 18 panel-level baselines — `toHaveScreenshot()` on isolated locators, never a full-page capture, so a failure names the panel that moved instead of producing an unreadable diff; determinism is enforced rather than hoped for: one browser build, one viewport, one device pixel ratio, `locale: en-GB`, `timezoneId: Europe/London`, `reducedMotion: reduce`, `animations: disabled`, `caret: hide`, `scale: css`, a frozen clock via `page.clock.setFixedTime`, and every API call stubbed through `page.route()` so no screenshot depends on whatever is in `var/bi_mart.db`; an unstubbed endpoint fails the test rather than silently rendering an empty panel; the one host-dependent timestamp is masked; `Dockerfile.playwright` pins `mcr.microsoft.com/playwright:v1.55.1-noble` to the exact `@playwright/test` version so the font stack and anti-aliasing match CI | `npm run test:visual` 8 passed, twice in a row against the committed baselines; negative control: changing `IBCS.actual` failed `plan-vs-baseline.png` with 3978 differing pixels (ratio 0.01) and left the other four panels green |
| PR-7.1 Data contracts and CI gate | Partial | `backend/app/bi/contracts.py`: 12 tables and 144 columns declared with storage type, nullability, grain and the dashboard panels that read them; the contract is the schema authority, so `build_mart` creates each table from the declared types and appends instead of letting pandas infer — which fixes a real defect where an empty frame produced an all-TEXT table and a column's type flipped the moment the first row arrived; a frame that cannot satisfy the contract fails before the file is touched, naming the consumers that would break; `assert_conforms` runs at the end of every build and raises `ContractError`, which `POST /api/v1/bi/export` returns as a 409; `GET /api/v1/bi/contracts` returns the declared contract plus a live conformance report; the mart panel shows a conformance badge | `test_bi.py::TestMartContracts` (14), `TestBiContractsApi` (3); `BiPage.test.jsx` "shows the mart contract conformance", "flags contract drift" |
| PR-5.3 Write-back flows | Partial | `backend/app/bi/writeback.py`: an append-only SQLite log with the same SHA-256 hash chain as the decision ledger, holding annotations and action events; every mutation is authenticated, role-gated, validated, idempotent (a repeated key returns the original row instead of writing a second) and attributed to a person, an IP and a session; an action's state is derived from its latest event, so nothing is updated in place; `POST /api/v1/bi/actions` refuses to propose an action on a cause that failed any of the four RCA checks, which is what makes the write validated; `POST /api/v1/bi/actions/{id}/decide` requires the caller's `expected_state` and returns 409 on a stale read; `GET /api/v1/bi/writeback/verify` recomputes the chain; the Operations and Analyst personas carry a proposed-actions queue and an annotation form | `test_bi.py::TestWriteBackStore`, `TestBiWriteBack`; `BiPage.test.jsx` "proposes an action from an actionable cause", "approves a pending action with its expected state", "annotates an exception", "gives a read-only auditor no write controls", "surfaces a refused proposal" |
| PR-5.2 Anomaly detection and RCA | Partial | `backend/app/bi/anomaly.py`: Iglewicz-Hoaglin modified z-score screening (median and MAD, threshold 3.5) over two monthly series, a linear-trend projection with a residual band, and segment decomposition by channel; every cause is gated by the four-check protocol — temporal precedence, arithmetic reconciliation, source-level permission on the evidence grain, and a named approver — and only a cause passing all four is marked actionable, the rest returned as hypotheses with the failed checks named; `GET /api/v1/bi/diagnostics`; a Diagnostics panel on the Operations and Analyst personas | `test_bi.py::TestAnomalyDetection`, `TestRcaProtocol`, `TestBiDiagnostics`; `BiPage.test.jsx` "renders anomaly diagnostics with the four RCA checks" and "downgrades a cause to a hypothesis when a check fails" |
| PR-1.1 KPI driver tree | Partial | `backend/app/bi/kpi_tree.py` plus `GET /api/v1/bi/kpi-tree` and `KpiTree.jsx`: 1 root, 4 branches, 13 leaves, each node carrying one owner, a confidence rating and a target corridor; health evaluated as healthy / watch / breach | `test_bi.py::TestBiKpiTree`; `BiPage.test.jsx` "renders the KPI driver tree" |
| PR-1.2 Persona workflows | Partial | Executive / Operations / Analyst tabs on the BI page; each persona renders only its own panels. The page is a declared layout, not a flat list: `LAYOUT` in `BiPage.jsx` gives each persona an ordered set of named sections — Performance, Drivers, Diagnostics, Actions, Detail, Governance — and a panel registry renders them, so the visualisations come first and the governance surfaces sit last. Each fact has one home: the scorecard carries three outcome KPIs, the triage donut owns the four state counts, the header owns the mart timestamp, and the metric catalog is a definition registry rather than a second scorecard | `BiPage.test.jsx` "segments the dashboard by persona", "puts the visualisations before the detail and governance surfaces", "does not repeat the triage counts in the scorecard" |
| PR-2.1 IBCS standardization | Partial | `frontend/src/lib/ibcs.js` semantic tokens; plan = dashed, forecast = dotted, actual = solid; peak/valley-only labels; diverging horizontal bars with negative labels on the left; line-style legend swatches | Visual review; `charts.jsx` |
| PR-2.2 Perceptual latency | Partial | `duration_ms` and a `cached` flag measured server-side and shown as a badge; the dashboard aggregation is cached until the mart file changes | Cold 1.8 s, then 0.2 ms cached; warm uncached calls measured at 289-391 ms against the 400 ms target |
| PR-2.3 Accessibility | Partial | `role="img"` plus `aria-label` and `<title>` on every chart; `sr-only` data table per chart; secondary text raised from slate-500 (3.9:1) to slate-400 (7.4:1); 32 px persona tab targets; `role="tablist"` / `aria-selected` | `BiPage.test.jsx`; manual contrast check |
| PR-4.1 Semantic layer | Partial | `backend/app/bi/semantics.py`: 18 governed metric definitions, each with owner, unit, grain, aggregation, version and description, compiled to SQL from one place; `GET /api/v1/bi/metrics`; the KPI tree now reads from the registry instead of its own hand-written SQL; the Analyst persona shows the catalog | `test_bi.py::TestBiMetrics::test_golden_query_parity` |
| PR-4.2 Edge-case modeling | Partial | Grain and `semi_additive` declared per metric; `add_across_time()` refuses to sum a point-in-time metric across periods; `combine()` refuses cross-grain combinations that would fan out | `test_bi.py::TestMetricGuards` |
| PR-5.1 Grounded conversational analytics | Partial | `backend/app/bi/ask.py`: the model receives only the semantic document built from the registry and may return only metric ids, so no code path turns model output into SQL; `parse_selection()` rejects any reply containing SQL and reports unknown ids instead of dropping them silently; `POST /api/v1/bi/ask` evaluates the accepted ids through the registry; the Analyst persona has an ask panel; an 8-question golden set scores the model | `test_bi.py::TestGroundedAsk`; golden set scored 8/8 against the live model |
| PR-7.3 Data observability | Partial | Mart freshness, cycle cutoff and query latency badges on the dashboard header | `BiPage.test.jsx` "shows freshness, cycle cutoff and latency badges" |
| PR-3.1 Freshness-tiered ingestion | Partial | `backend/app/bi/serving.py`: each mart table declares a freshness tier and the tier carries an SLA (`realtime` 60 s, `hourly` 1 h, `daily` 1 d, `weekly` 1 w); the write-back tables (`overrides`, `commits`) are held to the hourly tier because they change on a human action, and the tables rebuilt from cycle artifacts to the daily one; the report compares the mart's actual age, read from `mart_meta.generated_at` through the tenant-scoped connection, against each table's own SLA, so a table stale relative to its tier is a named breach rather than an average; a table absent from the mart is reported as absent rather than as a breach, because it cannot be stale | `test_serving.py::TestFreshness` (5); `test_bi.py::TestBiServingApi` (4); `BiPage.test.jsx` serving panel tests (4) |
| PR-3.2 Workload-aligned serving | Partial | `backend/app/bi/serving.py`: each BI workload declares a latency target taken from the persona SLA matrix — 400 ms for the interactive surfaces (the PR-2.2 budget) and 60 s for the grounded ask, which waits on a model round-trip rather than on the mart — and the report compares the p95 measured in the FinOps ledger against it; the percentile is computed from the raw sample distribution (`finops.durations`) rather than from per-bucket averages, so a slow tail moves it; the mart is indexed on the columns the governed queries filter by, leading with `tenant_id` so the tenancy temp views can use them, and `index_coverage` reports which declared indexes are actually present, so a mart built before this change is reported as uncovered rather than assumed to be indexed | `test_serving.py::TestLatency` (4), `TestIndexCoverage` (4); `test_bi.py::TestBiServingApi` (4); `BiPage.test.jsx` serving panel tests (4) |
| PR-3.3 Columnar transport | Partial | `backend/app/bi/transport.py`: the same tenant-scoped table is encoded three ways — JSON rows (what the API serves today), an Arrow IPC stream and Parquet with snappy — and the bytes and encode time for each are reported side by side, so the serialization overhead is measured rather than asserted; `GET /api/v1/bi/export/columnar` returns the bytes with the right media type and a download filename, and `GET /api/v1/bi/transport` returns the comparison; the export reads through `tenancy.scoped`, so a table without a `tenant_id` column is refused rather than exported unscoped, and the table name is checked against the declared contract before it reaches any SQL; the comparison reports the crossover honestly — Arrow's schema message makes it larger than JSON on a two-row table and smaller on a 500-row one, so the ratio is per table rather than a headline number | `test_serving.py::TestColumnarEncoding` (4), `TestComparison` (5), `TestExport` (5); `test_bi.py::TestBiTransportApi` (7); `BiPage.test.jsx` "measures the transport only when asked" |
| PR-6.1 Platform selection | Partial | `BI_ENHANCEMENT_PLAN.md` §7 decision record: the bespoke workbench stays primary and Superset stays the embedded surface; the deciding criterion is where metric logic lives — the governed registry in `backend/app/bi/semantics.py` is the only single definition of a metric, and a tool that imports dbt metadata without importing metric calculations would create a second one; Superset is the only option whose RLS is in the Apache 2.0 build rather than gated to a commercial tier; each rejected option is recorded with its disqualifying boundary, and the record states what would reverse the decision | Document review |
| PR-6.2 Visualization and grid components | Partial | `frontend/src/components/ui.jsx`: `DataTable` windows its rows — a fixed row height, a scroll viewport and an overscan band mean a million-row set renders a bounded slice with spacer rows standing in for the rest, so the scrollbar still reaches the final row without mounting it; a set that fits the viewport renders every row, so the small tables are unchanged; no dependency was added. Panels sit in a two-column grid with explicit spans, so a chart is half-width by default and a table or a tree takes the full row; `LineChart` caps its rendered width at 900 px and uses 11 px axis text, because an SVG scales its text with the viewBox and a 640-unit viewBox rendered at half width made the labels unreadable | `ui.test.jsx` (4); `bi-visual.spec.js` panel captures |

Still open: nothing in this plan is unstarted. PR-3.1, PR-3.2 and PR-3.3 stay Partial because the
infrastructure they name — a lakehouse, a streaming ingestion pipeline, an OLAP engine, a Flight SQL
server — is not deployed here, so the parts that could be measured were measured and the rest is
declared. PR-6.1 stays Partial because the sign-off it asks for is a business decision this record
informs but cannot make. PR-6.2 stays Partial because windowing is not the same as the 1M-row scroll
test the requirement names: the grid renders a bounded slice, but no browser test scrolls a million
rows, and the charts still have no dense-telemetry path.

Verified on this machine: `test_serving.py` 30 passed; `test_bi.py` 113 passed against the live API
(was 102, plus 11 for the serving and transport endpoints); `npm test` 65 passed across 6 files
(`BiPage.test.jsx` 36, was 28; `ui.test.jsx` 4); the Playwright visual suite 9 captures across 8
tests passed twice in a row. A mart build creates all 12 declared indexes and `index_coverage`
reports none missing. The live serving endpoint first reported all 12 indexes missing, because
`var/bi_mart.db` predated the index change — the report was correct and the mart needed a rebuild,
which is the manual post-deploy step noted below.
`npm test` was already exiting non-zero before this increment: `vite.config.js` declared no `test.exclude`,
so Vitest collected `e2e/bi-visual.spec.js` and failed on `test.describe()`. The config now extends
`configDefaults.exclude` with `e2e/**`, so the unit suite and the Playwright suite no longer overlap.

The dashboard was restructured after the serving work landed, because the Analyst persona had grown
to eighteen panels in a single column and repeated several facts. The baselines in
`e2e/__screenshots__/` were regenerated for that change; the previous set is in git history. The
Serving panel now has a baseline of its own (`serving-panel.png`), which it did not have before.

Known gaps in what shipped: the KPI tree corridors are hand-set rather than workshop-agreed, so
PR-1.1 stays Partial until the owners sign them off; the tree is read-only and has no quarterly
review workflow; and the persona selection is not persisted per user. The metric registry is an
in-process Python module, not a deployed semantic platform, so PR-4.1 keeps its vendor-selection
and multi-consumer parity obligations; PR-4.2 has no time spine, fiscal calendar or SCD2 model yet,
and the fan-out guard is a call-site check rather than a compiler-enforced join graph. PR-5.1 is
reachable over REST only, not through a semantic MCP endpoint, and has no Verified Query Curation
layer; the golden set is 8 questions, which is enough to catch a regression but too small to quote
as an accuracy figure; and questions are not audited per user. PR-5.2 screens twelve months with a
robust z-score, which cannot separate seasonality from an anomaly, so the peak months are flagged as
unusual rather than wrong; the projection is a linear trend, not a universal forecasting model; the
permission gate withholds item-level evidence from the read-only auditor role, so an auditor never
sees an actionable cause; and there is no alert delivery to Slack, Teams or email and no calibration
regression suite against a golden benchmark. PR-5.3 writes to the workbench's own log, not to an
operational ERP or planning system: there is no outbound connector, so "write back to operational
stores" holds only for the workbench's own store, and the allocation simulator is still read-only
with no persisted what-if override. The named approver is a routing label rather than an
authenticated principal, so any planner or admin can decide an action routed to someone else, and
nothing notifies that approver when an action is proposed. Optimistic locking covers the action's
state but not the annotation's target, so two planners can annotate the same item concurrently.
PR-7.1 has no CI pipeline to run its gate: the contract is enforced on build and by a pytest
invocation, not by a required check on a pull request, and there is no dbt or SQLMesh, so the
"virtualized CI" half of the requirement (selecting only the models a change touches) has no
analogue here. The contract covers the mart, not the upstream parquet artifacts, so a rename is
caught at the mart boundary rather than at its source, and nullability is advisory — a warning,
not a NOT NULL constraint. PR-7.2 has no CI pipeline to run it and no Docker daemon on this
machine, so the pinned container is defined but has never been built here: the baselines were
generated on Windows against the locally installed Chromium 140, and a Linux CI run would need its
own baselines generated inside the image. The suite covers the BI page only, not the other six
pages, and it asserts pixels rather than meaning — a screenshot cannot tell a correct chart from a
plausible wrong one, so the numeric assertions in `test_bi.py` and `BiPage.test.jsx` still carry
the correctness burden. A colour shift small enough to stay under `threshold: 0.2` per pixel and
`maxDiffPixelRatio: 0.002` is absorbed by design; nudging `IBCS.actual` by 5/255 passed, which is
the intended anti-aliasing budget rather than a defect. PR-8.1 isolates tenants in the serving layer,
not in the engine: the mart is still one file and the filter is a per-connection view, so anything
that opens the file directly — a backup, a Superset SQL Lab connection, an analyst with a SQLite
client — sees every tenant. There is no per-tenant Superset RLS or guest-token binding, no
object-level security hiding tables or columns, and no per-tenant workspace; the embedded dashboard is
still a single shared workspace, so the Superset guardrails in the requirement are unaddressed. Tenant
provisioning is a CLI argument on `python -m app.api.users add`, with no self-service onboarding, no
per-tenant quotas and no tenant lifecycle (suspend, export, delete). Isolation is tested at the unit
and session level, not by a penetration test, and the mart rebuild is a manual step after deploy —
the old file has no `tenant_id` column, so every BI endpoint returns 403 until `export_mart` runs.
PR-8.2 masks structured identifiers, not names: a person's name is not distinguishable from an ordinary
word without a roster, so a free-text comment can still carry one, and the rules are patterns, so an
unusual format for a supported kind passes through. ZDR is not attested — no provider's retention terms
were verified here, `AI_ZDR_PROVIDERS` is empty by default and `zdr.attested` is `False`, so the
requirement's "route all inference through ZDR endpoints" is unaddressed in substance and the control that
exists is the ability to refuse. OWASP LLM01 (prompt injection) is not covered by this increment: the
assistant still passes retrieved snippets and MCP evidence into the prompt, and the only injection defence
remains the grounded-ask path's refusal to execute model output as SQL. Redaction covers prompts and
provider error text, not the chat-history store, which keeps the planner's own words verbatim, and not
model output beyond restoring placeholders. The counters are process-local, so they reset on restart and
are not an audit trail.
PR-8.3 attributes compute, not money: with no cloud warehouse there is no `ACCOUNT_USAGE` to read and no
credit or dollar figure to report, so the three spend tiers are declared policy that can never fire here
and the report says so rather than estimating. The ledger is a local SQLite file with no retention policy,
no partitioning and no rotation, so it grows without bound and is not a substitute for a warehouse's own
query history. Attribution covers governed metric queries and the BI read surfaces; the anomaly diagnostics'
internal SQL, the write-back reads and the assistant's LLM calls are not tagged, so "100% of queries carry
a cost tag" holds for the semantic layer rather than for every statement the process issues. The AI quota
is off by default and applies only to `POST /api/v1/bi/ask`, not to the assistant stream, so it is a
control that exists rather than one that is switched on. Sprawl is measured from this ledger alone: a
metric is a prune candidate if nobody evaluated it in the window, which is not the same as being
uncertified, and there is no certification flag, no quarterly review workflow and no archive action — the
report lists candidates and stops there. The panel is not covered by the Playwright visual suite.
PR-3.1 declares a freshness tier; it does not deliver one. There is no Kafka, Flink or Iceberg, no
Bronze/Silver/Gold layering and no compaction, so the mart is rebuilt on demand from cycle artifacts
and every table shares one `generated_at`. The tier therefore distinguishes tables by the SLA they are
held to, not by how they are actually delivered, and a breach means the mart is older than the tier
allows rather than that a pipeline missed its window. There is no small-file metric, no partition
layout and no ingestion replay.
PR-3.2 measures latency against a single-file SQLite database, not an OLAP engine. No StarRocks, Pinot
or ClickHouse was evaluated or deployed, so the engine-selection half of the requirement is unaddressed
and the TCO-per-scanned-TB gate has nothing to measure. The indexes are ordinary B-tree indexes on the
filter columns; they help the tenancy views and the governed queries but they are not a columnar
projection, and the p95 figures only cover workloads that have been exercised since the FinOps ledger
was introduced — a workload with no samples reports `unmeasured` rather than a pass, which is honest
but means the SLA table is mostly empty on a fresh install.
PR-3.3 measures serialization, not the wire protocol. There is no Flight SQL server, so there is no
`GetFlightInfo` ticket and no parallel `DoGet` fetch across a thread pool; there is no ADBC driver, so
the client API is plain HTTP without ADBC's canonical configuration or query cancellation; and no
DuckDB-Wasm bundle is shipped, so the browser still receives JSON and cannot run SQL over the columnar
buffer in a Web Worker. The benchmark is therefore a same-process encode comparison rather than the
38x ODBC-versus-Flight transfer the requirement cites, and it is not comparable to that figure. The
export is a full-table read with no predicate pushdown, no column projection and no streaming, so a
large table is materialised in memory before it is sent.
PR-6.1 is a stack decision, not an evaluation. No commercial platform was trialled, no licensing quote
was obtained, and the feature boundaries in the comparison table come from vendor documentation rather
than from hands-on testing. The sign-off the requirement asks for is a business decision. PR-6.2
windows the grid but does not prove the requirement: the 1M-row claim is tested in jsdom against a
synthetic array, not in a browser against real data, and the charts still have no dense-telemetry path
for the 100,000-point case. The windowing assumes a fixed row height, so a cell that wraps will drift
from the spacer arithmetic.
