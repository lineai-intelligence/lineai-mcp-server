# Graph-backed MCP & API tooling ideas

## Goal

Design **`lineai-mcp-server`** so developers and AI agents get **safe, explainable** guidance when changing or generating code—grounded in the **knowledge graph**.

### Execution model

- **Curated graph access** lives behind an **HTTP API** (service layer runs bounded Cypher / repository logic against Neo4j).
- **MCP calls agent-oriented HTTP endpoints** only.
- **Avoid ad-hoc Bolt/Cypher from MCP** for product flows: weak guardrails, credential sprawl, and hard-to-cap queries.
- The graph may contain **Java**, **C# (.NET)**, or **mixed** scans; tools must **adapt to what is present** (labels and relationship types vary by language and pipeline).

## Existing `lineai-mcp-server` tools (baseline)

This document is about **enhancements** to the **same** MCP server (`lineai-mcp-server`), not a separate product. Implementations live alongside the current tool registrations in `src/lineai_mcp_server/handlers.py`.

### Already shipped today

| Tool | Role |
| --- | --- |
| **`lineai-method-impact`** | Impact analysis for a **method** in a **class** via the **Lineai server** HTTP API (`LINEAI_WORKSPACE_NAME`, credentials in env). |
| **`lineai-database-impact`** | Impact between **code and database** entities (table / view / column) via the same server API. |

### How graph tools relate

- The proposed **`lineai-graph-*`** tools **extend** the server’s surface area: deeper **graph discovery**, **bounded** traversals, **manifest-driven** behavior, and alignment with an **`/ai-retrieval`**-style HTTP API as that API is built out.
- They **complement** `lineai-method-impact` and `lineai-database-impact` where those stay the thin, stable wrappers around existing Lineai endpoints; over time, graph tools may **share backend capabilities** (same graph service) while keeping **MCP contracts** distinct from the web UI.
- CI / build-feedback pipeline patching for library upgrades is owned by neo4cape ticket-implementation prompts (`PromptConstants`), not by MCP CI tools.

## API Calls

- **Agent routes** should live under a dedicated prefix such as **`/ai-retrieval`**, alongside existing **AI-style retrieval** (e.g. shortname / DB entity search by materialized view).

### Suggested route sketches

| Pattern | Purpose |
| --- | --- |
| `GET …/capabilities` (or `…/manifest`) | What this graph supports (labels, relationship types, scopes)—server-side introspection, not from MCP |
| `POST …/graph/search` | Structured search with disambiguation metadata |
| `GET …/graph/node/{id}` | Bounded describe + neighborhood |
| `GET …/graph/impact` | Normalized impact + confidence flags |
| `GET …/graph/path` | Bounded path between two node ids |
| `GET …/graph/scan-spaces` | Optional; when many scans share one DB |

### Principles for new routes

Strict **timeouts and row caps**; **request/response DTOs**; reuse **services/repositories internally** shared with the UI without exposing UI routes to MCP.

### Auth

Same scheme as other secured API routes; MCP uses **service credentials**.

## Observations from real graphs (Java and C#)

These lessons come from **exploring** Neo4j-backed corpora (including direct graph reads during research). Production agents should consume the **`/ai-retrieval`**-style API, not raw graph queries.

### Java-heavy graphs

- **`Application` / named workspace** may be missing or sparse; **artifact-centric** identity (`JavaExecutable` / JAR, `JavaSourceFileEntity`, **`identity`** on methods and classes) often matters more than a single “app name.”
- **Duplicate nodes** are common (same logical file or method **many times** across materializations or history). Never treat **`name`** alone as unique; return **`elementId`**, full **`identity`**, and **multiplicity / disambiguation** hints.
- **`SEARCH` → `SearchNode`** can model **classpath / dependency** exposure; fanout ranges from **zero** to **very large**—tools must treat both as normal.
- **`INVOKES_METHOD`** may be **empty** where static analysis stops (e.g. framework entrypoints); report **confidence** / **static_analysis_gap** instead of implying no callers.
- **Unbounded scans** (`CONTAINS` on huge labels, global counts) **time out**; prefer **`elementId`**, tight **`identity STARTS WITH`**, and **`LIMIT`** / subqueries.
- **Shared libraries** may carry types whose names match the product; impact analysis may need **cross-artifact** scope.

