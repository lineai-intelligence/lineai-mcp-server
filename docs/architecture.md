# lineai-mcp-server — Architecture

Source-derived architecture map of **lineai-mcp-server** (PyPI `lineai-mcp-server`),
the Model Context Protocol server that exposes Lineai code-graph analysis to AI
programming assistants. Traced from `main` on **2026-10-05** (HEAD `6fdff87`,
version **1.3.2**). All module names, endpoint paths, environment variables, and
behaviors below were verified in source; paths are relative to the repo root.

Companion document: [architecture-diagrams.md](architecture-diagrams.md) (Mermaid
sources) and [rendered-diagrams/](rendered-diagrams/index.md) (committed SVG renders).

> Written for Linear ticket **LIN-737** (add `/docs` architecture documentation).
> The error-wording issue discussed in §7 is tracked as **LIN-699**.

---

## 1. What lineai-mcp-server is

A small (~2,200-line) **Python MCP server** that gives AI agents impact-analysis and
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
| `src/lineai_mcp_server/handlers/__init__.py` | 203 | `@server.list_tools` / `@server.call_tool` registration; tool schemas; dispatch; last-resort error wrapper |
| `src/lineai_mcp_server/handlers/method_impact.py` | 427 | `lineai-method-impact` handler + Markdown report builder |
| `src/lineai_mcp_server/handlers/database_impact.py` | 96 | `lineai-database-impact` handler |
| `src/lineai_mcp_server/handlers/graph_tools.py` | 200 | the six `lineai-graph-*` handlers + dispatch table |
| `src/lineai_mcp_server/handlers/common.py` | 54 | `LINEAI_DEBUG_MODE`, debug-log directory, `get_workspace_name()`, timing log |
| `src/lineai_mcp_server/utils.py` | 948 | auth, HTTP client, caches, MV resolution, impact retrieval/shaping, database-report generation |
| `src/lineai_mcp_server/graph_client.py` | 182 | authenticated HTTP wrapper for `/api/ai-retrieval/graph/*` + graph error messages |
| `src/lineai_mcp_server/__init__.py` | 25 | package entry point (`main()` → `asyncio.run(server.main())`) — the `lineai-mcp-server` console script |
| `src/start_server.py` | 42 | **dev-only** entry point: starts a `debugpy` listener on `127.0.0.1:5679`, then runs the same `main()` |
| `test/` | — | 4 unit suites (35 tests, mocked HTTP) + 2 integration suites against a real host |
| `.github/workflows/` | — | `ci.yml`, `bump-version.yml`, `publish-to-pypi.yml` (§9) |
| `context/` | — | reference material (MCP SDK docs, graph tooling design notes) — not shipped code |

Dependency direction is strictly downward: `handlers/*` → `utils.py` /
`graph_client.py` → `httpx`. `graph_client.py` reuses `utils.authenticate` and the
module-level `utils._client`.

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
  `server_version=utils.get_package_version()` (read from `pyproject.toml` at
  runtime — see finding 4), and a static `instructions` string telling agents when
  to use the impact tools and to prefer `lineai-graph-*` when available.

Two launchers exist: the packaged console script `lineai-mcp-server`
(`lineai_mcp_server:main`, no debugger) and `src/start_server.py` (adds `debugpy`;
used by `.vscode/launch.json`).

### 3.1 Dispatch and the last-resort error wrapper

`handle_call_tool(name, arguments)` routes `lineai-method-impact` and
`lineai-database-impact` to their async handlers (awaited) and any name in
`GRAPH_TOOL_DISPATCH` to the **synchronous** graph handlers. Unknown names raise
`ValueError`. Every exception escaping a handler is caught and rendered as a generic
`# Error executing tool: {name}` Markdown message with the exception text — so no
tool call ever crashes the server loop, but errors reaching this wrapper lose all
taxonomy (§7.3).

---

## 4. Tool surface

Eight tools: two report-style impact tools and six JSON-passthrough graph tools.
All tool names are declared in `handlers/__init__.py`; inputs are JSON-Schema
validated by the MCP client.

