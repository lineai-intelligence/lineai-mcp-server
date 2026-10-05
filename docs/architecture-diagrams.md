# lineai-mcp-server — Architecture Diagrams

Companion to [architecture.md](architecture.md); same source-verified content
(`main` @ `6fdff87`, v1.3.2, 2026-10-05) rendered as Mermaid. Committed SVG renders
live in [rendered-diagrams/](rendered-diagrams/index.md), one per section, named by
the section's two-digit prefix.

> Written for Linear ticket **LIN-737**; the error-wording issue in diagram 05 is
> tracked as **LIN-699**.

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
        MV["/api/materialized-view-definition/name<br/>/api/materialized-view/latest"]
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
        HINIT["__init__.py (203)<br/>@list_tools · @call_tool dispatch<br/>last-resort error wrapper"]
        HMI["method_impact.py (427)<br/>lineai-method-impact<br/>Markdown impact report"]
        HDI["database_impact.py (96)<br/>lineai-database-impact<br/>combined DB report"]
        HGT["graph_tools.py (200)<br/>6 × lineai-graph-*<br/>camelCase bodies → fenced JSON"]
        HCOM["common.py (54)<br/>LINEAI_DEBUG_MODE · logs dir<br/>get_workspace_name()"]
    end

    UTILS["utils.py (948)<br/>authenticate + token cache<br/>get_mv_id · get_method_nodes (+cache)<br/>get_impact (+cache) · DB search/report<br/>shared httpx.Client"]
    GC["graph_client.py (182)<br/>graph_request → /api/ai-retrieval/graph/*<br/>graph error messages"]

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
    GC -- "reuses authenticate()<br/>and _client" --> UTILS

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

    A->>H: call_tool(method, class)
    H->>U: get_mv_id(workspace)
    U->>L: POST /api/authenticate (if token cache miss)
    L-->>U: access_token (cached, TTL 3600s)
    U->>L: GET /api/materialized-view-definition/name?name=ws
    U->>L: GET /api/materialized-view/latest?definitionId=…
    Note over U,L: MV resolution runs on EVERY call — never cached
    L-->>U: mv_id

    H->>U: get_method_nodes(mv_id, method)
    alt cache hit (mv_id:method, TTL 300s)
        U-->>H: cached nodes, error_kind=None
    else cache miss
        U->>L: POST /api/ai-retrieval/search/shortname (empty body)
        alt 200 with data
            L-->>U: nodes → cached
            U-->>H: (nodes, None)
        else 200 with EMPTY data
            L-->>U: [] → cached for full TTL
            U-->>H: ([], None) — falls into generic branch (LIN-699 gap)
        else 404
            U-->>H: ([], "not_found") — NOT cached
        else client timeout / 504 / other error
            U-->>H: ([], "timeout" / "gateway_timeout" / "http_error")
        end
    end

    alt nodes empty
        H-->>A: per-kind Markdown error page (§7, LIN-699 wording)
    else nodes found
        H->>H: select node by |class| identity match<br/>(no match → ValueError → generic wrapper)
        H->>U: get_impact(node.properties.id)
        alt impact cache hit (TTL 300s)
            U-->>H: cached stripped JSON
        else miss
            U->>L: GET /api/dependency/impact/full/{id}/list
            L-->>U: impact graph
            U->>U: strip_unused_properties → cache
            U-->>H: JSON string
        end
        H->>H: metrics, owners, dependents,<br/>applications, REST endpoints
        H-->>A: Markdown impact report
    end
```

---

## 04 — Authentication and the three caches

```mermaid
flowchart TD
    CALL["any tool call"] --> MVQ["get_mv_id(workspace)<br/>2 × GET — <b>uncached</b>"]
    MVQ --> AUTHQ{"token cached and<br/>now &lt; expiry?"}
    AUTHQ -- yes --> USE["Bearer token"]
    AUTHQ -- "no (miss / TTL lapsed)" --> POST["POST /api/authenticate<br/>grant_type=password<br/>LINEAI_USERNAME / LINEAI_PASSWORD"]
    POST -- 200 --> STORE["cache access_token<br/>expiry = now + LINEAI_TOKEN_CACHE_TTL (3600s)<br/><i>server expires_in ignored · no 401 invalidation</i>"]
    POST -- failure --> RAISE["raise → caller decides<br/>(taxonomy or generic wrapper)"]
    STORE --> USE

    USE --> C1["_method_nodes_cache<br/>key: mv_id:short_name · TTL 300s<br/><i>caches empty 200 results too;<br/>404 never cached</i>"]
    USE --> C2["_impact_cache<br/>key: node id · TTL 300s<br/>value: stripped JSON string"]
    USE --> C3["graph tools<br/><i>no response cache</i>"]

    HTTP["shared httpx.Client<br/>timeout LINEAI_REQUEST_TIMEOUT 120s<br/>connect LINEAI_CONNECT_TIMEOUT 30s<br/>keepalive 20 / max 30 · connect retries 3"]
    C1 -.-> HTTP
    C2 -.-> HTTP
    C3 -.-> HTTP

    style STORE fill:#fef7e0,stroke:#f9ab00
    style C1 fill:#fce8e6,stroke:#ea4335
    style MVQ fill:#fce8e6,stroke:#ea4335
```

---

## 05 — Error-handling taxonomy (and where it leaks)

```mermaid
flowchart TD
    TC["handle_call_tool(name, args)"] --> MI["lineai-method-impact"]
    TC --> DI["lineai-database-impact"]
    TC --> GT["lineai-graph-*"]

    subgraph MIK["get_method_nodes → (nodes, error_kind)"]
        K404["404 → <b>not_found</b><br/><i>'indexed codebase' wording —<br/>misreports a correct empty result (LIN-699)</i>"]
        KTO["TimeoutException → <b>timeout</b>"]
        K504["HTTP 504 → <b>gateway_timeout</b>"]
        KERR["other HTTP / any exception → <b>http_error</b>"]
        KEMPTY["200 + empty data → error_kind=None,<br/>result CACHED → handler's generic branch<br/><i>(LIN-699 related gap)</i>"]
    end
    MI --> MIK
    MIK --> MIPAGE["per-kind Markdown error page<br/>returned as tool result"]

    DI --> DSE["search_database_entity:<br/>catches EVERYTHING → returns []"]
    DSE --> DIPAGE["'No {type}s found matching …'<br/><i>errors indistinguishable from empty results</i>"]

    subgraph GTK["graph_request → error kind"]
        G404["404 → <b>not_deployed</b><br/>'use method/database impact instead'"]
        G504["504 → <b>gateway_timeout</b>"]
        GTO["TimeoutException → <b>timeout</b>"]
        GJSON["2xx non-JSON → <b>invalid_json</b> (+ excerpt)"]
        GERR["other ≥400 / transport / no host → <b>http_error</b>"]
    end
    GT --> GTK
    GTK --> GTPAGE["graph_error_message →<br/>Markdown page with path + status"]

    UNTYPED["get_mv_id · get_impact · ValueErrors:<br/>no taxonomy — raise straight through"]
    MI -.-> UNTYPED
    DI -.-> UNTYPED
    GT -.-> UNTYPED
    UNTYPED --> WRAP["last-resort wrapper in handlers/__init__.py:<br/># Error executing tool: {name}<br/>raw exception text"]

    style K404 fill:#fce8e6,stroke:#ea4335
    style KEMPTY fill:#fce8e6,stroke:#ea4335
    style DSE fill:#fce8e6,stroke:#ea4335
    style WRAP fill:#fef7e0,stroke:#f9ab00
```