### C# / .NET-heavy graphs

- **DotNet\*** labels may dominate; **Java** and **DB/HTTP** subgraphs may be **absent**. Tools must **feature-detect** and **degrade** (no hard dependency on `Endpoint`, `Table`, `INVOKES_METHOD`, `CONTAINS_SOURCEFILE`, etc.).
- **`.NET` `identity`** is often **pipe-delimited** (assembly, module, path, type, signature); short **method names** collide—same disambiguation rules as Java.
- **`Workspace` / `ScanSpace` / `MaterializedView`** properties may be **minimal or null** in some deployments; do not assume rich display names everywhere.
- **Relationship sets differ** from typical Java enterprise scans; traversal templates must be **per-rel-type flags**, not one fixed bundle.

### Any language

- **One database name** can point at **different corpora** over time—**discover** shape at runtime (manifest) or document environment explicitly.
- **`ScanSpace`** (or equivalent) sometimes encodes **repository + branch** in one string—useful **scope** when many variants share a DB.
- **`Workspace.displayName` + UUID** may be populated in some multi-repo setups—use when present.
- **`UnresolvedReference`** (and similar) can be **sparse**; treat as **best-effort** enrichment.

## Graph model palette (verify per deployment)

- **Org / scan:** `Workspace`, `ScanSpace`, `MaterializedView`, `MaterializedViewDefinition`, optional `Application`
- **Java:** `JavaExecutable`, `JavaSourceFileEntity`, `JavaClassEntity`, `JavaMethodEntity`, …
- **.NET:** `DotNetAssembly`, `DotNetModuleEntity`, `DotNetClassEntity`, `DotNetMethodEntity`, …
- **Data / HTTP (when scanned):** `Schema`, `Table`, `Column`, `View`, `StoredProcedure`, `Endpoint`
- **Other:** `SearchNode`, `UnresolvedReference`

**Relationships (subset varies):** containment (`CONTAINS_*`), `SEARCH`, `REFERENCES_*`, `EXTENDS_CLASS`, `IMPLEMENTS_INTERFACE`, `INVOKES_METHOD`, `SERVES_ENDPOINT`, `GROUPS`, …

**Metadata:** **`identity`** for disambiguation; **`name`** is not unique; optional owners/reviewers and metrics when present.

## MCP tool catalog (API-backed)

Implementations call **`/ai-retrieval`** (or equivalent on the Lineai host)—**not** UI graph URLs, **not** raw Cypher from MCP.

### Discovery and targeting

- **`lineai-graph-search`** — Multi-strategy scope: materialized view, workspace UUID / display name when present, optional **scan-space / branch** filter, artifact or **identity prefix** fallback; returns **`elementId`**, **`identity`**, collision hints.
- **`lineai-graph-scan-spaces`** (optional) — List or filter scan-space entries when multi-scan DBs.
- **`lineai-graph-describe-node`** — One node + bounded neighborhood.
- **`lineai-graph-neighborhood`** — Filtered 1–2 hop expansion.

### Impact

- **`lineai-graph-impact`** — Seeds, direction, depth; applications **or** jars / scan buckets; **`confidence` / `static_analysis_gap`** when invoke edges are missing.
- **`lineai-graph-path-explain`** — Bounded paths between entities.
- **`lineai-graph-impact-summary`** — Aggregates by type, jar/package, optional scan dimension, owners.

### Database and HTTP (when graph supports them)

