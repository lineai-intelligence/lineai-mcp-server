# lineai-mcp-server — Architecture Diagrams

Companion to [architecture.md](architecture.md); same source-verified content
(`main` @ `e539ba9`, v1.3.2, 2026-10-05) rendered as Mermaid. Committed SVG renders
live in [rendered-diagrams/](rendered-diagrams/index.md), one per section, named by
the section's two-digit prefix.

> Written for Linear ticket **LIN-737**. **Updated for LIN-699** (PR #61 in this
> repo; server side neo4cape PR #1860): diagrams 03–05 now show the typed
> `LineaiApiError` taxonomy with `isError=True`, the honest no-match routing,
> the 401/403 auth retry with `expires_in` handling, the MV-resolution cache,
> and `viewId`-scoped impact fetches.

---

## 01 — Ecosystem: where lineai-mcp-server sits

```mermaid
flowchart LR
    subgraph CLIENTS["MCP clients (stdio)"]
        AGENTS["Claude agent sessions<br/><i>claude-code-container / lineai-agent images:<br/>git clone → claude mcp add-json →<br/>uvx --directory /claude/lineai-mcp-server</i>"]
        IDES["IDE assistants<br/><i>VS Code · Cursor · Claude Desktop · Windsurf<br/>uvx lineai-mcp-server@latest (PyPI)</i>"]
    end

    MCP["lineai-mcp-server v1.3.2<br/><i>Python · mcp.server.Server · stdio only<br/>8 tools · in-process caches · no own graph logic</i>"]

    subgraph LINEAI["Lineai server (neo4cape) — LINEAI_SERVER_HOST"]
        AUTH["/api/authenticate"]
        MV["/api/materialized-view-definition/name<br/>/api/materialized-view/latest<br/>/api/materialized-view/default"]
        SEARCH["/api/ai-retrieval/search/*<br/>(shortname · table · column · view)"]
        IMPACT["/api/dependency/impact/full/{id}/list"]
        GRAPH["/api/ai-retrieval/graph/*<br/><i>404 until graph tier is deployed</i>"]
    end

    REG["PyPI + official MCP registry<br/><i>publish-to-pypi.yml · server.json</i>"]

    AGENTS -- "JSON-RPC over stdin/stdout" --> MCP
    IDES -- "JSON-RPC over stdin/stdout" --> MCP
    MCP -- "Bearer-token HTTPS<br/>(httpx, 120 s timeout)" --> AUTH
    MCP --> MV
    MCP --> SEARCH
    MCP --> IMPACT
    MCP --> GRAPH
    REG -. "distribution" .-> IDES

    style MCP fill:#fef7e0,stroke:#f9ab00
    style GRAPH fill:#fce8e6,stroke:#ea4335
```

---

## 02 — Module map

```mermaid
flowchart TD
    subgraph ENTRY["Entry points"]
        PKG["__init__.py — main()<br/><i>console script lineai-mcp-server</i>"]
        DBG["src/start_server.py<br/><i>dev only: + debugpy :5679</i>"]
    end

    SRV["server.py (88)<br/>Server('lineai-mcp-server') · load_dotenv<br/>stdio_server loop · instructions string"]

    subgraph HANDLERS["handlers/"]
        HINIT["__init__.py (249)<br/>@list_tools · @call_tool<br/>dispatch via asyncio.to_thread<br/>LineaiApiError rendering + last-resort wrapper"]
        HMI["method_impact.py (459)<br/>lineai-method-impact<br/>Markdown impact report + UR section"]
        HDI["database_impact.py (168)<br/>lineai-database-impact<br/>combined DB report + UR section"]
        HGT["graph_tools.py (188)<br/>6 × lineai-graph-*<br/>camelCase bodies → fenced JSON"]
        HCOM["common.py (145)<br/>LINEAI_DEBUG_MODE · logs dir<br/>error_result (isError=True)<br/>render_lineai_api_error"]
    end

    UTILS["utils.py (1388)<br/>LineaiApiError · _authed_request (401/403 retry)<br/>authenticate + token cache (expires_in)<br/>resolve_mv_id (+cache) · get_method_nodes (+cache)<br/>get_impact (+cache, viewId) · UR extraction<br/>DB search/report · shared httpx.Client"]
    GC["graph_client.py (176)<br/>graph_request → /api/ai-retrieval/graph/*<br/>graph error messages"]

    PKG --> SRV
    DBG --> SRV
    SRV -- "import side effect<br/>registers decorators" --> HINIT
    HINIT --> HMI
    HINIT --> HDI
    HINIT --> HGT
    HMI --> HCOM
    HDI --> HCOM
    HGT --> HCOM
    HMI --> UTILS
    HDI --> UTILS
    HGT --> UTILS
    HGT --> GC
    GC -- "reuses _authed_request()<br/>and _client" --> UTILS

    style UTILS fill:#e8f0fe,stroke:#4285f4
    style HINIT fill:#fef7e0,stroke:#f9ab00
```

---

## 03 — `lineai-method-impact` call sequence (richest path, with error branches)

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (MCP client)
    participant H as handle_method_impact
    participant U as utils.py
    participant L as Lineai /api

    A->>H: call_tool(method, class?, workspace?, materialized_view_id?)
    H->>U: resolve_mv_id(arguments)
    Note over U: precedence: explicit MV id → workspace arg →<br/>LINEAI_WORKSPACE_NAME → server default
    alt explicit materialized_view_id given
        U-->>H: mv_id (no resolution needed)
    else MV cache hit (name or server-default slot, TTL 300s)
        U-->>H: cached mv_id
    else cache miss
        U->>L: POST /api/authenticate (if token cache miss)
        L-->>U: access_token (+ expires_in, cached §5)
        alt workspace name known
            U->>L: GET /api/materialized-view-definition/name?name=ws
            U->>L: GET /api/materialized-view/latest?definitionId=…
        else nothing specified
            U->>L: GET /api/materialized-view/default (single call)
        end
        L-->>U: mv_id → _mv_cache (LINEAI_MV_CACHE_TTL)
        Note over U,L: 404 → LineaiApiError mv_not_found / mv_no_default<br/>→ per-kind page, isError=true
    end

    H->>U: get_method_nodes(mv_id, method)
    alt cache hit (mv_id:method, TTL 300s)
        U-->>H: cached nodes, error_kind=None
    else cache miss
        U->>L: POST /api/ai-retrieval/search/shortname (empty body,<br/>via _authed_request: one retry on 401/403)
        alt 200 with data
            L-->>U: nodes (may include URs) → cached
            U-->>H: (nodes, None)
        else 200 with EMPTY data
            U-->>H: ([], None) — NOT cached
        else 404
            U-->>H: ([], "not_found") — NOT cached
        else client timeout / 504 / other error
            U-->>H: ([], "timeout" / "gateway_timeout" / "http_error")
        else auth failure after retry
            U--xH: LineaiApiError("auth") → dispatcher page, isError=true
        end
    end

    alt no nodes — 404 OR empty 200
        H-->>A: honest no-match page, isError=true<br/>("view {mv_id} resolved, no method matched — not infrastructure")
    else no nodes — timeout / 504 / http_error
        H-->>A: per-kind infrastructure-error page, isError=true<br/>("says nothing about whether the method exists")
    else nodes found
        H->>H: class filter — case-insensitive substring on identity<br/>(no match → candidates page, isError=true)
        H->>U: get_impact(node.properties.id, mv_id)
        alt impact cache hit ({id}:{mv_id}, TTL 300s)
            U-->>H: cached stripped JSON
        else miss
            U->>L: GET /api/dependency/impact/full/{id}/list?viewId={mv_id}
            L-->>U: impact graph (may include UR nodes)
            U->>U: strip_unused_properties (URs untouched) → cache
            U-->>H: JSON string
        end
        H->>H: metrics, owners, dependents, applications,<br/>REST endpoints, unresolved-reference section
        H-->>A: Markdown impact report
    end
```

---

## 04 — Authentication and the four caches

```mermaid
flowchart TD
    CALL["any tool call"] --> MVQ["resolve_mv_id(arguments)<br/>explicit id → workspace arg →<br/>LINEAI_WORKSPACE_NAME → server default<br/><b>cached: _mv_cache, LINEAI_MV_CACHE_TTL 300s</b>"]
    MVQ --> REQ["_authed_request(method, url)<br/><i>the ONLY place auth headers are attached</i>"]

    REQ --> AUTHQ{"token cached and<br/>now &lt; expiry?"}
    AUTHQ -- yes --> USE["Bearer token"]
    AUTHQ -- "no (miss / TTL lapsed)" --> POST["POST /api/authenticate<br/>grant_type=password<br/>LINEAI_USERNAME / LINEAI_PASSWORD"]
    POST -- 200 --> STORE["cache access_token<br/>TTL = min(LINEAI_TOKEN_CACHE_TTL 3600s,<br/>expires_in − 30s margin)"]
    POST -- failure --> RAISE["LineaiApiError('auth' / 'timeout')<br/>→ per-kind page, isError=true"]
    STORE --> USE

    USE --> RESP{"response status?"}
    RESP -- "401 or 403 (first time)" --> INV["invalidate_token()<br/>retry ONCE with fresh token<br/><i>server answers bad tokens with 403, not 401</i>"]
    INV --> POST
    RESP -- "401/403 again" --> RAISE
    RESP -- "2xx / other" --> CACHES["response → caller"]

    CACHES --> C0["_mv_cache<br/>key: workspace name or '&lt;server-default&gt;'<br/>TTL 300s (LINEAI_MV_CACHE_TTL)"]
    CACHES --> C1["_method_nodes_cache<br/>key: mv_id:short_name · TTL 300s<br/><i>non-empty results only;<br/>404 and empty 200 never cached</i>"]
    CACHES --> C2["_impact_cache<br/>key: id:mv_id · TTL 300s<br/>value: stripped JSON string"]
    CACHES --> C3["graph tools<br/><i>no response cache</i>"]

    HTTP["shared httpx.Client<br/>timeout LINEAI_REQUEST_TIMEOUT 120s<br/>connect LINEAI_CONNECT_TIMEOUT 30s<br/>keepalive 20 / max 30 · connect retries 3"]
    C1 -.-> HTTP
    C2 -.-> HTTP
    C3 -.-> HTTP

    style STORE fill:#e6f4ea,stroke:#34a853
    style INV fill:#fef7e0,stroke:#f9ab00
    style MVQ fill:#e6f4ea,stroke:#34a853
    style REQ fill:#e8f0fe,stroke:#4285f4
```

---

## 05 — Error-handling taxonomy (honest since LIN-699 / PR #61)

```mermaid
flowchart TD
    TC["handle_call_tool(name, args)<br/><i>every error path returns CallToolResult(isError=true)</i>"] --> MI["lineai-method-impact"]
    TC --> DI["lineai-database-impact"]
    TC --> GT["lineai-graph-*"]

    subgraph API["LineaiApiError(kind, status, detail, endpoint) — utils.py"]
        KA["<b>auth</b> — 401/403 after one retry,<br/>or authenticate() failure"]
        KMV["<b>mv_not_found</b> — unknown workspace name<br/><b>mv_no_default</b> — no server default view"]
        KT["<b>timeout</b> · <b>gateway_timeout</b> (504)"]
        KH["<b>http_error</b> — other ≥400 / transport<br/><b>invalid_response</b> — malformed payload"]
    end
    API --> DPAGE["render_lineai_api_error:<br/>per-kind Markdown page with endpoint,<br/>status, detail + recommendations"]

    subgraph MIK["get_method_nodes → (nodes, error_kind)"]
        KOK["404 OR empty 200 →<br/><b>honest no-match page</b>:<br/>'view {mv_id} resolved; no method matched —<br/>not an index or infrastructure problem'"]
        KINfra["timeout / gateway_timeout / http_error →<br/>infrastructure page: 'says nothing about<br/>whether the method exists'"]
        KCLASS["class filter miss →<br/>candidates page (up to 20 identities)"]
    end
    MI --> MIK
    MI -. "auth raises" .-> API

    DI --> DSE["search_database_entity → (results, error_kind):<br/>honest infra pages; no-match page names the<br/>resolved view; per-entity impact failures →<br/>'Impact retrieval failures' table"]
    DI -. "auth / get_impact raise" .-> API

    subgraph GTK["graph_request → error kind"]
        G404["404 → <b>not_deployed</b><br/>'use method/database impact instead'"]
        G504["504 → <b>gateway_timeout</b>"]
        GTO["timeout → <b>timeout</b>"]
        GJSON["2xx non-JSON → <b>invalid_json</b> (+ excerpt)"]
        GERR["other ≥400 / transport / no host → <b>http_error</b>"]
    end
    GT --> GTK
    GT -. "auth raises" .-> API
    GTK --> GTPAGE["graph_error_message →<br/>Markdown page with path + status"]

    MVRES["resolve_mv_id · get_impact:<br/>raise typed LineaiApiError"] --> API
    UNTYPED["argument-validation ValueErrors only"] --> WRAP["last-resort wrapper:<br/># Error executing tool: {name}<br/>raw exception text, isError=true"]

    style KOK fill:#e6f4ea,stroke:#34a853
    style DSE fill:#e6f4ea,stroke:#34a853
    style DPAGE fill:#e8f0fe,stroke:#4285f4
    style WRAP fill:#fef7e0,stroke:#f9ab00
```
