# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Main handlers module for Lineai MCP server.

This module provides the main handler registry and routing for all Lineai tools.
"""

import asyncio
import sys
import mcp.types as types
from ..server import server
from ..utils import LineaiApiError
from .common import error_result, render_lineai_api_error
from .method_impact import handle_method_impact
from .database_impact import handle_database_impact
from .graph_tools import GRAPH_TOOL_DISPATCH, handle_graph_tool


# Shared description of how the target materialized view is resolved.
_SCOPE_NOTE = (
    "Scope resolution precedence: explicit `materialized_view_id` -> `workspace` (name) -> "
    "`LINEAI_WORKSPACE_NAME` environment variable -> the server's default workspace's latest "
    "materialized view (nothing specified is never an error while a default view exists)."
)

_WORKSPACE_PROPERTY = {
    "type": "string",
    "description": "Optional workspace (materialized view definition) name; overrides LINEAI_WORKSPACE_NAME",
}

_MV_ID_PROPERTY = {
    "type": "string",
    "description": "Optional materialized view id; takes precedence over `workspace`",
}


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    """
    return [
        types.Tool(
            name="lineai-method-impact",
            description="Analyze impacts of modifying a specific method within a given class or type.\n"
                        f"{_SCOPE_NOTE}\n"
                        "When the impact graph contains unresolved references (calls to symbols that could "
                        "not be resolved inside the analyzed view), they are reported in a dedicated "
                        "'Unresolved References' section as dependency indicators.\n"
                        "Recommended workflow:\n"
                        "1. Use this tool before implementing code changes\n"
                        "2. Run the tool against methods or functions that are being modified\n"
                        "3. Carefully review the impact analysis results to understand potential downstream effects\n"
                        "Particularly crucial when AI-suggested modifications are being considered.",
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "Name of the method being analyzed"},
                    "class": {"type": "string", "description": "Optional name of the class containing the method (case-insensitive substring filter)"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
                "required": ["method"],
            },
        ),
        types.Tool(
            name="lineai-database-impact",
            description="Analyze impacts between code and database entities.\n"
                        f"{_SCOPE_NOTE}\n"
                        "Unresolved references found in the impact graphs are reported in a dedicated "
                        "'Unresolved References' section as dependency indicators.\n"
                        "Recommended workflow:\n"
                        "1. Use this tool before implementing code or database changes\n"
                        "2. Search for the relevant database entity\n"
                        "3. Review the impact analysis to understand which code depends on this database object and vice versa\n"
                        "Particularly crucial when AI-suggested modifications are being considered or when modifying SQL code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Type of database entity to search for (column, table, or view)",
                        "enum": ["column", "table", "view"]
                    },
                    "name": {"type": "string", "description": "Name of the database entity to search for"},
                    "table_or_view": {"type": "string", "description": "Name of the table or view containing the column (required for columns only)"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
                "required": ["entity_type", "name"],
            },
        ),
        types.Tool(
            name="lineai-graph-capabilities",
            description="Fetch graph API capabilities/manifest from the Lineai server (GET). "
                        "Returns label and relationship metadata when the graph tier is deployed; "
                        "otherwise explains missing routes. "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "materialized_view_id": _MV_ID_PROPERTY,
                    "materializedViewId": {
                        "type": "string",
                        "description": "Alias for materialized_view_id",
                    },
                    "workspace": _WORKSPACE_PROPERTY,
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="lineai-graph-search",
            description="Search the Lineai knowledge graph (curated HTTP API). "
                        "Provide `query` or `identity_prefix`; optional `scan_space`, `workspace`, `materialized_view_id`, `limit`. "
                        "Results may include unresolved references (primaryLabel `UnresolvedReference`): "
                        "symbols that are referenced but could not be resolved in the view — treat them as dependency indicators. "
                        "Requires server route POST .../ai-retrieval/graph/search. "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Symbol or text query (alias: q)"},
                    "q": {"type": "string", "description": "Alias for query"},
                    "identity_prefix": {"type": "string", "description": "Prefix of stable graph identity"},
                    "scan_space": {"type": "string", "description": "Optional scan-space / branch filter"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                    "prefer_latest_scan": {"type": "boolean"},
                    "limit": {"type": "integer", "description": "Suggested max hits (server may cap)"},
                },
            },
        ),
        types.Tool(
            name="lineai-graph-impact",
            description="Bounded graph impact from seed node ids (curated HTTP API). "
                        "Optional `direction` (upstream|downstream|both), `depth`, `scan_space`, `workspace`, `materialized_view_id`. "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Graph node ids to expand from",
                    },
                    "direction": {"type": "string", "enum": ["upstream", "downstream", "both"]},
                    "depth": {"type": "integer"},
                    "scan_space": {"type": "string"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
                "required": ["seed_node_ids"],
            },
        ),
        types.Tool(
            name="lineai-graph-path-explain",
            description="Explain bounded paths between two graph nodes (curated HTTP API). "
                        "Requires `from_node_id`, `to_node_id`; optional `max_depth`, `scan_space`, `workspace`, `materialized_view_id`. "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_node_id": {"type": "string"},
                    "to_node_id": {"type": "string"},
                    "max_depth": {"type": "integer"},
                    "scan_space": {"type": "string"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
                "required": ["from_node_id", "to_node_id"],
            },
        ),
        types.Tool(
            name="lineai-graph-validate-change-scope",
            description="Validate whether a proposed change scope is safe given seed graph nodes (curated HTTP API). "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_node_ids": {"type": "array", "items": {"type": "string"}},
                    "proposed_change_summary": {"type": "string"},
                    "scan_space": {"type": "string"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
                "required": ["seed_node_ids", "proposed_change_summary"],
            },
        ),
        types.Tool(
            name="lineai-graph-owners",
            description="Look up owners/reviewers for a graph node (curated HTTP API). "
                        "Provide `node_id` or `identity_prefix`. "
                        f"{_SCOPE_NOTE}",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "identity_prefix": {"type": "string"},
                    "scan_space": {"type": "string"},
                    "workspace": _WORKSPACE_PROPERTY,
                    "materialized_view_id": _MV_ID_PROPERTY,
                },
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource] | types.CallToolResult:
    """
    Handle tool execution requests.

    The sync handlers are dispatched via ``asyncio.to_thread`` so their blocking
    HTTP calls do not stall the MCP event loop. ``LineaiApiError`` is rendered
    as an honest per-kind error page; anything else gets a generic error page.
    Both are returned as ``CallToolResult`` with ``isError=True``.
    """
    try:
        if name == "lineai-method-impact":
            return await asyncio.to_thread(handle_method_impact, arguments)
        elif name == "lineai-database-impact":
            return await asyncio.to_thread(handle_database_impact, arguments)
        elif name in GRAPH_TOOL_DISPATCH:
            return await asyncio.to_thread(handle_graph_tool, name, arguments)
        else:
            sys.stderr.write(f"Unknown tool: {name}\n")
            raise ValueError(f"Unknown tool: {name}")
    except LineaiApiError as e:
        sys.stderr.write(f"Lineai API error handling tool call {name}: {e}\n")
        return error_result(render_lineai_api_error(name, e))
    except Exception as e:
        sys.stderr.write(f"Error handling tool call {name}: {str(e)}\n")
        error_message = f"""# Error executing tool: {name}

An error occurred while executing this tool:
```
{str(e)}
```
Please check the server logs for more details.
"""
        return error_result(error_message)