- **`lineai-graph-db-usage`** / **`lineai-graph-db-cascade-risk`** — Use **actual** relationship types in that deployment.
- **`lineai-graph-classpath-slice`** — `SEARCH`-based slice with caps; **zero edges** is valid.
- **`lineai-graph-unresolved-hints`** — Best-effort unresolved rows for a scope.
- **`lineai-graph-endpoint-impact`** / **`lineai-graph-endpoint-inventory`** — Only when **`Endpoint`** (and related) data exists.

### Workflow and guardrails

- **`lineai-graph-owners`**, **`lineai-graph-change-checklist`**
- **`lineai-graph-validate-change-scope`**, **`lineai-graph-risk-score`** (include optional **duplicate / multi-MV** penalty)

## Recommended MVP

1. `lineai-graph-search`  
2. `lineai-graph-impact`  
3. `lineai-graph-path-explain`  
4. `lineai-graph-validate-change-scope`  
5. `lineai-graph-owners`  

Optional early win: **`lineai-graph-classpath-slice`** when `SEARCH` data is useful and cheap.

## Design principles

- **Scope object**, not a single free-text workspace string: MV id, workspace id, scan filter, artifact prefix, or `elementId` list from a prior step.
- **No unbounded global scans** on large labels in default paths.
- **Curated server-side queries** only; optional **feature-flagged** raw query for admins.
- **Bounded depth**, **result caps**, **`query_stats`** and **`status`** (`ok`, `partial`, `timeout`, `error`) in responses.

## Reliability

- Timeouts and retries at the **HTTP** layer; caps inside the service.
- **`status=partial`** when truncated or degraded; never silent under-completion.

## Implementation roadmap (sketch)

**Phase 1 — Infrastructure:** HTTP client to graph API base (including **`/ai-retrieval`** path), auth, shared response normalization for MCP.

**Phase 2 — MVP tools:** search, impact, path-explain; smoke tests against **Java** and, if available, **.NET** corpora.

**Phase 3 — Guardrails:** validate-change-scope, owners, risk score.

**Phase 4 — Domain packs:** Java web patterns, SQL evolution, cross-service HTTP—still behind the same API discipline.

**Env (illustrative):** same **`LINEAI_SERVER_HOST`** as other MCP tools (graph lives under `/api/ai-retrieval/graph/…` on that host), materialized view / definition hints aligned with existing **`materializedViewId`**-style parameters, request timeouts.

## Open questions (working answers)

These were design tensions; below are **default recommendations** so implementation can proceed. Product or ops can still override with explicit configuration or API policy.

### Canonical node among duplicates

**Recommendation:** **Do not silently merge** duplicate graph nodes in the API. Every response row should carry **stable ids** (`elementId` or service-native id), **`identity`**, and **provenance** (materialized view / scan / materialization metadata when available). If the product ever defines a **canonical merge key**, that should be an **explicit, versioned** field (e.g. `logical_entity_id`) returned alongside raw nodes—not inferred from `name` alone.

**Default narrowing:** Honor the caller’s **scope** (MV, workspace, optional scan-space filter). Within that scope, optional flags such as **`prefer_latest_scan=true`** (default **off** or **on** per product decision) should be **documented** and reflected in the response (`assumptions_applied`).

### Multi-`ScanSpace` databases

