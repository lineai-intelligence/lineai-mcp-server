# codelogic-mcp-server

An [MCP Server](https://modelcontextprotocol.io/introduction) to utilize Codelogic's rich software dependency data in your AI programming assistant.

## Components

### Tools

The server implements **eight** tools: two impact tools plus six **graph** tools backed by the CodeLogic graph HTTP API.

#### Code Analysis Tools
- **codelogic-method-impact**: Pulls an impact assessment from the CodeLogic server's APIs for your code.
  - Takes the given "method" that you're working on and its associated "class".
- **codelogic-database-impact**: Analyzes impacts between code and database entities.
  - Takes the database entity type (column, table, or view) and its name.

#### Graph API tools

These call `POST` / `GET` endpoints under `/codelogic/server/ai-retrieval/graph/` on the same host as `CODELOGIC_SERVER_HOST`, using the same session auth as other MCP tools. If graph routes are not deployed, the server returns a clear “graph not available” style message (often after HTTP 404).

- **codelogic-graph-capabilities**: `GET` — discover supported relationship types, limits, and flags for the workspace materialized view (`materializedViewId` defaults from `CODELOGIC_WORKSPACE_NAME` like other tools).
- **codelogic-graph-search**: Search nodes by text `query` / `q` and/or `identity_prefix`; optional `scan_space`, `limit`, etc.
- **codelogic-graph-impact**: Dependency / blast-radius style traversal from `seed_node_ids`.
- **codelogic-graph-path-explain**: Shortest-path style explanation between `from_node_id` and `to_node_id`.
- **codelogic-graph-validate-change-scope**: Heuristic checklist / risk summary for a proposed change given seed nodes and `proposed_change_summary`.
- **codelogic-graph-owners**: Resolve a node by `node_id` or `identity_prefix` and surface property fields whose names contain `"owner"`.

Tool arguments accept **snake_case** aliases (for example `materialized_view_id`, `seed_node_ids`) where noted in the MCP schema; request bodies sent to CodeLogic use **camelCase** JSON keys.

### Install

#### Pre Requisites

The MCP server relies upon Astral UV to run, please [install](https://docs.astral.sh/uv/getting-started/installation/)

### MacOS Workaround for uvx

There is a known issue with `uvx` on **MacOS** where the CodeLogic MCP server may fail to launch in certain IDEs (such as Cursor), resulting in errors like:
See [issue #11](https://github.com/CodeLogicIncEngineering/codelogic-mcp-server/issues/11)
```
Failed to connect client closed
```

This appears to be a problem with Astral `uvx` running on MacOS. The following can be used as a workaround:

1. Clone this project locally.
2. Configure your `mcp.json` to use `uv` instead of `uvx`. For example:

```json
{
  "mcpServers": {
    "codelogic-mcp-server": {
      "type": "stdio",
      "command": "<PATH_TO_UV>/uv",
      "args": [
        "--directory",
        "<PATH_TO_THIS_REPO>/codelogic-mcp-server-main",
        "run",
        "codelogic-mcp-server"
      ],
      "env": {
        "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
        "CODELOGIC_USERNAME": "<my username>",
        "CODELOGIC_PASSWORD": "<my password>",
        "CODELOGIC_WORKSPACE_NAME": "<my workspace>",
        "CODELOGIC_DEBUG_MODE": "true"
      }
    }
  }
}
```

3. Restart Cursor.
4. Ensure the Cursor Global Rule for CodeLogic is in place.
5. Open the MCP tab in Cursor and refresh the `codelogic-mcp-server`.
6. Ask Cursor to make a code change in an existing class. The MCP server should now run the impact analysis successfully.

## Configuration for Different IDEs

### Visual Studio Code Configuration

To configure this MCP server in VS Code:

1. First, ensure you have GitHub Copilot agent mode enabled in VS Code.

2. Create a `.vscode/mcp.json` file in your workspace with the following configuration:

```json
{
  "servers": {
    "codelogic-mcp-server": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "codelogic-mcp-server@latest"
      ],
      "env": {
        "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
        "CODELOGIC_USERNAME": "<my username>",
        "CODELOGIC_PASSWORD": "<my password>",
        "CODELOGIC_WORKSPACE_NAME": "<my workspace>",
        "CODELOGIC_DEBUG_MODE": "true"
      }
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

3. Alternatively, you can run the `MCP: Add Server` command from the Command Palette and provide the server information.

4. To manage your MCP servers, use the `MCP: List Servers` command from the Command Palette.

5. Once configured, the server's tools will be available to Copilot agent mode. You can toggle specific tools on/off as needed by clicking the Tools button in the Chat view when in agent mode.

6. To use the Codelogic tools in agent mode, you can specifically ask about code impacts or database relationships, and the agent will utilize the appropriate tools.

### Claude Desktop Configuration

Configure Claude Desktop by editing the configuration file:

- On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`
- On Windows: `%APPDATA%/Claude/claude_desktop_config.json`
- On Linux: `~/.config/Claude/claude_desktop_config.json`

Add the following to your configuration file:

```json
"mcpServers": {
  "codelogic-mcp-server": {
    "command": "uvx",
    "args": [
      "codelogic-mcp-server@latest"
    ],
    "env": {
      "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
      "CODELOGIC_USERNAME": "<my username>",
      "CODELOGIC_PASSWORD": "<my password>",
      "CODELOGIC_WORKSPACE_NAME": "<my workspace>"
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
  "codelogic-mcp-server": {
    "command": "uvx",
    "args": [
      "codelogic-mcp-server@latest"
    ],
    "env": {
      "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
      "CODELOGIC_USERNAME": "<my username>",
      "CODELOGIC_PASSWORD": "<my password>",
      "CODELOGIC_WORKSPACE_NAME": "<my workspace>"
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

After adding the configuration, restart Windsurf IDE or refresh the tools to apply the changes.

### Cursor Configuration

To configure the CodeLogic MCP server in Cursor:

1. Configure the MCP server by creating a `.cursor/mcp.json` file:

```json
{
  "mcpServers": {
    "codelogic-mcp-server": {
      "command": "uvx",
      "args": [
        "codelogic-mcp-server@latest"
      ],
      "env": {
        "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
        "CODELOGIC_USERNAME": "<my username>",
        "CODELOGIC_PASSWORD": "<my password>",
        "CODELOGIC_WORKSPACE_NAME": "<my workspace>",
        "CODELOGIC_DEBUG_MODE": "true"
      }
    }
  }
}
```

> **Note:** On some systems, you may need to use the full path to the uvx executable instead of just "uvx". For example: `/home/user/.local/bin/uvx` on Linux/Mac or `C:\Users\username\AppData\Local\astral\uvx.exe` on Windows.

2. Restart Cursor to apply the changes.

The CodeLogic MCP server tools will now be available in your Cursor workspace.

## AI Assistant Instructions/Rules

To help the AI assistant use the CodeLogic tools effectively, you can add the following instructions/rules to your client's configuration. We recommend customizing these instructions to align with your team's specific coding standards, best practices, and workflow requirements:

When the graph API is available on your CodeLogic host, extend your rules with the same guidance the server already advertises in its MCP `instructions`: use **`codelogic-graph-*`** tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; if graph calls fail with “not available”, fall back to **codelogic-method-impact** / **codelogic-database-impact**.

### VS Code (GitHub Copilot) Instructions

Create a `.vscode/copilot-instructions.md` file with the following content:

```markdown
# CodeLogic MCP Server Instructions

When modifying existing code methods:
- Use codelogic-method-impact to analyze code changes
- Use codelogic-database-impact for database modifications
- When the CodeLogic graph API is available, use codelogic-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use codelogic-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the CodeLogic tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer codelogic-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Claude Desktop Instructions

Create a file `~/.claude/instructions.md` with the following content:

```markdown
# CodeLogic MCP Server Instructions

When modifying existing code methods:
- Use codelogic-method-impact to analyze code changes
- Use codelogic-database-impact for database modifications
- When the CodeLogic graph API is available, use codelogic-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use codelogic-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the CodeLogic tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer codelogic-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Windsurf IDE Rules

Create or modify the `~/.codeium/windsurf/memories/global_rules.md` markdown file with the following content:

```markdown
When modifying existing code methods:
- Use codelogic-method-impact to analyze code changes
- Use codelogic-database-impact for database modifications
- When the CodeLogic graph API is available, use codelogic-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
- Highlight impact results for the modified methods

When modifying SQL code or database entities:
- Always use codelogic-database-impact to analyze potential impacts
- Highlight impact results for the modified database entities

To use the CodeLogic tools effectively:
- For code impacts: Ask about specific methods or functions
- For database relationships: Ask about tables, views, or columns
- For graph discovery: Prefer codelogic-graph-* tools when available
- Review the impact results before making changes
- Consider both direct and indirect impacts
```

### Cursor Global Rule

To configure CodeLogic rules in Cursor:

1. Open Cursor Settings
2. Navigate to the "Rules" section
3. Add the following content to "User Rules":

```markdown
# CodeLogic MCP Server Rules
## Codebase
- The CodeLogic MCP Server is for java, javascript, typescript, and C# dotnet codebases
- don't run the tools on python or other non supported codebases
## AI Assistant Behavior
- When modifying existing code methods:
  - Use codelogic-method-impact to analyze code changes
  - Use codelogic-database-impact for database modifications
  - When the CodeLogic graph API is available, use codelogic-graph-* tools (search, impact, path-explain, validate-change-scope, owners, capabilities) for bounded graph discovery; otherwise rely on method/database impact tools
  - Highlight impact results for the modified methods
- When modifying SQL code or database entities:
  - Always use codelogic-database-impact to analyze potential impacts
  - Highlight impact results for the modified database entities
- To use the CodeLogic tools effectively:
  - For code impacts: Ask about specific methods or functions
  - For database relationships: Ask about tables, views, or columns
  - Review the impact results before making changes
  - Consider both direct and indirect impacts
```

## Environment Variables

The following environment variables can be configured to customize the behavior of the server:

- `CODELOGIC_SERVER_HOST`: The URL of the CodeLogic server.
- `CODELOGIC_USERNAME`: Your CodeLogic username.
- `CODELOGIC_PASSWORD`: Your CodeLogic password.
- `CODELOGIC_WORKSPACE_NAME`: The name of the workspace to use.
- `CODELOGIC_DEBUG_MODE`: Set to `true` to enable debug mode. When enabled, additional debug files such as `timing_log.txt` and `impact_data*.json` will be generated. Defaults to `false`.

**Tests only**

- `CODELOGIC_GRAPH_E2E_REQUIRED`: Set to `1` when running graph MCP integration tests if you want missing graph APIs (HTTP 404 / “Graph API not available”) to **fail** the suite instead of **skipping** those tests.

### Example Configuration

```json
"env": {
  "CODELOGIC_SERVER_HOST": "<url to the server e.g. https://myco.app.codelogic.com>",
  "CODELOGIC_USERNAME": "<my username>",
  "CODELOGIC_PASSWORD": "<my password>",
  "CODELOGIC_WORKSPACE_NAME": "<my workspace>",
  "CODELOGIC_DEBUG_MODE": "true"
}
```

### Pinning the version

instead of using the **latest** version of the server, you can pin to a specific version by changing the **args** field to match the version in [pypi](https://pypi.org/project/codelogic-mcp-server/) e.g.

```json
    "args": [
      "codelogic-mcp-server@0.2.2"
    ],
```

### Version Compatibility

This MCP server has the following version compatibility requirements:

- Version 0.3.1 and below: Compatible with all CodeLogic API versions
- Version 0.4.0 and above: Requires CodeLogic API version 25.10.0 or greater

If you're upgrading, make sure your CodeLogic server meets the minimum API version requirement.

**Graph tools**: Require your CodeLogic deployment to serve the graph endpoints under `/codelogic/server/ai-retrieval/graph/`. Older or partial deployments may return 404; the MCP tools surface that as a clear error instead of opaque failures.

## Debug Logging

When `CODELOGIC_DEBUG_MODE=true`, debug files are written to the system temporary directory:

- **Windows**: `%TEMP%\codelogic-mcp-server` (typically `C:\Users\{username}\AppData\Local\Temp\codelogic-mcp-server`)
- **macOS**: `/tmp/codelogic-mcp-server` (or `$TMPDIR/codelogic-mcp-server` if set)  
- **Linux**: `/tmp/codelogic-mcp-server` (or `$TMPDIR/codelogic-mcp-server` if set)

**Debug files include**:
- `timing_log.txt` - Performance timing information
- `impact_data_*.json` - Raw impact analysis data for troubleshooting

**Finding your log directory**:
```python
import tempfile
import os
print("Log directory:", os.path.join(tempfile.gettempdir(), "codelogic-mcp-server"))
```

## Testing

### Running Unit Tests

The project uses unittest for testing. You can run unit tests without any external dependencies:

```bash
python -m unittest discover -s test -p "unit_*.py"
```

Unit tests use mock data and don't require a connection to a CodeLogic server.

### Integration Tests (Optional)

If you want to run integration tests that connect to a real CodeLogic server:

1. Copy `test/.env.test.example` to `test/.env.test` and populate with your CodeLogic server details
2. Run the integration tests:

```bash
python -m unittest discover -s test -p "integration_*.py"
```

Note: Integration tests require access to a CodeLogic server instance.

### Graph MCP end-to-end tests

`test/integration_test_graph.py` drives the real MCP handler path (`handle_call_tool`) for **`codelogic-graph-capabilities`** and a chained flow (search → impact → path → validate → owners) against `CODELOGIC_SERVER_HOST`. Configure credentials the same way as other integration tests (`test/.env.test` from `test/.env.test.example`).

- If the host does not expose graph routes, tests **skip** by default.
- Set **`CODELOGIC_GRAPH_E2E_REQUIRED=1`** to turn missing graph APIs into hard failures (useful in CI when graph must be present).

From the repo root:

```bash
./scripts/run_graph_e2e.sh
```

Equivalent:

```bash
uv run python -m unittest test.integration_test_graph -v
```

## Validation for Official MCP Registry

mcp-name: io.github.CodeLogicIncEngineering/codelogic-mcp-server