### 4.1 `lineai-method-impact` (`handlers/method_impact.py`)

The richest handler. Inputs: `method` (enforced by code), `class` (required by the
schema, but the code only *uses* it as a filter — see finding 9; a dotted class name
is reduced to its last segment).

Pipeline (endpoints in call order):

1. **MV resolution** — `get_mv_id(get_workspace_name())`:
   `GET /api/materialized-view-definition/name?name={workspace}` →
   `GET /api/materialized-view/latest?definitionId={defId}`. Resolved on **every**
   call; never cached (finding 12).
2. **Shortname search** — `get_method_nodes(mv_id, method)`:
   `POST /api/ai-retrieval/search/shortname?materializedViewId=...&shortname=...`
   with an *empty body* (a deliberate choice documented in-code: sending
   `Content-Type: application/json` with an empty dict caused gateway 504 stalls
   that Swagger did not reproduce). Returns the `(nodes, error_kind)` tuple (§7.1)
   and populates the method-nodes cache (§6).
3. **Node selection** — if `class` was given, pick the node whose `identity`
   contains `|{class}|` or `|{class}.class|` (no match raises `ValueError` →
   generic wrapper); otherwise the first node.
4. **Impact fetch** — `get_impact(node.properties.id)`:
   `GET /api/dependency/impact/full/{id}/list`, response stripped of a dozen
   internal properties (`strip_unused_properties`) and cached (§6).
5. **Report assembly** — extracts nodes/relationships, finds the target method node
   (preferring one with `statistics.cyclomaticComplexity`), code owners/reviewers
   (`lineai.owners` / `lineai.reviewers`, falling back to the containing class
   node), direct dependents (incoming relationships), affected applications (via
   `Application` nodes, `groupIds`, `GROUPS` and `REFERENCES_GROUP` relationships),
   and REST surface (`Endpoint` nodes, mapping/Http annotations on
   `JavaMethodEntity`/`DotNetMethodEntity`, controller-like labels,
   `INVOKES_ENDPOINT`/`REFERENCES_ENDPOINT` edges).

Output: a Markdown report — AI guidelines, summary (complexity/instruction count,
owners), risk assessment (complexity > 10 flagged, cross-application warning, REST
API alerts), dependents list, a per-node metrics table, a full relationship table
(target rows bolded), and an ASCII application-dependency graph when more than one
application is affected.

### 4.2 `lineai-database-impact` (`handlers/database_impact.py`)

Inputs: `entity_type` (`column` | `table` | `view`), `name`, `table_or_view`
(enforced for columns). Pipeline:

1. **Entity search** — `search_database_entity` (utils):
   `POST /api/ai-retrieval/search/{table|column|view}` with query params
   (`materializedViewId` from `get_mv_id(encoded_workspace_name)` — note this path
   uses the module-level pre-quoted workspace name, unlike every other tool;
   finding 5) and a `{}` JSON body. **All errors are swallowed and returned as
   `[]`** (finding 2).
2. For up to the **first 5** matches: `get_impact(entity_id)` (same endpoint and
   cache as §4.1), then `process_database_entity_impact` derives dependent code
   (direct `REFERENCES`/`USES`/`SELECTS`/... edges, plus table-level references
   marked `indirect (via table)` for columns), referencing database objects
   (`REFERENCES`/`FOREIGN_KEY`), affected applications (direct `GROUPS` plus a
   recursive containment/reference traversal), parent table for columns, and code
   owners/reviewers harvested from dependent code and their containing classes.
   Per-entity failures are logged to stderr and skipped.
3. `generate_combined_database_report` renders one Markdown report: application
   impact, per-entity detail (ownership, dependency tables capped at 10 rows, risk
   tier by dependency count: >20 high / >5 medium / else low, cross-application
   warning), and a static best-practices section.

An empty search yields a `# No {entity_type}s found matching '{name}'` message —
which, because of the error swallowing in step 1, is also what an HTTP failure
produces (finding 2).

### 4.3 `lineai-graph-*` (`handlers/graph_tools.py` + `graph_client.py`)