**Recommendation:** **Never default to “all scans”** for impact or path tools—too easy to mix branches and blow caps. Default to **(a)** the narrowest scope implied by the request (e.g. MV + latest materialization already resolved for shortname search), or **(b)** **require** an explicit **`scan_space` / branch / scan id`** when the backend detects **multiple** candidates for the same identity prefix.

Search/discovery tools may return **disambiguation groups** (`status=partial`, multiple hits) instead of picking a branch arbitrarily.

### Contract stability of labels and relationship types

**Recommendation:** MCP tools and agent prompts target **semantic operations** (search, impact, path, describe), not raw Neo4j label strings. The HTTP layer exposes a **`capabilities` / `manifest`** (versioned: e.g. `graph_contract_version`, `capabilities_etag`) listing what exists **in that environment**. Breaking graph schema changes **update the manifest version**; clients pass `If-None-Match` or version when caching. Raw label names may still appear **inside** DTOs for debugging but are not the primary agent contract.

### Risk scoring: deterministic first vs calibrated

**Recommendation:** Ship **deterministic** scoring first—documented weighted signals (e.g. depth, fanout, duplicate multiplicity penalty, `static_analysis_gap` flags). Log **telemetry** (truncation, timeouts, optional anonymized counts) for later **calibration** or ML-assisted ranking **without** changing default semantics in a patch release.

### Graph contract vs live label/relationship discovery

**At runtime**, querying **`capabilities`** (or equivalent) is enough to know **which** labels and relationship types exist on **this** graph—agents and servers should **not** depend on a static list that can drift after rescans or upgrades.

**A frozen minimal contract** is still useful for **other** reasons: **(1)** **Regression tests** need pinned expectations (fixture JSON sampled from `capabilities` at a known `graph_contract_version`, not “whatever prod returned Tuesday”). **(2)** **Semantics**—which rel types participate in “impact,” default scope rules, disambiguation—are **not** implied by `CALL db.relationshipTypes()`; they belong next to HTTP DTOs or a short doc. **(3)** **Compatibility**—which MCP/server pairs you support—is easier to state against a **versioned manifest** than against an unversioned live DB.

So the “freeze” is **not** a second source of truth competing with discovery; it is **pinned artifacts + documented semantics** for engineering and release discipline. If you prefer, treat the “document” as **checked-in fixture snapshots** derived from `capabilities`, not a hand-maintained duplicate inventory.

## E2E testing (MCP + neo4cape)

### neo4cape (Java, Testcontainers)

- Integration tests live in `neo4cape-service/src/test/java/.../*TestIT.java`, e.g. **`AIGraphMcpControllerTestIT`** (`/ai-retrieval/graph/*`).
- Parent POM sets **`skipITs` default `true`**, so ITs do **not** run on a normal `mvn test` / `mvn verify` unless you opt in.

```bash
cd neo4cape
mvn -pl neo4cape-service verify -DskipITs=false -DskipUTs=true -Dit.test=AIGraphMcpControllerTestIT
```

- Omit **`-Dit.test=...`** to run **all** `*TestIT` classes in the module (slow).

### lineai-mcp-server (Python, real host)

- **`test/integration_test_graph.py`** calls **`handle_call_tool`** for the six `lineai-graph-*` tools against **`LINEAI_SERVER_HOST`** (same credentials / workspace as `integration_test_all.py`).
- If the host returns **404** for graph routes, tests **skip** unless **`LINEAI_GRAPH_E2E_REQUIRED=1`** is set (then they **fail**).

```bash
cd lineai-mcp-server
uv run python -m unittest test.integration_test_graph -v
```

- Load real credentials via **`test/.env.test`** or project **`.env`** (see `load_test_config()` in `test/integration_test_all.py`).

### One-liner script (MCP repo)

- **`scripts/run_graph_e2e.sh`** runs the Python graph E2E module (exits 0 on skip, non-zero on failure when required).

## Next steps

1. Publish a small **`/ai-retrieval/.../capabilities`** (or equivalent) contract.  
2. Freeze a minimal **graph contract** document **per target graph context** (labels, rels, scope rules): usually **one Lineai server deployment** (host/tenant) you care about, and—if schema differs materially—**scanner or graph-ingestion version band**. *Environment* here means **“this graph as deployed”** (what labels/rels actually exist), **not** a generic `.env` file; the live **`capabilities`** response stays authoritative, while the frozen doc is what you **test and document MCP against** until the manifest version bumps.  
3. Define MCP tool **JSON schemas** and map each to **one or a few** HTTP operations.  
4. Implement MVP with **id- and prefix-first** queries and strict caps.  
5. Add **fixtures** that include **duplicates** (same name, many ids) and **multi-scan** scope for regression tests.
