# Copyright (C) 2025 CodeLogic Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Main handlers module for CodeLogic MCP server.

This module provides the main handler registry and routing for all CodeLogic tools.
"""

import sys
import mcp.types as types
from ..server import server
from .method_impact import handle_method_impact
from .database_impact import handle_database_impact
from .graph_tools import GRAPH_TOOL_DISPATCH, handle_graph_tool


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    """
    return [
        types.Tool(
            name="codelogic-method-impact",
            description="Analyze impacts of modifying a specific method within a given class or type.\n"
                        "Uses CODELOGIC_WORKSPACE_NAME environment variable to determine the target workspace.\n"
                        "Recommended workflow:\n"
                        "1. Use this tool before implementing code changes\n"
                        "2. Run the tool against methods or functions that are being modified\n"
                        "3. Carefully review the impact analysis results to understand potential downstream effects\n"
                        "Particularly crucial when AI-suggested modifications are being considered.",
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "Name of the method being analyzed"},
                    "class": {"type": "string", "description": "Name of the class containing the method"},
                },
                "required": ["method", "class"],
            },
        ),
        types.Tool(
            name="codelogic-database-impact",
            description="Analyze impacts between code and database entities.\n"
                        "Uses CODELOGIC_WORKSPACE_NAME environment variable to determine the target workspace.\n"
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
                },
                "required": ["entity_type", "name"],
            },
        ),
        types.Tool(
            name="codelogic-graph-capabilities",
            description="Fetch graph API capabilities/manifest from the CodeLogic server (GET). "
                        "Returns label and relationship metadata when the graph tier is deployed; "
                        "otherwise explains missing routes. Uses CODELOGIC_WORKSPACE_NAME for MV id unless "
                        "`materialized_view_id` is set.",
            inputSchema={
                "type": "object",
                "properties": {
                    "materialized_view_id": {
                        "type": "string",
                        "description": "Optional materialized view id; default from workspace name",
                    },
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="codelogic-graph-search",
            description="Search the CodeLogic knowledge graph (curated HTTP API). "
                        "Provide `query` or `identity_prefix`; optional `scan_space`, `materialized_view_id`, `limit`. "
                        "Requires server route POST .../ai-retrieval/graph/search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Symbol or text query (alias: q)"},
                    "q": {"type": "string", "description": "Alias for query"},
                    "identity_prefix": {"type": "string", "description": "Prefix of stable graph identity"},
                    "scan_space": {"type": "string", "description": "Optional scan-space / branch filter"},
                    "materialized_view_id": {"type": "string", "description": "Override MV id; default from workspace name"},
                    "prefer_latest_scan": {"type": "boolean"},
                    "limit": {"type": "integer", "description": "Suggested max hits (server may cap)"},
                },
            },
        ),
        types.Tool(
            name="codelogic-graph-impact",
            description="Bounded graph impact from seed node ids (curated HTTP API). "
                        "Optional `direction` (upstream|downstream|both), `depth`, `scan_space`, `materialized_view_id`.",
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
                    "materialized_view_id": {"type": "string"},
                },
                "required": ["seed_node_ids"],
            },
        ),
        types.Tool(
            name="codelogic-graph-path-explain",
            description="Explain bounded paths between two graph nodes (curated HTTP API). "
                        "Requires `from_node_id`, `to_node_id`; optional `max_depth`, `scan_space`, `materialized_view_id`.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_node_id": {"type": "string"},
                    "to_node_id": {"type": "string"},
                    "max_depth": {"type": "integer"},
                    "scan_space": {"type": "string"},
                    "materialized_view_id": {"type": "string"},
                },
                "required": ["from_node_id", "to_node_id"],
            },
        ),
        types.Tool(
            name="codelogic-graph-validate-change-scope",
            description="Validate whether a proposed change scope is safe given seed graph nodes (curated HTTP API).",
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_node_ids": {"type": "array", "items": {"type": "string"}},
                    "proposed_change_summary": {"type": "string"},
                    "scan_space": {"type": "string"},
                    "materialized_view_id": {"type": "string"},
                },
                "required": ["seed_node_ids", "proposed_change_summary"],
            },
        ),
        types.Tool(
            name="codelogic-graph-owners",
            description="Look up owners/reviewers for a graph node (curated HTTP API). "
                        "Provide `node_id` or `identity_prefix`.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "identity_prefix": {"type": "string"},
                    "scan_space": {"type": "string"},
                    "materialized_view_id": {"type": "string"},
                },
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Handle tool execution requests.
    Tools can modify server state and notify clients of changes.
    """
    try:
        if name == "codelogic-method-impact":
            return await handle_method_impact(arguments)
        elif name == "codelogic-database-impact":
            return await handle_database_impact(arguments)
        elif name in GRAPH_TOOL_DISPATCH:
            return handle_graph_tool(name, arguments)
        else:
            sys.stderr.write(f"Unknown tool: {name}\n")
            raise ValueError(f"Unknown tool: {name}")
    except Exception as e:
        sys.stderr.write(f"Error handling tool call {name}: {str(e)}\n")
        error_message = f"""# Error executing tool: {name}

An error occurred while executing this tool:
```
{str(e)}
```
Please check the server logs for more details.
"""
        return [
            types.TextContent(
                type="text",
                text=error_message
            )
        ]