Six thin tools over `POST`/`GET` `/api/ai-retrieval/graph/{suffix}`. Handlers accept
snake_case (and camelCase alias) arguments, validate required fields, build a
**camelCase** JSON body, inject `materializedViewId` (explicit argument, else
`get_mv_id(get_workspace_name())` — an MV-resolution round-trip per call), and
delegate to `graph_request`. Success responses are returned verbatim as
`# {tool-name}` + fenced, sorted, pretty-printed JSON; the agent does its own
interpretation — unlike §4.1/4.2 there is no report shaping.

| Tool | Method + path suffix | Required args | Optional args |
|---|---|---|---|
| `lineai-graph-capabilities` | `GET /capabilities?materializedViewId=` | — | `materialized_view_id` |
| `lineai-graph-search` | `POST /search` | `query`/`q` or `identity_prefix` | `scan_space`, `prefer_latest_scan`, `limit`, `materialized_view_id` |
| `lineai-graph-impact` | `POST /impact` | `seed_node_ids` (non-empty list) | `direction` (`upstream`\|`downstream`\|`both`), `depth`, `scan_space`, `materialized_view_id` |
| `lineai-graph-path-explain` | `POST /path` | `from_node_id`, `to_node_id` | `max_depth`, `scan_space`, `materialized_view_id` |
| `lineai-graph-validate-change-scope` | `POST /validate-change-scope` | `seed_node_ids`, `proposed_change_summary` | `scan_space`, `materialized_view_id` |
| `lineai-graph-owners` | `POST /owners` | `node_id` or `identity_prefix` | `scan_space`, `materialized_view_id` |

`graph_request` classifies failures into its own error-kind set (§7.2). A 404 is
treated as **"graph tier not deployed"** — the graph API is newer than many Lineai
deployments, and the error message explicitly tells the agent to fall back to
`lineai-method-impact` / `lineai-database-impact`.

---

## 5. Authentication flow (`utils.authenticate`)

Password-grant token auth against the Lineai server, with a client-side cache:

1. If `_cached_token` exists and `datetime.now() < _token_expiry`, return it.
2. Otherwise `POST /api/authenticate` with form-encoded
   `grant_type=password&username={LINEAI_USERNAME}&password={LINEAI_PASSWORD}`.
3. On success, cache `response.json()['access_token']` with expiry
   `now + LINEAI_TOKEN_CACHE_TTL` (default **3600 s**) and return it. On failure,
   log to stderr and **re-raise** (callers decide: `get_method_nodes` converts it
   to an error kind; `get_impact`/`get_mv_*` let it bubble to the generic wrapper).

Every API helper calls `authenticate()` and sends `Authorization: Bearer {token}`.
Notable properties (findings 7): the TTL is purely client-side — the server's
actual `expires_in` is never read — and there is **no 401-triggered invalidation or
retry**; a token revoked or expired server-side before the client TTL elapses makes
every call fail as a generic HTTP error until the hour is up or the process
restarts.

All requests share one module-level synchronous `httpx.Client` configured with
`Timeout(LINEAI_REQUEST_TIMEOUT, connect=LINEAI_CONNECT_TIMEOUT)` (120 s / 30 s
defaults), connection limits (20 keepalive / 30 max), and
`HTTPTransport(retries=3)` — note these are **connect-level** retries only; failed
HTTP responses are never retried.

---

## 6. Caching

Three in-process caches in `utils.py`, all plain dicts guarded only by TTL expiry —
no size bound, no invalidation API, no cross-process sharing (acceptable for a
per-session stdio server, but entries live for the full TTL even if the underlying
graph is rescanned):

| Cache | Key | Value | TTL (env, default) |
|---|---|---|---|
| token (`_cached_token`/`_token_expiry`) | — (single slot) | bearer token | `LINEAI_TOKEN_CACHE_TTL`, 3600 s |
| `_method_nodes_cache` | `"{mv_id}:{short_name}"` | node list from shortname search | `LINEAI_METHOD_CACHE_TTL`, 300 s |
| `_impact_cache` | node `id` | stripped impact JSON **string** | `LINEAI_IMPACT_CACHE_TTL`, 300 s |

