# lineai-mcp-server

An [MCP Server](https://modelcontextprotocol.io/introduction) to utilize Lineai's rich software dependency data in your AI programming assistant.

## Documentation

Code-derived architecture documentation lives in [`docs/`](docs/) (added in
LIN-737; updated for the LIN-699 changes — PR #61 / neo4cape #1860):

- [docs/architecture.md](docs/architecture.md) — full architecture analysis: module map, tool surface and the Lineai `/api/...` endpoints each tool calls, auth flow, caching, error-handling taxonomy, configuration, packaging/release, and findings.
- [docs/architecture-diagrams.md](docs/architecture-diagrams.md) — Mermaid diagram set (ecosystem, module map, call sequence, auth/caching, error taxonomy).
- [docs/rendered-diagrams/](docs/rendered-diagrams/index.md) — committed SVG renders of every diagram.

## Components

### Tools

The server implements **eight** tools: two impact tools plus six **graph** tools backed by the Lineai graph HTTP API.

#### Code Analysis Tools
- **lineai-method-impact**: Pulls an impact assessment from the Lineai server's APIs for your code.
  - Takes the given "method" that you're working on. Pass the **fully-qualified** class name in "class" (e.g. `com.acme.shell.AnalyzeCommands`) when the simple name may exist in several repositories: a fully-qualified class selects exactly that class, while a simple name that matches several classes returns an ambiguity page listing the candidates instead of silently analyzing another repository's class.
  - The impact is a **bounded upstream traversal** from the Lineai graph impact endpoint (`depth` argument, default 3, max 10). The report's **Impact scope** section says where the data came from (graph, or the legacy full-chain endpoint when the graph endpoint is not deployed) and whether it is complete (`ok`) or a lower bound (`partial`) — a partial result is never "no impact".
- **lineai-database-impact**: Analyzes impacts between code and database entities.
  - Takes the database entity type (column, table, or view) and its name.

All eight tools also accept optional `workspace` and `materialized_view_id` arguments to scope a single call; when neither is given (and `LINEAI_WORKSPACE_NAME` is unset) the server's default workspace's latest materialized view is used automatically.

#### Graph API tools

These call `POST` / `GET` endpoints under `/api/ai-retrieval/graph/` on the same host as `LINEAI_SERVER_HOST`, using the same session auth as other MCP tools. If graph routes are not deployed, the server returns a clear “graph not available” style message (often after HTTP 404).

- **lineai-graph-capabilities**: `GET` — discover supported relationship types, limits, and flags for the workspace materialized view (`materializedViewId` resolved like every other tool: explicit id → `workspace` argument → `LINEAI_WORKSPACE_NAME` → server default).
- **lineai-graph-search**: Search nodes by text `query` / `q` and/or `identity_prefix`; optional `scan_space` (scan space id or display name), `prefer_latest_scan`, `limit`. Results are **ranked by the server** (exact short name → exact fully-qualified name → class-like labels → text score → newest scan) and `data.hits[i]` describes `data.nodes[i]` (`score`, `matchKind`, `labelTier`); `data.totalHitCount` is the real total, so a full page means "narrow the query", not "absent".
- **lineai-graph-impact**: Bounded dependency / blast-radius traversal from `seed_node_ids` with `direction` (`upstream` = what depends on the seeds, `downstream`, `both`) and `depth` (server default 3, max 10). The response carries `status` (`ok` | `partial`), `truncated`, `nodeCount`, `staticAnalysisGap` and `assumptionsApplied` markers; the tool prints a note above the JSON whenever the result is partial, degraded, or an older server ignored an option.
- **lineai-graph-path-explain**: Shortest-path style explanation between `from_node_id` and `to_node_id`.
- **lineai-graph-validate-change-scope**: Heuristic checklist / risk summary for a proposed change given seed nodes and `proposed_change_summary`.
- **lineai-graph-owners**: Resolve a node by `node_id` or `identity_prefix` and surface property fields whose names contain `"owner"`.

Tool arguments accept **snake_case** aliases (for example `materialized_view_id`, `seed_node_ids`) where noted in the MCP schema; request bodies sent to Lineai use **camelCase** JSON keys.

### Install

#### Pre Requisites

The MCP server relies upon Astral UV to run, please [install](https://docs.astral.sh/uv/getting-started/installation/)

### MacOS Workaround for uvx

There is a known issue with `uvx` on **MacOS** where the Lineai MCP server may fail to launch in certain IDEs (such as Cursor), resulting in errors like:
See [issue #11](https://github.com/lineai-intelligence/lineai-mcp-server/issues/11)
```
Failed to connect client closed
```

This appears to be a problem with Astral `uvx` running on MacOS. The following can be used as a workaround:

1. Clone this project locally.
2. Configure your `mcp.json` to use `uv` instead of `uvx`. For example:

```json
{
  "mcpServers": {
    "lineai-mcp-server": {
      "type": "stdio",
      "command": "<PATH_TO_UV>/uv",
      "args": [
        "--directory",
        "<PATH_TO_THIS_REPO>/lineai-mcp-server-main",
        "run",
        "lineai-mcp-server"
      ],
      "env": {
        "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
        "LINEAI_USERNAME": "<my username>",
        "LINEAI_PASSWORD": "<my password>",
        "LINEAI_WORKSPACE_NAME": "<my workspace>",
        "LINEAI_DEBUG_MODE": "true"
      }
    }
  }
}
```

3. Restart Cursor.
4. Ensure the Cursor Global Rule for Lineai is in place.
5. Open the MCP tab in Cursor and refresh the `lineai-mcp-server`.
6. Ask Cursor to make a code change in an existing class. The MCP server should now run the impact analysis successfully.

## Configuration for Different IDEs

### Visual Studio Code Configuration

To configure this MCP server in VS Code:

1. First, ensure you have GitHub Copilot agent mode enabled in VS Code.

2. Create a `.vscode/mcp.json` file in your workspace with the following configuration:

```json
{
  "servers": {
    "lineai-mcp-server": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "lineai-mcp-server@latest"
      ],
      "env": {
        "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
        "LINEAI_USERNAME": "<my username>",
        "LINEAI_PASSWORD": "<my password>",
        "LINEAI_WORKSPACE_NAME": "<my workspace>",
        "LINEAI_DEBUG_MODE": "true"
      }
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

3. Alternatively, you can run the `MCP: Add Server` command from the Command Palette and provide the server information.

4. To manage your MCP servers, use the `MCP: List Servers` command from the Command Palette.

5. Once configured, the server's tools will be available to Copilot agent mode. You can toggle specific tools on/off as needed by clicking the Tools button in the Chat view when in agent mode.

6. To use the Lineai tools in agent mode, you can specifically ask about code impacts or database relationships, and the agent will utilize the appropriate tools.

### Claude Desktop Configuration

Configure Claude Desktop by editing the configuration file:

- On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`
- On Windows: `%APPDATA%/Claude/claude_desktop_config.json`
- On Linux: `~/.config/Claude/claude_desktop_config.json`

Add the following to your configuration file:

```json
"mcpServers": {
  "lineai-mcp-server": {
    "command": "uvx",
    "args": [
      "lineai-mcp-server@latest"
    ],
    "env": {
      "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
      "LINEAI_USERNAME": "<my username>",
      "LINEAI_PASSWORD": "<my password>",
      "LINEAI_WORKSPACE_NAME": "<my workspace>"
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

After adding the configuration, restart Claude Desktop to apply the changes.

### Windsurf IDE Configuration

To run this MCP server with [Windsurf IDE](https://codeium.com/windsurf):

**Configure Windsurf IDE**:

To configure Windsurf IDE, you need to create or modify the `~/.codeium/windsurf/mcp_config.json` configuration file.

Add the following configuration to your file:

```json
"mcpServers": {
  "lineai-mcp-server": {
    "command": "uvx",
    "args": [
      "lineai-mcp-server@latest"
    ],
    "env": {
      "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
      "LINEAI_USERNAME": "<my username>",
      "LINEAI_PASSWORD": "<my password>",
      "LINEAI_WORKSPACE_NAME": "<my workspace>"
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

After adding the configuration, restart Windsurf IDE or refresh the tools to apply the changes.

### Cursor Configuration

To configure the Lineai MCP server in Cursor:

1. Configure the MCP server by creating a `.cursor/mcp.json` file:

```json
{
  "mcpServers": {
    "lineai-mcp-server": {
      "command": "uvx",
      "args": [
        "lineai-mcp-server@latest"
      ],
      "env": {
        "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
        "LINEAI_USERNAME": "<my username>",
        "LINEAI_PASSWORD": "<my password>",
        "LINEAI_WORKSPACE_NAME": "<my workspace>",
        "LINEAI_DEBUG_MODE": "true"
      }
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

2. Restart Cursor to apply the changes.

The Lineai MCP server tools will now be available in your Cursor workspace.

## AI Assistant Instructions/Rules

To help the AI assistant use the Lineai tools effectively, you can add the following instructions/rules to your client's configuration. We recommend customizing these instructions to align with your team's specific coding standards, best practices, and workflow requirements:

When the graph API is available on your Lineai host, extend your rules with the same guidance the server already advertises in its MCP `instructions`: use **`lineai-graph-*`** tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; if graph calls fail with “not available”, fall back to **lineai-method-impact** / **lineai-database-impact**.

### VS Code (GitHub Copilot) Instructions

Create a `.vscode/copilot-instructions.md` file with the following content:

```markdown
# Lineai MCP Server Instructions

When modifying existing code methods:
- Use lineai-method-impact to analyze code changes
- Use lineai-database-impact for database modifications
- When the Lineai graph API is available, use lineai-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use lineai-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the Lineai tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer lineai-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Claude Desktop Instructions

Create a file `~/.claude/instructions.md` with the following content:

```markdown
# Lineai MCP Server Instructions

When modifying existing code methods:
- Use lineai-method-impact to analyze code changes
- Use lineai-database-impact for database modifications
- When the Lineai graph API is available, use lineai-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use lineai-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the Lineai tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer lineai-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Windsurf IDE Rules

Create or modify the `~/.codeium/windsurf/memories/global_rules.md` markdown file with the following content:

```markdown
When modifying existing code methods:
- Use lineai-method-impact to analyze code changes
- Use lineai-database-impact for database modifications
- When the Lineai graph API is available, use lineai-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use lineai-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the Lineai tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer lineai-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Cursor Global Rule

To configure Lineai rules in Cursor:

1. Open Cursor Settings
2. Navigate to the "Rules" section
3. Add the following content to "User Rules":

```markdown
# Lineai MCP Server Rules
## Codebase
- The Lineai MCP Server is for java, javascript, typescript, and C# dotnet codebases
- don't run the tools on python or other non supported codebases
## AI Assistant Behavior
- When modifying existing code methods:
  - Use lineai-method-impact to analyze code changes
  - Use lineai-database-impact for database modifications
  - When the Lineai graph API is available, use lineai-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
  - Highlight impact results for the modified methods
- When modifying SQL code or database entities:
  - Always use lineai-database-impact to analyze potential impacts
  - Highlight impact results for the modified database entities
- To use the Lineai tools effectively:
  - For code impacts: Ask about specific methods or functions
  - For database relationships: Ask about tables, views, or columns
  - Review the impact results before making changes
  - Consider both direct and indirect impacts
```

## Environment Variables

The following environment variables can be configured to customize the behavior of the server:

- `LINEAI_SERVER_HOST`: The URL of the Lineai server (required). All API paths (`/api/authenticate`, `/api/ai-retrieval/...`, `/api/dependency/...`, `/api/materialized-view...`) are appended to it.
- `LINEAI_USERNAME`: Your Lineai username (required).
- `LINEAI_PASSWORD`: Your Lineai password (required).
- `LINEAI_WORKSPACE_NAME`: The default workspace to use. Optional: individual tool calls can override it with `workspace` / `materialized_view_id` arguments, and when nothing is specified at all the server's default workspace's latest materialized view is used.
- `LINEAI_DEBUG_MODE`: Set to `true` to enable debug mode. When enabled, additional debug files such as `timing_log.txt` and `impact_data*.json` will be generated. Defaults to `false`.

**Tuning (optional — read once at server startup)**

- `LINEAI_TOKEN_CACHE_TTL`: Authentication-token cache lifetime in seconds. Defaults to `3600`.
- `LINEAI_METHOD_CACHE_TTL`: Method shortname-search result cache lifetime in seconds. Defaults to `300`.
- `LINEAI_IMPACT_CACHE_TTL`: Impact-analysis result cache lifetime in seconds. Defaults to `300`.
- `LINEAI_MV_CACHE_TTL`: Materialized-view resolution cache lifetime in seconds (workspace name / server default → view id). Defaults to `300`.
- `LINEAI_REQUEST_TIMEOUT`: Overall HTTP request timeout in seconds for all Lineai API calls. Defaults to `120.0`. Graph impact calls are bounded server-side (depth / node / relationship caps, 60 s query timeout), so a timeout here usually means the depth is too large — retry with `depth` 1–2.
- `LINEAI_CONNECT_TIMEOUT`: HTTP connect timeout in seconds. Defaults to `30.0`.
- `LINEAI_METHOD_IMPACT_BACKEND`: `graph` (default) fetches the `lineai-method-impact` subgraph from the bounded graph impact endpoint and falls back to the legacy `/api/dependency/impact/full` list only when the graph **route** is missing (a 404 without neo4cape's entity message); a 404 that names the node ("Unable to locate node …") is reported as a search/view disagreement instead, because the legacy traversal from an unknown id returns an empty result that would read as "isolated"; `legacy` forces the legacy endpoint (unbounded full incoming chain). Read per call.

**Tests only**

- `LINEAI_TEST_MODE`: When set (any value), the server skips loading a `.env` file so tests fully control the environment.
- `LINEAI_GRAPH_E2E_REQUIRED`: Set to `1` when running graph MCP integration tests if you want missing graph APIs (HTTP 404 / “Graph API not available”) to **fail** the suite instead of **skipping** those tests.

### Example Configuration

```json
"env": {
  "LINEAI_SERVER_HOST": "<url to the server e.g. https://myco.app.lineai.net>",
  "LINEAI_USERNAME": "<my username>",
  "LINEAI_PASSWORD": "<my password>",
  "LINEAI_WORKSPACE_NAME": "<my workspace>",
  "LINEAI_DEBUG_MODE": "true"
}
```

### Pinning the version

instead of using the **latest** version of the server, you can pin to a specific version by changing the **args** field to match the version in [pypi](https://pypi.org/project/lineai-mcp-server/) e.g.

```json
    "args": [
      "lineai-mcp-server@0.2.2"
    ],
```

### Version Compatibility

This MCP server has the following version compatibility requirements:

- Version 0.3.1 and below: Compatible with all Lineai API versions
- Version 0.4.0 and above: Requires Lineai API version 25.10.0 or greater

If you're upgrading, make sure your Lineai server meets the minimum API version requirement.

**Graph tools**: Require your Lineai deployment to serve the graph endpoints under `/api/ai-retrieval/graph/`. Older or partial deployments may return 404; the MCP tools surface that as a clear error instead of opaque failures.

**Ranked search / bounded impact (LIN-870)**: ranking, `hits`, the real `totalHitCount`, `scan_space` / `prefer_latest_scan`, and the `depth` / `direction` / `status: partial` impact contract need a Lineai server that includes neo4cape LIN-870. Against an older server the tools still work: search comes back in the server's unranked order, impact runs the full subgraph, and the ignored options are echoed as `*_not_implemented` / `*_reserved_*` markers that the tool renders as "`x` was ignored (older server)". `lineai-method-impact` falls back to the legacy impact endpoint automatically when the graph endpoint is missing.

## Debug Logging

When `LINEAI_DEBUG_MODE=true`, debug files are written to the system temporary directory:

- **Windows**: `%TEMP%\lineai-mcp-server` (typically `C:\Users\{username}\AppData\Local\Temp\lineai-mcp-server`)
- **macOS**: `/tmp/lineai-mcp-server` (or `$TMPDIR/lineai-mcp-server` if set)  
- **Linux**: `/tmp/lineai-mcp-server` (or `$TMPDIR/lineai-mcp-server` if set)

**Debug files include**:
- `timing_log.txt` - Performance timing information
- `impact_data_*.json` - Raw impact analysis data for troubleshooting

**Finding your log directory**:
```python
import tempfile
import os
print("Log directory:", os.path.join(tempfile.gettempdir(), "lineai-mcp-server"))
```

## Testing

### Running Unit Tests

The project uses unittest for testing. You can run unit tests without any external dependencies:

```bash
python -m unittest discover -s test -p "unit_*.py"
```

Unit tests use mock data and don't require a connection to a Lineai server. `test/unit_test_handlers.py` includes the `TestMethodNodeSelection` table (the LIN-870 `AnalyzeCommands`-in-three-repositories case: fully-qualified class → the right repository, simple name → ambiguity page, foreign fully-qualified name → not-found page, unresolved references never chosen) and the graph-backend / legacy-fallback / partial-result handler cases; `test/unit_test_graph_tools.py` covers the partiality note and old-server markers.

### Integration Tests (Optional)

If you want to run integration tests that connect to a real Lineai server:

1. Copy `test/.env.test.example` to `test/.env.test` and populate with your Lineai server details
2. Run the integration tests:

```bash
python -m unittest discover -s test -p "integration_*.py"
```

Note: Integration tests require access to a Lineai server instance.

### Graph MCP end-to-end tests

`test/integration_test_graph.py` drives the real MCP handler path (`handle_call_tool`) for **`lineai-graph-capabilities`** and a chained flow (search → impact → path → validate → owners) against `LINEAI_SERVER_HOST`. Configure credentials the same way as other integration tests (`test/.env.test` from `test/.env.test.example`).

- If the host does not expose graph routes, tests **skip** by default.
- Set **`LINEAI_GRAPH_E2E_REQUIRED=1`** to turn missing graph APIs into hard failures (useful in CI when graph must be present).

From the repo root:

```bash
./scripts/run_graph_e2e.sh
```

Equivalent:

```bash
uv run python -m unittest test.integration_test_graph -v
```

## Validation for Official MCP Registry

mcp-name: io.github.lineai-intelligence/lineai-mcp-server
