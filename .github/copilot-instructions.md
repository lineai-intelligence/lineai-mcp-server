# Lineai MCP Server - AI Assistant Instructions

## About This Codebase

This repository contains a Model Context Protocol (MCP) server that integrates with Lineai's knowledge graph APIs. It enables AI programming assistants (like GitHub Copilot and Cursor) to access dependency data from Lineai to analyze code and database impacts.

## Key Technologies

- **Python 3.13+** with extensive use of async/await
- **Model Context Protocol SDK** (`mcp[cli]`)
- **Neo4j** for graph database operations
- **HTTPX** for API requests
- **Environment variables** via dotenv for configuration

## Project Structure

- **src/lineai_mcp_server/**: Core package
- **`__init__.py`**: Package initialization and entry point  
- **`server.py`**: MCP server implementation  
- **`handlers.py`**: Tool handlers implementation  
- **`utils.py`**: API interaction utilities  

## Core Coding Patterns

### MCP Server Pattern

```python
server = Server("lineai-mcp-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    # Define and return tools
    
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    # Handle tool execution
```

### Error Handling

```python
try:
    # Operations that might fail
except Exception as e:
    sys.stderr.write(f"Error: {str(e)}\n")
    return [types.TextContent(type="text", text=f"# Error\n\n{str(e)}")]
```

## Style Guidelines

1. **Copyright Headers**: Include MPL 2.0 headers in all Python files
2. **Docstrings**: Google-style docstrings for modules/classes/functions
3. **Type Hints**: Always use Python type hints
4. **Asynchronous**: Keep I/O operations asynchronous
5. **Format Outputs**: Return markdown-formatted text in tool responses

## Tool Implementation Pattern

When implementing new MCP tools:

1. Add to `handle_list_tools()` with descriptive name (prefix: `lineai-`)
2. Add handler in `handle_call_tool()`
3. Implement handler function with error handling
4. Return results as markdown-formatted text

## Testing Approach

- **Unit Tests**: For functions without external dependencies
- **Integration Tests**: For tests against a real Lineai server
- Use the `LINEAI_TEST_MODE` environment variable

## Debugging

- Debug Mode: Set `LINEAI_DEBUG_MODE=true`
- Remote Debugging: Use debugpy capabilities

## Key Environment Variables

- `LINEAI_SERVER_HOST`: Lineai server URL
- `LINEAI_USERNAME`: Username for authentication
- `LINEAI_PASSWORD`: Password for authentication
- `LINEAI_WORKSPACE_NAME`: Workspace name
- `LINEAI_DEBUG_MODE`: Enable debug logging
- `LINEAI_TEST_MODE`: Used by test framework

## AI Assistant Integration

### GitHub Copilot
- Uses these instructions via `.github/copilot-instructions.md`
- Integrates through GitHub's Copilot infrastructure

### Cursor
- Uses these instructions via `.cursor/instructions.md`
- Integrates through Cursor's AI infrastructure
- Supports additional features like:
  - Direct file editing
  - Terminal command execution
  - Code search and analysis
  - Database impact analysis