Expired entries are detected on read but never evicted (the dict entry is simply
overwritten on the next successful fetch). Asymmetries worth knowing:

- A **404** from the shortname search returns immediately and is *not* cached —
  repeated misses re-query the server.
- A **200 with an empty `data` array** *is* cached, so a "no such method" result of
  that shape sticks for the full 5-minute TTL even if a rescan lands meanwhile
  (part of the LIN-699 cluster, §7.4).
- Materialized-view resolution (`get_mv_id`) is **not cached at all** (finding 12).
- The graph tools (§4.3) have no response caching.

---

## 7. Error-handling taxonomy

### 7.1 The `(nodes, error_kind)` contract — `get_method_nodes`

`get_method_nodes` never raises; it returns `(nodes, error_kind)` where
`error_kind` is `None` on success (**including a 200 with empty `data`**) or one of:

| Kind | Trigger | Handler rendering (`method_impact.py`) |
|---|---|---|
| `not_found` | HTTP **404** from the shortname search (server detail message logged to stderr) | "No method nodes matched this short name in the current workspace materialized view (HTTP 404 NOT_FOUND)…" + recommendations to *"Confirm the method exists in the indexed codebase"*, check `LINEAI_WORKSPACE_NAME`, refresh the view |
| `timeout` | `httpx.TimeoutException` (client-side, after `LINEAI_REQUEST_TIMEOUT`) | "request timed out after {timeout}s (client timeout)" + retry / raise timeout / check network |
| `gateway_timeout` | `httpx.HTTPStatusError` with status **504** | "Lineai API returned 504 Gateway Timeout" + retry hint and a note to compare against Swagger behavior |
| `http_error` | any other `HTTPStatusError` **or any other exception** (including auth failure) | generic "request … failed (timeout, HTTP error, or empty result)" with possible causes and a pointer at stderr logs |

The handler renders one Markdown error page per kind and returns it as the tool
result — the agent always gets a well-formed answer, never a protocol error.

### 7.2 Graph error kinds — `graph_client.py`

