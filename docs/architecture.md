# lineai-mcp-server — Architecture

Source-derived architecture map of **lineai-mcp-server** (PyPI `lineai-mcp-server`),
the Model Context Protocol server that exposes Lineai code-graph analysis to AI
programming assistants. Traced from `main` on **2026-10-05** (HEAD `e539ba9`,
version **1.3.2**). All module names, endpoint paths, environment variables, and
behaviors below were verified in source; paths are relative to the repo root.

Companion document: [architecture-diagrams.md](architecture-diagrams.md) (Mermaid
sources) and [rendered-diagrams/](rendered-diagrams/index.md) (committed SVG renders).

> Written for Linear ticket **LIN-737** (add `/docs` architecture documentation).
> **Updated for LIN-699** (PR #61 in this repo; server side neo4cape PR #1860):
> honest error taxonomy (`LineaiApiError`, `isError=True`), per-call
> `workspace`/`materialized_view_id` scoping with a server-default fallback,
> auth retry on 401/403 + `expires_in` handling, an MV-resolution cache, and
> unresolved-reference reporting. Re-verified against `main` @ `e539ba9`.

---

## 1. What lineai-mcp-server is

A small (~2,900-line) **Python MCP server** that gives AI agents impact-analysis and
graph-discovery tools backed by a Lineai (neo4cape) server's HTTP API. It is a thin,
read-only API client with report formatting: every tool authenticates against
`LINEAI_SERVER_HOST`, calls one or more `/api/...` endpoints, and renders the JSON
into a Markdown (or fenced-JSON) `TextContent` response for the calling agent.

It holds **no local state beyond in-process caches** (§6): no database, no files
except optional debug logs, no graph logic of its own — all analysis happens on the
Lineai server.

### 1.1 Ecosystem context — who runs this server

Two deployment paths exist, both using **stdio transport**:

1. **Lineai agent runtimes.** The `claude-code-container` and `lineai-agent` images
   `git clone` this repo to `/claude/lineai-mcp-server` at image build time and
   register it into Claude agent sessions with
   `claude mcp add-json -s user lineai-mcp-server` using the config
   `{"type": "stdio", "command": "uvx", "args": ["--directory", "/claude/lineai-mcp-server", "lineai-mcp-server"]}`
   (verified in both repos' Dockerfiles and `claude/lineai-mcp-server-config.json` /
   `claude/mcp-servers.json`). The `LINEAI_*` variables (§8) come from the container
   environment. This is how prodsim/ticket-implementation agent pods get the tools.
2. **End-user IDEs.** The package is published to PyPI and the official MCP registry
   (`io.github.lineai-intelligence/lineai-mcp-server`, `server.json`); the README
   documents `uvx lineai-mcp-server@latest` configs for VS Code, Claude Desktop,
   Windsurf, and Cursor.

Upstream, every tool talks to the Lineai server (the neo4cape service) at
`LINEAI_SERVER_HOST` under the post-migration **`/api/...`** path prefix (commit
`9c6bded` moved all paths from the legacy `/codelogic/server/...` layout; no legacy
paths remain anywhere in this repo).

---

## 2. Repository and module map

| Path | Lines | Purpose |
|---|---|---|
| `src/lineai_mcp_server/server.py` | 88 | MCP `Server` instance, `.env` loading, stdio main loop, server `instructions` |
| `src/lineai_mcp_server/handlers/__init__.py` | 249 | `@server.list_tools` / `@server.call_tool` registration; tool schemas (shared `workspace`/`materialized_view_id` properties); dispatch via `asyncio.to_thread`; `LineaiApiError` rendering + last-resort error wrapper |
| `src/lineai_mcp_server/handlers/method_impact.py` | 459 | `lineai-method-impact` handler + Markdown report builder |
| `src/lineai_mcp_server/handlers/database_impact.py` | 168 | `lineai-database-impact` handler |
| `src/lineai_mcp_server/handlers/graph_tools.py` | 188 | the six `lineai-graph-*` handlers + dispatch table |
| `src/lineai_mcp_server/handlers/common.py` | 145 | `LINEAI_DEBUG_MODE`, debug-log directory, `error_result()` (`isError=True`), `render_lineai_api_error()`, timing log |
| `src/lineai_mcp_server/utils.py` | 1388 | `LineaiApiError`, `_authed_request` (single auth-header site), caches, MV resolver, impact retrieval/shaping, unresolved-reference extraction, database-report generation |
| `src/lineai_mcp_server/graph_client.py` | 176 | HTTP wrapper for `/api/ai-retrieval/graph/*` (via `utils._authed_request`) + graph error messages |
| `src/lineai_mcp_server/__init__.py` | 25 | package entry point (`main()` → `asyncio.run(server.main())`) — the `lineai-mcp-server` console script |
| `src/start_server.py` | 42 | **dev-only** entry point: starts a `debugpy` listener on `127.0.0.1:5679`, then runs the same `main()` |
| `test/` | — | 4 unit suites (93 tests, mocked HTTP) + 2 integration suites against a real host |
| `.github/workflows/` | — | `ci.yml`, `bump-version.yml`, `publish-to-pypi.yml` (§9) |
| `context/` | — | reference material (MCP SDK docs, graph tooling design notes) — not shipped code |

Dependency direction is strictly downward: `handlers/*` → `utils.py` /
`graph_client.py` → `httpx`. `graph_client.py` reuses `utils._authed_request`
(and through it the module-level `utils._client`), so auth headers and the
401/403 retry live in exactly one place (§5).

---

## 3. Entry point and MCP transport

The server uses the **low-level `mcp.server.Server` API** (not FastMCP):

- `server.py` creates `Server("lineai-mcp-server")` at module import, after
  `load_dotenv()` (skipped when `LINEAI_TEST_MODE` is set, so tests control their own
  environment). It prints `LINEAI_SERVER_HOST` to stderr at import.
- `server.main()` imports `handlers` **for its side effect**: the import executes the
  `@server.list_tools()` and `@server.call_tool()` decorators in
  `handlers/__init__.py`, which is the only registration mechanism.
- Transport is **stdio only**: `mcp.server.stdio.stdio_server()` wraps
  stdin/stdout; all diagnostics go to stderr. There is no HTTP/SSE transport.
- `InitializationOptions` advertises `server_name="lineai-mcp-server"`,
  `server_version=utils.get_package_version()` (now `importlib.metadata.version()`
  first, with a `pyproject.toml` fallback for source checkouts — finding 4,
  resolved by PR #61), and a static `instructions` string telling agents when
  to use the impact tools and to prefer `lineai-graph-*` when available.

Two launchers exist: the packaged console script `lineai-mcp-server`
(`lineai_mcp_server:main`, no debugger) and `src/start_server.py` (adds `debugpy`;
used by `.vscode/launch.json`).

### 3.1 Dispatch and the error wrappers

`handle_call_tool(name, arguments)` routes every tool to a **synchronous**
handler dispatched through `asyncio.to_thread`, so the blocking HTTP calls no
longer stall the MCP event loop (finding 6, resolved by PR #61). Unknown names
raise `ValueError`. Two catch layers wrap every handler:

- a `LineaiApiError` (§7.3) escaping a handler is rendered by
  `common.render_lineai_api_error` as an honest, per-kind Markdown page
  (auth / mv_not_found / mv_no_default / timeout / gateway_timeout /
  http_error / invalid_response);
- any other exception still gets the generic `# Error executing tool: {name}`
  page with the raw exception text.

Both paths — and every error page the handlers build themselves — are returned
as `CallToolResult(isError=True)` via `common.error_result`, so no tool call
ever crashes the server loop and clients can distinguish errors from reports.

---

## 4. Tool surface

Eight tools: two report-style impact tools and six JSON-passthrough graph tools.
All tool names are declared in `handlers/__init__.py`; inputs are JSON-Schema
validated by the MCP client.

**Scoping (all 8 tools, since PR #61):** every tool accepts optional
`workspace` (materialized-view-definition name) and `materialized_view_id`
arguments (`lineai-graph-capabilities` additionally accepts the camelCase alias
`materializedViewId`). The view is resolved by `utils.resolve_mv_id` with one
shared precedence chain — **explicit MV id → `workspace` argument →
`LINEAI_WORKSPACE_NAME` env var → the server's default workspace's latest view
via `GET /api/materialized-view/default`** — so specifying nothing is never an
error as long as the server has a default view. Name-based and default
resolutions are cached (§6).

### 4.1 `lineai-method-impact` (`handlers/method_impact.py`)

The richest handler. Inputs: `method` (required — schema and code agree since
PR #61), `class` (**optional**, a case-insensitive substring filter on node
identity; a dotted class name is reduced to its last segment), plus the shared
scope arguments.

Pipeline (endpoints in call order):

1. **MV resolution** — `resolve_mv_id(arguments)` per the shared precedence
   chain above; cached for `LINEAI_MV_CACHE_TTL` (finding 12, resolved).
2. **Shortname search** — `get_method_nodes(mv_id, method)`:
   `POST /api/ai-retrieval/search/shortname?materializedViewId=...&shortname=...`
   with an *empty body* (a deliberate choice documented in-code: sending
   `Content-Type: application/json` with an empty dict caused gateway 504 stalls
   that Swagger did not reproduce). Returns the `(nodes, error_kind)` tuple (§7.1)
   and populates the method-nodes cache (§6, non-empty results only).
3. **Node selection** — if `class` was given, pick the first node whose
   `identity` contains it (case-insensitive substring). No match renders a
   shaped no-match page listing up to 20 candidate identities (not a
   `ValueError` any more — finding 9, resolved); without `class`, the first
   node wins.
4. **Impact fetch** — `get_impact(node.properties.id, mv_id)`:
   `GET /api/dependency/impact/full/{id}/list?viewId={mv_id}` (the resolved
   view id is now sent as `viewId` so the traversal is scoped to that view),
   response stripped of a dozen internal properties (`strip_unused_properties`
   — unresolved-reference nodes are left untouched) and cached per
   `{id}:{mv_id}` (§6).
5. **Report assembly** — extracts nodes/relationships, finds the target method node
   (preferring one with `statistics.cyclomaticComplexity`), code owners/reviewers
   (`lineai.owners` / `lineai.reviewers`, falling back to the containing class
   node), direct dependents (incoming relationships), affected applications (via
   `Application` nodes, `groupIds`, `GROUPS` and `REFERENCES_GROUP` relationships),
   and REST surface (`Endpoint` nodes, mapping/Http annotations on
   `JavaMethodEntity`/`DotNetMethodEntity`, controller-like labels,
   `INVOKES_ENDPOINT`/`REFERENCES_ENDPOINT` edges).

Output: a Markdown report — AI guidelines, summary (complexity/instruction count,
owners, resolved materialized view), risk assessment (complexity > 10 flagged,
cross-application warning, REST API alerts), dependents list, an
unresolved-references section when the graph contains any (§4.4), a per-node
metrics table (UR nodes excluded — they carry no metrics), a full relationship
table (target rows bolded), and an ASCII application-dependency graph when more
than one application is affected.

### 4.2 `lineai-database-impact` (`handlers/database_impact.py`)

Inputs: `entity_type` (`column` | `table` | `view`), `name`, `table_or_view`
(enforced for columns), plus the shared scope arguments. Pipeline:

1. **Entity search** — `search_database_entity` (utils):
   `POST /api/ai-retrieval/search/{table|column|view}` with query params
   (`materializedViewId` from `resolve_mv_id(arguments)` — the same chain as
   every other tool; findings 2 and 5, resolved by PR #61) and a `{}` JSON
   body. Returns `(results, error_kind)`: `timeout`/`gateway_timeout`/
   `http_error` render honest infrastructure-error pages, auth failures raise
   `LineaiApiError('auth')` to the dispatcher, and only a 404 or an empty 200
   fall through to the no-match page.
2. For up to the **first 5** matches: `get_impact(entity_id, mv_id)` (same
   endpoint and cache as §4.1), then `process_database_entity_impact` derives
   dependent code (direct `REFERENCES`/`USES`/`SELECTS`/... edges, plus
   table-level references marked `indirect (via table)` for columns),
   referencing database objects (`REFERENCES`/`FOREIGN_KEY`), affected
   applications (direct `GROUPS` plus a recursive containment/reference
   traversal), parent table for columns, and code owners/reviewers harvested
   from dependent code and their containing classes. Per-entity failures are
   collected and rendered as an **"Impact retrieval failures"** table in the
   report (error kind + HTTP status) instead of a stderr-only skip.
3. `generate_combined_database_report` renders one Markdown report: application
   impact, per-entity detail (ownership, dependency tables capped at 10 rows, risk
   tier by dependency count: >20 high / >5 medium / else low, cross-application
   warning), a deduplicated unresolved-references section across all analyzed
   entities (§4.4), and a static best-practices section.

An empty search (404 or empty 200) yields a `# No {entity_type}s found matching
'{name}'` page that states the view resolved successfully and that this is a
definitive no-match, not an index or infrastructure problem — infrastructure
failures no longer masquerade as it (finding 2, resolved).

### 4.3 `lineai-graph-*` (`handlers/graph_tools.py` + `graph_client.py`)

Six thin tools over `POST`/`GET` `/api/ai-retrieval/graph/{suffix}`. Handlers accept
snake_case (and camelCase alias) arguments, validate required fields, build a
**camelCase** JSON body, inject `materializedViewId` via `resolve_mv_id`
(shared precedence chain, cached — no longer a per-call MV round-trip), and
delegate to `graph_request` (which routes through `utils._authed_request`, §5).
Success responses are returned verbatim as
`# {tool-name}` + fenced, sorted, pretty-printed JSON; the agent does its own
interpretation — unlike §4.1/4.2 there is no report shaping.

| Tool | Method + path suffix | Required args | Optional args |
|---|---|---|---|
| `lineai-graph-capabilities` | `GET /capabilities?materializedViewId=` | — | `workspace`, `materialized_view_id` (+ camelCase alias `materializedViewId`) |
| `lineai-graph-search` | `POST /search` | `query`/`q` or `identity_prefix` | `scan_space`, `prefer_latest_scan`, `limit`, `workspace`, `materialized_view_id` |
| `lineai-graph-impact` | `POST /impact` | `seed_node_ids` (non-empty list) | `direction` (`upstream`\|`downstream`\|`both`), `depth`, `scan_space`, `workspace`, `materialized_view_id` |
| `lineai-graph-path-explain` | `POST /path` | `from_node_id`, `to_node_id` | `max_depth`, `scan_space`, `workspace`, `materialized_view_id` |
| `lineai-graph-validate-change-scope` | `POST /validate-change-scope` | `seed_node_ids`, `proposed_change_summary` | `scan_space`, `workspace`, `materialized_view_id` |
| `lineai-graph-owners` | `POST /owners` | `node_id` or `identity_prefix` | `scan_space`, `workspace`, `materialized_view_id` |

`graph_request` classifies failures into its own error-kind set (§7.2). A 404 is
treated as **"graph tier not deployed"** — the graph API is newer than many Lineai
deployments, and the error message explicitly tells the agent to fall back to
`lineai-method-impact` / `lineai-database-impact`.

### 4.4 Unresolved-reference reporting (new in PR #61)

Since neo4cape PR #1860, impact and `/ai-retrieval/search/*` payloads can
contain **unresolved references** (URs): per-view nodes materialized for
references that could not be resolved to a concrete node inside the analyzed
view. The impact tools surface them as dependency indicators:

- **Detection** (`utils.is_unresolved_reference`): a node is a UR when
  `properties.primaryLabel == "UnresolvedReference"` (current servers emit
  top-level `primaryLabel == "SearchNode"` with that property), or — for
  normalized/legacy shapes — when the top-level `primaryLabel` is
  `"SearchNode"` or `"UnresolvedReference"` itself.
- **Extraction** (`utils.extract_unresolved_references`): per UR, the sought
  specification, target labels (per-view `v-...` labels filtered out), fuzzy
  criteria (server-serialized JSON string, parsed defensively), endpoint
  details (flat `endpoint.*` keys and a nested `endpoint` object), and the
  non-UR nodes referencing it via relationship endpoints.
- **Rendering** (`utils.format_unresolved_references_section`): a
  `## Unresolved References (dependency indicators)` Markdown section with a
  Sought / Target Labels / Endpoint / Referenced By table, appended to both
  impact reports (deduplicated by UR id across entities in the database
  report). **The section is omitted entirely when there are none**, so reports
  are unchanged against servers that do not emit URs.
- `strip_unused_properties` leaves UR nodes untouched (their properties carry
  the report data), and `extract_relationships` falls back to the synthetic
  `name` when a UR has no top-level `identity`. UR nodes are excluded from the
  per-node metrics table.

The `lineai-graph-search` tool's description also tells agents that results may
include `UnresolvedReference` rows and to treat them as dependency indicators
(the graph tools pass the JSON through without shaping).

---

## 5. Authentication flow (`utils.authenticate`)

Password-grant token auth against the Lineai server, with a client-side cache
(lock-guarded; finding 7 resolved by PR #61):

1. If `_cached_token` exists and `datetime.now() < _token_expiry`, return it.
2. Otherwise `POST /api/authenticate` with form-encoded
   `grant_type=password&username={LINEAI_USERNAME}&password={LINEAI_PASSWORD}`.
3. On success, cache `response.json()['access_token']`. The TTL **honors a
   server-provided `expires_in`**: `min(LINEAI_TOKEN_CACHE_TTL, expires_in − 30 s
   margin)` (floored at 0; default `LINEAI_TOKEN_CACHE_TTL` **3600 s** when the
   server sends none or an unparseable value). On failure, raise
   `LineaiApiError('auth')` (or `'timeout'`), rendered per-kind by the
   dispatcher (§3.1).

**`_authed_request` is the single place that attaches auth headers.** Every API
helper — including `graph_client.graph_request` — goes through it. On a **401
or 403** response it invalidates the cached token and retries the request
exactly once with a fresh token; a second 401/403 raises
`LineaiApiError('auth')`. Treating 403 as an auth failure matters: **the Lineai
server answers bad or expired tokens with 403, not 401** (verified live during
LIN-699 e2e). Transport errors are mapped to `LineaiApiError('timeout')` /
`('http_error')`.

All requests share one module-level synchronous `httpx.Client` configured with
`Timeout(LINEAI_REQUEST_TIMEOUT, connect=LINEAI_CONNECT_TIMEOUT)` (120 s / 30 s
defaults), connection limits (20 keepalive / 30 max), and
`HTTPTransport(retries=3)` — note these are **connect-level** retries only; failed
HTTP responses are never retried.

---

## 6. Caching

Four in-process caches in `utils.py`, all plain dicts guarded only by TTL expiry —
no size bound, no invalidation API (beyond the auth path's `invalidate_token`),
no cross-process sharing (acceptable for a per-session stdio server, but entries
live for the full TTL even if the underlying graph is rescanned):

| Cache | Key | Value | TTL (env, default) |
|---|---|---|---|
| token (`_cached_token`/`_token_expiry`) | — (single slot) | bearer token | `LINEAI_TOKEN_CACHE_TTL`, 3600 s (capped by server `expires_in` − 30 s, §5) |
| `_mv_cache` (new in PR #61) | workspace name, or `"<server-default>"` when none given | materialized view id | `LINEAI_MV_CACHE_TTL`, 300 s |
| `_method_nodes_cache` | `"{mv_id}:{short_name}"` | node list from shortname search | `LINEAI_METHOD_CACHE_TTL`, 300 s |
| `_impact_cache` | `"{id}:{mv_id}"` | stripped impact JSON **string** | `LINEAI_IMPACT_CACHE_TTL`, 300 s |

Expired entries are detected on read but never evicted (the dict entry is simply
overwritten on the next successful fetch). Asymmetries worth knowing:

- A **404** from the shortname search returns immediately and is *not* cached —
  repeated misses re-query the server.
- A **200 with an empty `data` array is no longer cached** either (PR #61), so
  transient empty results don't stick for the TTL — only non-empty node lists
  enter `_method_nodes_cache`.
- An explicit `materialized_view_id` argument bypasses `_mv_cache` entirely
  (nothing to resolve); only name-based and server-default resolutions are
  cached.
- The impact cache is scoped per view (`{id}:{mv_id}`), so the same node
  analyzed in two views caches separately.
- The graph tools (§4.3) have no response caching (their MV resolution is
  cached like everyone else's).

---

## 7. Error-handling taxonomy

### 7.0 `LineaiApiError` — the typed error (new in PR #61)

`utils.LineaiApiError(kind, status, detail, endpoint)` is the single typed
error for Lineai API failures. `kind` is one of **`auth`**, **`mv_not_found`**,
**`mv_no_default`**, **`timeout`**, **`gateway_timeout`**, **`http_error`**,
**`invalid_response`** (unknown kinds coerce to `http_error`). One escaping a
handler is rendered by `common.render_lineai_api_error` as a per-kind Markdown
page with the failing endpoint, HTTP status, a detail snippet, and
kind-specific recommendations. **Every error path — typed, generic, or a
handler-built error page — returns `CallToolResult(isError=True)`** via
`common.error_result` (§3.1).

### 7.1 The `(nodes, error_kind)` contract — `get_method_nodes`

`get_method_nodes` returns `(nodes, error_kind)` where `error_kind` is `None`
on success (**including a 200 with empty `data`**) or one of the kinds below;
only an `auth` failure raises (`LineaiApiError`, straight to the dispatcher's
per-kind rendering):

| Kind | Trigger | Handler rendering (`method_impact.py`) |
|---|---|---|
| `not_found` **or `None` with zero nodes** | HTTP **404** from the shortname search, or a 200 with empty `data` — **the same honest no-match page since PR #61** | "The materialized view resolved successfully (view `{mv_id}`), but no method matched the short name… This is a definitive no-match result from the Lineai search API, not an index or infrastructure problem." + check spelling / scope arguments / rebuild the view |
| `timeout` | `LineaiApiError('timeout')` (client-side, after `LINEAI_REQUEST_TIMEOUT`) | "request timed out after {timeout}s (client timeout)" + retry / raise timeout / check network |
| `gateway_timeout` | HTTP **504** | "Lineai API returned 504 Gateway Timeout" + retry hint and a note to compare against Swagger behavior |
| `http_error` | any other ≥400, transport error, or non-JSON payload | "request … failed (HTTP error or unexpected response). **This is an infrastructure problem — it says nothing about whether the method exists.**" + stderr pointer |

A class-filter miss on a non-empty result gets its own shaped page listing
candidate identities (§4.1). The handler renders one Markdown error page per
kind and returns it as the tool result (`isError=True`) — the agent always
gets a well-formed answer, never a protocol error.

### 7.2 Graph error kinds — `graph_client.py`

The graph path has a parallel taxonomy, computed from the response rather than
exceptions where possible: `not_deployed` (404 — "graph endpoints not deployed on
this host; use lineai-method-impact / lineai-database-impact instead"),
`gateway_timeout` (504), `http_error` (other ≥400, or transport error, or unset
`LINEAI_SERVER_HOST`, or unsupported method), `timeout`, and `invalid_json`
(2xx body that fails to parse; the message includes a ≤1500-char excerpt).
Since PR #61 the requests go through `utils._authed_request`, so transport
errors arrive as `LineaiApiError` (mapped onto `timeout`/`http_error`) and
`auth` failures raise to the dispatcher's per-kind page. `graph_error_message`
renders each kind as Markdown with the failing path and status, returned with
`isError=True`.

### 7.3 Everything else

`get_impact`, the MV resolvers (`get_mv_definition_id`, `get_mv_id_from_def`,
`get_default_mv_id`, `resolve_mv_id`), and `authenticate` all raise typed
`LineaiApiError`s — a bad workspace name is `mv_not_found` (404 on the
definition or latest-view lookup), a missing server default is
`mv_no_default`, malformed payloads are `invalid_response` — and the dispatcher
renders each as a recommendation-bearing page (finding 11, resolved).
Argument-validation `ValueError`s still fall to the generic
`# Error executing tool` page, but marked `isError=True`.

### 7.4 LIN-699 — RESOLVED by PR #61

Tracked in Linear as **LIN-699** (*"lineai-method-impact 'not indexed' wording
misreports a correct empty result as an infrastructure problem"*). Historical
context, kept brief: the old `not_found` branch answered a correct, healthy
empty result (HTTP 404 = zero matching nodes in a populated view) with
*"Confirm the method exists in the **indexed** codebase"*, which agents
compressed to "404 — not indexed" and reported as an indexing failure; a 200
with empty `data` fell into an equally misleading generic branch **and** was
cached for the full method-cache TTL.

All three are fixed by PR #61:

- 404 and empty-200 now route to the **same honest no-match page**: *"The
  materialized view resolved successfully (view `{mv_id}`), but no method
  matched the short name `{method}` in it. This is a definitive no-match result
  from the Lineai search API, not an index or infrastructure problem."* —
  naming the resolved view so an empty view is distinguishable from a miss.
- Genuine infrastructure failures say so explicitly ("…says nothing about
  whether the method exists") and are typed (§7.0/§7.1).
- Empty 200 results are no longer cached (§6).

---

## 8. Configuration

All configuration is environment variables (plus optional `.env` via
`python-dotenv`, loaded at `server.py` import unless `LINEAI_TEST_MODE` is set).
Every variable actually read by the code:

| Variable | Default | Read in | Purpose |
|---|---|---|---|
| `LINEAI_SERVER_HOST` | — (required) | `utils.py`, `graph_client.py`, `server.py` | Base URL of the Lineai server, e.g. `https://myco.app.lineai.net`; all `/api/...` paths are appended to it |
| `LINEAI_USERNAME` / `LINEAI_PASSWORD` | — (required) | `utils.authenticate` | Password-grant credentials for `POST /api/authenticate` |
| `LINEAI_WORKSPACE_NAME` | unset → the server's default workspace (`GET /api/materialized-view/default`) | `utils.resolve_mv_id` | Default workspace whose materialized view scopes searches; overridden per call by the `workspace` / `materialized_view_id` tool arguments (§4) |
| `LINEAI_DEBUG_MODE` | `false` | `handlers/common.py` | `true` writes `timing_log.txt` and raw `impact_data_*.json` to `{tempdir}/lineai-mcp-server` |
| `LINEAI_TOKEN_CACHE_TTL` | `3600` (s) | `utils.py` | Client-side auth-token cache lifetime |
| `LINEAI_METHOD_CACHE_TTL` | `300` (s) | `utils.py` | Shortname-search result cache lifetime (non-empty results only) |
| `LINEAI_IMPACT_CACHE_TTL` | `300` (s) | `utils.py` | Impact-response cache lifetime |
| `LINEAI_MV_CACHE_TTL` | `300` (s) | `utils.py` | Materialized-view resolution cache lifetime (workspace name / server default → view id; new in PR #61) |
| `LINEAI_REQUEST_TIMEOUT` | `120.0` (s) | `utils.py` (+ echoed in error text) | Overall httpx request timeout for every API call |
| `LINEAI_CONNECT_TIMEOUT` | `30.0` (s) | `utils.py` | httpx connect timeout |
| `LINEAI_TEST_MODE` | unset | `server.py`, `test/` | Any value skips `load_dotenv()` so tests fully control the environment |
| `LINEAI_GRAPH_E2E_REQUIRED` | unset | `test/integration_test_graph.py` only | `1` turns "graph routes return 404" from test *skip* into test *failure* |

TTL/timeout values are read **once at `utils` import**; changing them requires a
server restart.

---

## 9. Packaging, versioning, and release

- **Build**: `pyproject.toml`, hatchling backend, src layout, console script
  `lineai-mcp-server = lineai_mcp_server:main`. `requires-python >=3.13,<3.15`;
  `.python-version` pins **3.14** for local `uv`. License **MPL-2.0** (headers
  enforced ad hoc by `add_license_headers.py`).
- **Runtime dependencies** (pruned by PR #61): `mcp[cli] >=1.4.0,<2`, `httpx`,
  `python-dotenv`, `debugpy`, `httpcore`. `tenacity`, `pip-licenses`, `anyio`,
  and `toml` (replaced by stdlib `tomllib`) are gone; `debugpy` remains a hard
  install dependency despite being dev-only, and `httpcore` is still pinned to
  the encode git repo via `[tool.uv.sources]` (finding 3, partially resolved).
- **Version**: `1.3.2`, maintained in **two** files — `pyproject.toml` and
  `server.json` (twice: top level and `packages[0].version`). The MCP
  `server_version` comes from `importlib.metadata.version()` (exact for wheel
  installs), falling back to `pyproject.toml` for source checkouts and `"0.0.0"`
  only when neither is available (finding 4, resolved).
- **CI** (`ci.yml`): on push/PR to `main` — Python **3.13 and 3.14** matrix
  (finding 10, resolved) — flake8 (hard gate restricted to `E9,F63,F7,F82`;
  everything else `--exit-zero`) and `unittest discover -s test -p "unit*.py"`
  (unit suites only; integration suites need a live host and are not run in CI).
- **Release**: `bump-version.yml` (manual `workflow_dispatch`: patch/minor/major or
  custom version) rewrites the version and pushes to `main`; its completion — or a
  GitHub release — triggers `publish-to-pypi.yml`, which builds with
  `uv build --no-sources` on Python 3.14, `twine check` + uploads to **PyPI**,
  publishes `server.json` to the **official MCP registry** via `mcp-publisher`
  (GitHub OIDC login), and posts a Teams notification.
- `renovate.json` keeps dependencies and digest-pinned action SHAs updated;
  `glama.json` registers the maintainer for the Glama MCP directory.

---

## 10. Tests

- **Unit** (CI, mocked HTTP; substantially expanded by PR #61 to 93 tests):
  `unit_test_environment.py` (2: env handling),
  `unit_test_utils.py` (53: auth/retry/`expires_in`, `_authed_request`, MV
  resolver + cache, caching asymmetries, UR detection/extraction/rendering,
  node extraction, report pieces),
  `unit_test_handlers.py` (31: tool listing/schemas, dispatch, honest error
  pages, `isError` flags, class-filter candidates),
  `unit_test_graph_tools.py` (7: graph handler validation and request shaping).
  `test/test_env.py` injects `DEFAULT_TEST_ENV` (fake host/credentials, 60 s TTLs,
  `LINEAI_TEST_MODE=true`) before package import.
- **Integration** (manual, real host via `test/.env.test`):
  `integration_test_all.py`, and `integration_test_graph.py` which drives the real
  `handle_call_tool` path for capabilities plus a chained
  search → impact → path → validate → owners flow; graph-route 404s **skip** unless
  `LINEAI_GRAPH_E2E_REQUIRED=1`. `scripts/run_graph_e2e.sh` is a one-line wrapper.

---

## 11. Findings (original pass 2026-10-05; status re-verified after PR #61 merged)

Most of the original findings were fixed by **PR #61 (LIN-699)**; each entry
below states its current status against `main`.

1. **LIN-699 — `not_found` wording misreports a correct empty result** —
   **RESOLVED (PR #61)**. 404 and empty-200 now share one honest no-match page
   naming the resolved view; infrastructure errors say so explicitly; empty 200
   results are no longer cached. See §7.4.
2. **`search_database_entity` swallows every error and returns `[]`** —
   **RESOLVED (PR #61)**. It now returns `(results, error_kind)`; the handler
   renders honest infrastructure-error pages, auth failures raise to the typed
   dispatcher, and per-entity impact failures appear in an "Impact retrieval
   failures" report section (§4.2).
3. **Declared-but-unused runtime dependencies** — **PARTIALLY RESOLVED
   (PR #61)**. `tenacity`, `pip-licenses`, `anyio`, and `toml` were removed.
   Still open: `debugpy` (imported only by the dev-only `src/start_server.py`)
   remains a hard install dependency of the published wheel, and `httpcore` is
   still force-sourced from the encode git repo via `[tool.uv.sources]` (and
   duplicated as the sole `dev` dependency-group entry) — a pin that
   `--no-sources` builds deliberately ignore at publish time.
4. **Installed servers report version `0.0.0`** — **RESOLVED (PR #61)**.
   `get_package_version()` now tries `importlib.metadata.version()` first,
   falling back to `pyproject.toml` for source checkouts.
5. **Workspace-name handling is inconsistent across tool families** —
   **RESOLVED (PR #61)**. `encoded_workspace_name` and `get_workspace_name()`
   are gone; all eight tools resolve scope through `utils.resolve_mv_id` with
   one precedence chain, and name lookups go through `httpx` query params
   (properly encoded) instead of f-string URLs.
6. **Synchronous HTTP inside async handlers** — **RESOLVED (PR #61)**. The
   handlers are now synchronous functions dispatched via `asyncio.to_thread`
   (§3.1); the HTTP client is still blocking, but it no longer stalls the MCP
   event loop.
7. **Token cache ignores the server** — **RESOLVED (PR #61)**. `authenticate()`
   honors a server-provided `expires_in` (30 s safety margin, capped at
   `LINEAI_TOKEN_CACHE_TTL`), and `_authed_request` invalidates and retries
   once on 401 **or 403** (the Lineai server's actual bad-token answer). See §5.
8. **Dead local in `handle_database_impact`** — **RESOLVED (PR #61)**, removed
   along with the `encoded_workspace_name` path (finding 5). The flake8 hard
   gate still selects only `E9,F63,F7,F82`, so future F841s would still pass CI.
9. **Schema/code disagreement on `lineai-method-impact`** — **RESOLVED
   (PR #61)**. The schema now requires only `method` (`class` is an optional
   case-insensitive substring filter), and a class-filter miss renders a shaped
   page listing candidate identities instead of a raw `ValueError` (§4.1).
10. **Python version matrix drift** — **RESOLVED (PR #61)**. CI now runs a
    `[3.13, 3.14]` matrix, matching `.python-version` (3.14), the publish
    workflow, and `requires-python`.
11. **MV resolution and impact fetch have no error taxonomy** — **RESOLVED
    (PR #61)**. All of them raise typed `LineaiApiError`s (`mv_not_found`,
    `mv_no_default`, `invalid_response`, …) rendered as per-kind
    recommendation-bearing pages with `isError=True` (§7.0, §7.3).
12. **MV resolution is never cached** — **RESOLVED (PR #61)**. `_mv_cache`
    caches name-based and server-default resolutions for `LINEAI_MV_CACHE_TTL`
    (default 300 s); an explicit `materialized_view_id` argument needs no
    resolution at all (§6).

---

## Appendix A — Tool → Lineai (neo4cape) endpoint mapping

Consolidated map of every HTTP call this server makes, with the neo4cape handler
behind each one (verified in the neo4cape source, `integration` branch,
`neo4cape-service/src/main/java/com/codelogic/neo4cape/service/`). The Spring
controllers map paths *without* the `/api` prefix (e.g. `@RequestMapping("/ai-retrieval")`);
the public `/api/...` prefix this server calls is added by the deployment's routing
layer (see this repo's commit `940ac95`, which routed the legacy
`/codelogic/server` paths to `/api`). All endpoints except `/api/authenticate`
require the bearer token (`@PreAuthorize` admin-or-user).

> **Server side of LIN-699** — neo4cape **PR #1860** (merged): all AI endpoints
> (`/ai-retrieval/search/*`, `/ai-retrieval/graph/*`, the dependency impact
> endpoint) now accept an **absent** `materializedViewId`/`viewId` and default
> to the primary (default) materialized view, and the `/ai-retrieval/search/*`
> endpoints return **UnresolvedReference** rows (§4.4) alongside concrete
> matches.

| MCP tool | Endpoint (public path) | neo4cape handler | Notes |
|---|---|---|---|
| all tools (auth) | `POST /api/authenticate` | `AuthController.loginUser` → `AuthService.authenticate` | Form-encoded username/password (the client's extra `grant_type` field is ignored by `LoginRequest`); token cached client-side, `LINEAI_TOKEN_CACHE_TTL` 3600 s capped by `expires_in` − 30 s; bad tokens on later calls come back as **403** and trigger the one-shot invalidate-and-retry (§5) |
| all tools (named-workspace MV resolution, step 1) | `GET /api/materialized-view-definition/name?name=` | `MaterializedViewDefinitionController.getMaterializedViewDefinitionByName` → `MaterializedViewDefinitionService` | Workspace name → MV definition; cached via `_mv_cache`, `LINEAI_MV_CACHE_TTL` 300 s (finding 12, resolved); 404 → `LineaiApiError('mv_not_found')` (§7.3) |
| all tools (named-workspace MV resolution, step 2) | `GET /api/materialized-view/latest?definitionId=` | `MaterializedViewController.getLatestMaterializedViewByDefinitionId` → `MaterializedViewService.getLatestMaterializedViewObject` | Definition id → latest MV id; same caching/error typing as step 1 |
| all tools (no workspace specified) | `GET /api/materialized-view/default` | `MaterializedViewController` (`@GetMapping("/default")`) → `MaterializedViewService` | **Single-call** resolution of the server's default (primary) workspace's latest finished view id (the `data` field is the bare id); cached via `_mv_cache`; 404 → `LineaiApiError('mv_no_default')` |
| `lineai-method-impact` | `POST /api/ai-retrieval/search/shortname` | `AIRetrievalController.searchByShortname` → `AIRetrievalServiceImpl.findByShortname` → `BaseNodeSearchRepositoryCustomImpl.findNodesByShortname` | Exact `shortName` match on method-entity nodes in the MV; 404 on zero rows. Cached per `mv_id:short_name` (non-empty results only), `LINEAI_METHOD_CACHE_TTL` 300 s; maps to §7.1 error kinds; 404/empty-200 → honest no-match page (LIN-699, resolved). May return UnresolvedReference rows (neo4cape #1860) |
| `lineai-database-impact` | `POST /api/ai-retrieval/search/{table\|column\|view}` | `AIRetrievalController.searchForTableNodes` / `searchForColumnNodes` / `searchForViewNodes` → `AIRetrievalServiceImpl.find{Table,Column,View}Nodes…` (same repository) | Uncached; returns `(results, error_kind)` — errors rendered honestly, no longer swallowed to `[]` (finding 2, resolved). May return UnresolvedReference rows (neo4cape #1860) |
| `lineai-method-impact`, `lineai-database-impact` | `GET /api/dependency/impact/full/{id}/list?viewId=` | `DependencyController.findDependencyImpact2` → `ImpactService.getImpactData` | Full incoming-relationship chain for the node. The resolved MV id **is now sent as `viewId`** to scope the traversal; cached per `{id}:{mv_id}`, `LINEAI_IMPACT_CACHE_TTL` 300 s; failures raise typed `LineaiApiError`s (§7.3) |
| `lineai-graph-capabilities` | `GET /api/ai-retrieval/graph/capabilities?materializedViewId=` | `AIGraphMcpController.capabilities` → `AIGraphMcpServiceImpl` | Graph tier; uncached; §7.2 error kinds (404 → `not_deployed`) |
| `lineai-graph-search` | `POST /api/ai-retrieval/graph/search` | `AIGraphMcpController.search` → `AIGraphMcpServiceImpl.search` | camelCase body; uncached; §7.2 |
| `lineai-graph-impact` | `POST /api/ai-retrieval/graph/impact` | `AIGraphMcpController.impact` → `AIGraphMcpServiceImpl.impact` | "Impact subgraph from seed node ids (merged, capped)"; uncached; §7.2 |
| `lineai-graph-path-explain` | `POST /api/ai-retrieval/graph/path` | `AIGraphMcpController.path` → `AIGraphMcpServiceImpl.path` | Shortest path within the MV; uncached; §7.2 |
| `lineai-graph-validate-change-scope` | `POST /api/ai-retrieval/graph/validate-change-scope` | `AIGraphMcpController.validateChangeScope` → `AIGraphMcpServiceImpl.validateChangeScope` | Deterministic heuristics per the controller's `@Operation` summary; uncached; §7.2 |
| `lineai-graph-owners` | `POST /api/ai-retrieval/graph/owners` | `AIGraphMcpController.owners` → `AIGraphMcpServiceImpl.owners` | "Owner-like fields from node properties (best-effort)"; uncached; §7.2 |

For the full neo4cape API surface see neo4cape's
`docs/neo4cape-architecture/LINEAI-API-REFERENCE.md` (renamed from
`CODELOGIC-API-REFERENCE.md` in neo4cape PR #1857; a July 2026 snapshot).