The graph path has a parallel taxonomy, computed from the response rather than
exceptions where possible: `not_deployed` (404 — "graph endpoints not deployed on
this host; use lineai-method-impact / lineai-database-impact instead"),
`gateway_timeout` (504), `http_error` (other ≥400, or transport error, or unset
`LINEAI_SERVER_HOST`, or unsupported method), `timeout`
(`httpx.TimeoutException`), and `invalid_json` (2xx body that fails to parse; the
message includes a ≤1500-char excerpt). `graph_error_message` renders each as
Markdown with the failing path and status.

### 7.3 Everything else

`get_impact`, `get_mv_definition_id`, `get_mv_id_from_def`, and argument-validation
`ValueError`s have **no** dedicated handling — they propagate to
`handle_call_tool`'s last-resort wrapper (§3.1) and come back as the generic
`# Error executing tool` page with the raw exception string (finding 11).
`search_database_entity` is the opposite extreme: it catches everything and returns
`[]`, erasing the distinction between "no match" and "server down" (finding 2).

### 7.4 Known issue — LIN-699 (wording, not behavior)

Tracked in Linear as **LIN-699** (*"lineai-method-impact 'not indexed' wording
misreports a correct empty result as an infrastructure problem"*); referenced here
per LIN-737, deliberately **not** fixed by this docs change:

- The `not_found` branch fires on a **correct, healthy** empty result — HTTP 404
  from the shortname search simply means *zero matching method nodes in a populated
  view* (verified server-side in LIN-699: the view in the triggering incident held
  39,678 nodes). But the message's *"Confirm the method exists in the **indexed**
  codebase"* wording gets compressed by agents to "404 — not indexed" and lands in
  PR summaries looking like a Neo4j/indexing failure. The message also never states
  that the view resolved successfully, so a genuine empty result is
  indistinguishable from a broken or empty view.
- **Related gap:** if the server instead returns **200 with an empty `data`
  array**, `error_kind` is `None`, so the handler's `if not nodes` falls into the
  *generic* `http_error`-style else-branch — whose text ("timeout, HTTP error, or
  empty result"; "the method name does not exist…", "server under heavy load…")
  equally missells a correct empty answer as a possible infrastructure problem.
- **And** that empty 200 result is cached for the full `LINEAI_METHOD_CACHE_TTL`
  (§6), so the misleading answer repeats for up to 5 minutes without touching the
  server.

---

## 8. Configuration

All configuration is environment variables (plus optional `.env` via
`python-dotenv`, loaded at `server.py` import unless `LINEAI_TEST_MODE` is set).
Every variable actually read by the code:

| Variable | Default | Read in | Purpose |
|---|---|---|---|
| `LINEAI_SERVER_HOST` | — (required) | `utils.py`, `graph_client.py`, `server.py` | Base URL of the Lineai server, e.g. `https://myco.app.lineai.net`; all `/api/...` paths are appended to it |
| `LINEAI_USERNAME` / `LINEAI_PASSWORD` | — (required) | `utils.authenticate` | Password-grant credentials for `POST /api/authenticate` |
| `LINEAI_WORKSPACE_NAME` | `"default-workspace"` via `get_workspace_name()`; `""` in the module-level `encoded_workspace_name` (finding 5) | `common.py`, `utils.py` | Workspace whose materialized view scopes every search |
| `LINEAI_DEBUG_MODE` | `false` | `handlers/common.py` | `true` writes `timing_log.txt` and raw `impact_data_*.json` to `{tempdir}/lineai-mcp-server` |
| `LINEAI_TOKEN_CACHE_TTL` | `3600` (s) | `utils.py` | Client-side auth-token cache lifetime |
| `LINEAI_METHOD_CACHE_TTL` | `300` (s) | `utils.py` | Shortname-search result cache lifetime |
| `LINEAI_IMPACT_CACHE_TTL` | `300` (s) | `utils.py` | Impact-response cache lifetime |
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
- **Runtime dependencies**: `mcp[cli] >=1.4.0,<2`, `httpx`, `python-dotenv`,
  `toml`, plus `debugpy`, `tenacity`, `pip-licenses`, `httpcore` (pinned to the
  encode git repo via `[tool.uv.sources]`), `anyio` — several of which nothing in
  `src/` imports (finding 3).
- **Version**: `1.3.2`, maintained in **two** files — `pyproject.toml` and
  `server.json` (twice: top level and `packages[0].version`). The MCP
  `server_version` is read from `pyproject.toml` at runtime with a `"0.0.0"`
  fallback (finding 4).
- **CI** (`ci.yml`): on push/PR to `main` — Python **3.13** only — flake8 (hard
  gate restricted to `E9,F63,F7,F82`; everything else `--exit-zero`) and
  `unittest discover -s test -p "unit*.py"` (unit suites only; integration suites
  need a live host and are not run in CI).
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

- **Unit** (CI, mocked HTTP): `unit_test_environment.py` (env handling),
  `unit_test_utils.py` (17 tests: auth/caching, node extraction, report pieces),
  `unit_test_handlers.py` (9: tool listing/dispatch/error paths),
  `unit_test_graph_tools.py` (7: graph handler validation and request shaping).
  `test/test_env.py` injects `DEFAULT_TEST_ENV` (fake host/credentials, 60 s TTLs,
  `LINEAI_TEST_MODE=true`) before package import.
- **Integration** (manual, real host via `test/.env.test`):
  `integration_test_all.py`, and `integration_test_graph.py` which drives the real
  `handle_call_tool` path for capabilities plus a chained
  search → impact → path → validate → owners flow; graph-route 404s **skip** unless
  `LINEAI_GRAPH_E2E_REQUIRED=1`. `scripts/run_graph_e2e.sh` is a one-line wrapper.

---

## 11. Findings (verified 2026-10-05)

1. **LIN-699 — `not_found` wording misreports a correct empty result** (§7.4): the
   404 branch's "indexed codebase" phrasing steers agents to an infrastructure
   framing for a plain "symbol does not exist" answer, and the message never says
   the view itself resolved and is populated. Two adjacent gaps in the same
   cluster: a 200-with-empty-`data` result bypasses the taxonomy entirely and is
   rendered by the generic branch (equally misworded), and that empty result is
   cached for the full `LINEAI_METHOD_CACHE_TTL`. Fix tracked in LIN-699; not
   changed here.
2. **`search_database_entity` swallows every error and returns `[]`**
   (`utils.py`): an auth failure, 5xx, or timeout during the database search is
   rendered by the handler as *"No {entity_type}s found matching '{name}'"* — the
   database tool has no error taxonomy at all, the same class of misreporting as
   finding 1 but losing even the HTTP status.
3. **Declared-but-unused runtime dependencies**: `tenacity`, `pip-licenses`, and
   `anyio` are in `[project.dependencies]` but imported nowhere in `src/` or
   `test/`; `debugpy` is imported only by the dev-only `src/start_server.py` yet is
   a hard install dependency of the published wheel; `httpcore` is force-sourced
   from the encode **git repo** via `[tool.uv.sources]` (and duplicated as the
   sole `dev` dependency-group entry) — an unusual pin that `--no-sources` builds
   deliberately ignore at publish time.
4. **Installed servers report version `0.0.0`**: `get_package_version()` reads
   `pyproject.toml` two directories above `utils.py` — correct in a repo checkout,
   absent in a wheel install (uvx/pip), where the fallback `"0.0.0"` becomes the
   advertised MCP `server_version`. `importlib.metadata.version()` would be exact
   everywhere.
5. **Workspace-name handling is inconsistent across tool families**:
   `lineai-database-impact` resolves its MV from the module-level
   `encoded_workspace_name` (URL-quoted **once at import**, empty string if the
   env var is unset), while `lineai-method-impact` and all graph tools use
   `get_workspace_name()` (raw, un-quoted, `"default-workspace"` fallback) passed
   into an f-string query URL. Different quoting, different fallbacks, and
   import-time capture that ignores later env changes.
6. **Synchronous HTTP inside async handlers**: all network I/O goes through a
   blocking `httpx.Client` called directly from `async def` handlers, so a slow
   call (up to the 120 s timeout) blocks the event loop — tolerable for a
   single-client stdio server, but it serializes everything including protocol
   keepalive.
7. **Token cache ignores the server**: `authenticate()` never reads the token
   response's expiry and has no 401-invalidation path; a token that dies
   server-side before the client-side `LINEAI_TOKEN_CACHE_TTL` (1 h) produces
   generic HTTP errors on every tool until the TTL lapses or the process restarts.
8. **Dead local**: `handle_database_impact` assigns
   `workspace_name = get_workspace_name()` and never uses it (the search path uses
   `encoded_workspace_name`, finding 5). Invisible to CI because the flake8 hard
   gate selects only `E9,F63,F7,F82` (F841 is not among them).
9. **Schema/code disagreement on `lineai-method-impact`**: the input schema marks
   both `method` and `class` required, but the handler enforces only `method`; and
   when `class` is provided but matches no node identity, the raised `ValueError`
   surfaces via the generic wrapper rather than a shaped error page.
10. **Python version matrix drift**: CI tests 3.13 only, `.python-version` and the
    publish workflow use 3.14, and `requires-python` allows both — 3.14 is shipped
    but never tested in CI.
11. **MV resolution and impact fetch have no error taxonomy**: `get_mv_id`'s two
    HTTP calls and `get_impact` raise straight through to the generic
    `# Error executing tool` wrapper (§3.1) — a bad workspace name (404 on the MV
    definition lookup) renders as a raw exception string rather than a
    recommendation-bearing message like the shortname branch gets.
12. **MV resolution is never cached**: every tool call pays two extra HTTP
    round-trips (`materialized-view-definition/name` + `materialized-view/latest`)
    even when the method-nodes and impact caches hit — the workspace→MV mapping is
    the most stable value in the system and the only uncached one.

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

| MCP tool | Endpoint (public path) | neo4cape handler | Notes |
|---|---|---|---|
| all tools (auth) | `POST /api/authenticate` | `AuthController.loginUser` → `AuthService.authenticate` | Form-encoded username/password (the client's extra `grant_type` field is ignored by `LoginRequest`); token cached client-side, `LINEAI_TOKEN_CACHE_TTL` 3600 s (§5) |
| all tools (MV resolution, step 1) | `GET /api/materialized-view-definition/name?name=` | `MaterializedViewDefinitionController.getMaterializedViewDefinitionByName` → `MaterializedViewDefinitionService` | Workspace name → MV definition; uncached on the MCP side (finding 12); errors bypass the taxonomy (§7.3) |
| all tools (MV resolution, step 2) | `GET /api/materialized-view/latest?definitionId=` | `MaterializedViewController.getLatestMaterializedViewByDefinitionId` → `MaterializedViewService.getLatestMaterializedViewObject` | Definition id → latest MV id; same caching/error caveats as step 1 |
| `lineai-method-impact` | `POST /api/ai-retrieval/search/shortname` | `AIRetrievalController.searchByShortname` → `AIRetrievalServiceImpl.findByShortname` → `BaseNodeSearchRepositoryCustomImpl.findNodesByShortname` | Exact `shortName` match on method-entity nodes in the MV; 404 on zero rows. Cached per `mv_id:short_name`, `LINEAI_METHOD_CACHE_TTL` 300 s; maps to §7.1 error kinds (`not_found`/`timeout`/`gateway_timeout`/`http_error`; LIN-699 wording) |
| `lineai-database-impact` | `POST /api/ai-retrieval/search/{table\|column\|view}` | `AIRetrievalController.searchForTableNodes` / `searchForColumnNodes` / `searchForViewNodes` → `AIRetrievalServiceImpl.find{Table,Column,View}Nodes…` (same repository) | Uncached; all client-side errors swallowed to `[]` (§7.3, finding 2) |
| `lineai-method-impact`, `lineai-database-impact` | `GET /api/dependency/impact/full/{id}/list` | `DependencyController.findDependencyImpact2` → `ImpactService.getImpactData` | Full incoming-relationship chain for the node. Cached per node id, `LINEAI_IMPACT_CACHE_TTL` 300 s; errors bypass the taxonomy (§7.3). The controller's optional `viewId` query param is not sent by this client |
| `lineai-graph-capabilities` | `GET /api/ai-retrieval/graph/capabilities?materializedViewId=` | `AIGraphMcpController.capabilities` → `AIGraphMcpServiceImpl` | Graph tier; uncached; §7.2 error kinds (404 → `not_deployed`) |
| `lineai-graph-search` | `POST /api/ai-retrieval/graph/search` | `AIGraphMcpController.search` → `AIGraphMcpServiceImpl.search` | camelCase body; uncached; §7.2 |
| `lineai-graph-impact` | `POST /api/ai-retrieval/graph/impact` | `AIGraphMcpController.impact` → `AIGraphMcpServiceImpl.impact` | "Impact subgraph from seed node ids (merged, capped)"; uncached; §7.2 |
| `lineai-graph-path-explain` | `POST /api/ai-retrieval/graph/path` | `AIGraphMcpController.path` → `AIGraphMcpServiceImpl.path` | Shortest path within the MV; uncached; §7.2 |
| `lineai-graph-validate-change-scope` | `POST /api/ai-retrieval/graph/validate-change-scope` | `AIGraphMcpController.validateChangeScope` → `AIGraphMcpServiceImpl.validateChangeScope` | Deterministic heuristics per the controller's `@Operation` summary; uncached; §7.2 |
| `lineai-graph-owners` | `POST /api/ai-retrieval/graph/owners` | `AIGraphMcpController.owners` → `AIGraphMcpServiceImpl.owners` | "Owner-like fields from node properties (best-effort)"; uncached; §7.2 |

For the full neo4cape API surface see neo4cape's
`docs/neo4cape-architecture/LINEAI-API-REFERENCE.md` (renamed from
`CODELOGIC-API-REFERENCE.md` in neo4cape PR #1857; a July 2026 snapshot).
