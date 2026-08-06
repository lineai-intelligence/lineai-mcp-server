# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
MCP handlers for ``lineai-graph-*`` tools (graph HTTP API).

Request bodies use **camelCase** keys aligned with Lineai JSON conventions.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import mcp.types as types

from ..graph_client import graph_error_message, graph_request
from ..utils import get_mv_id
from .common import get_workspace_name


def _require_arguments(arguments: dict | None) -> dict:
    if not arguments:
        raise ValueError("Missing arguments")
    return arguments


def _inject_materialized_view_id(body: dict[str, Any], arguments: dict) -> dict[str, Any]:
    """Add ``materializedViewId`` from args or workspace env (via MV name)."""
    out = dict(body)
    explicit = arguments.get("materialized_view_id") or arguments.get("materializedViewId")
    if explicit:
        out["materializedViewId"] = explicit
        return out
    workspace = get_workspace_name()
    out["materializedViewId"] = get_mv_id(workspace)
    return out


def _strip_nones(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _markdown_json(title: str, payload: Any) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    return f"# {title}\n\n```json\n{text}\n```\n"


def _resolve_materialized_view_id_str(arguments: dict) -> str:
    explicit = arguments.get("materialized_view_id") or arguments.get("materializedViewId")
    if explicit:
        return str(explicit)
    return str(get_mv_id(get_workspace_name()))


def _run_graph_tool(
    tool_name: str,
    path_suffix: str,
    *,
    method: str = "POST",
    json_body: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> list[types.TextContent]:
    payload, status, kind, snippet = graph_request(
        method, path_suffix, json_body=json_body, query_params=query_params
    )
    if kind is not None:
        msg = graph_error_message(tool_name, path_suffix, kind, status, snippet)
        return [types.TextContent(type="text", text=msg)]
    return [types.TextContent(type="text", text=_markdown_json(tool_name, payload))]


def handle_lineai_graph_search(arguments: dict | None) -> list[types.TextContent]:
    args = _require_arguments(arguments)
    query = (args.get("query") or args.get("q") or "").strip()
    identity_prefix = (args.get("identity_prefix") or "").strip()
    if not query and not identity_prefix:
        raise ValueError("Provide at least one of: `query` (or `q`), `identity_prefix`")

    body = _strip_nones(
        {
            "query": query or None,
            "identityPrefix": identity_prefix or None,
            "scanSpace": args.get("scan_space"),
            "preferLatestScan": args.get("prefer_latest_scan"),
            "limit": args.get("limit"),
        }
    )
    body = _inject_materialized_view_id(body, args)
    sys.stderr.write(f"lineai-graph-search scope materializedViewId={body.get('materializedViewId')}\n")
    return _run_graph_tool("lineai-graph-search", "/search", json_body=body)


def handle_lineai_graph_impact(arguments: dict | None) -> list[types.TextContent]:
    args = _require_arguments(arguments)
    seeds = args.get("seed_node_ids") or args.get("seedNodeIds")
    if not seeds or not isinstance(seeds, list):
        raise ValueError("`seed_node_ids` must be a non-empty list of graph node ids")
    body = _strip_nones(
        {
            "seedNodeIds": seeds,
            "direction": args.get("direction"),
            "depth": args.get("depth"),
            "scanSpace": args.get("scan_space"),
        }
    )
    body = _inject_materialized_view_id(body, args)
    return _run_graph_tool("lineai-graph-impact", "/impact", json_body=body)


def handle_lineai_graph_path_explain(arguments: dict | None) -> list[types.TextContent]:
    args = _require_arguments(arguments)
    from_id = args.get("from_node_id") or args.get("fromNodeId")
    to_id = args.get("to_node_id") or args.get("toNodeId")
    if not from_id or not to_id:
        raise ValueError("`from_node_id` and `to_node_id` are required")
    body = _strip_nones(
        {
            "fromNodeId": from_id,
            "toNodeId": to_id,
            "maxDepth": args.get("max_depth"),
            "scanSpace": args.get("scan_space"),
        }
    )
    body = _inject_materialized_view_id(body, args)
    return _run_graph_tool("lineai-graph-path-explain", "/path", json_body=body)


def handle_lineai_graph_validate_change_scope(arguments: dict | None) -> list[types.TextContent]:
    args = _require_arguments(arguments)
    seeds = args.get("seed_node_ids") or args.get("seedNodeIds")
    summary = (args.get("proposed_change_summary") or "").strip()
    if not seeds or not isinstance(seeds, list):
        raise ValueError("`seed_node_ids` must be a non-empty list")
    if not summary:
        raise ValueError("`proposed_change_summary` is required")
    body = _strip_nones(
        {
            "seedNodeIds": seeds,
            "proposedChangeSummary": summary,
            "scanSpace": args.get("scan_space"),
        }
    )
    body = _inject_materialized_view_id(body, args)
    return _run_graph_tool(
        "lineai-graph-validate-change-scope",
        "/validate-change-scope",
        json_body=body,
    )


def handle_lineai_graph_owners(arguments: dict | None) -> list[types.TextContent]:
    args = _require_arguments(arguments)
    node_id = args.get("node_id") or args.get("nodeId")
    identity_prefix = args.get("identity_prefix")
    if not node_id and not identity_prefix:
        raise ValueError("Provide `node_id` or `identity_prefix`")
    body = _strip_nones(
        {
            "nodeId": node_id,
            "identityPrefix": identity_prefix,
            "scanSpace": args.get("scan_space"),
        }
    )
    body = _inject_materialized_view_id(body, args)
    return _run_graph_tool("lineai-graph-owners", "/owners", json_body=body)


def handle_lineai_graph_capabilities(arguments: dict | None) -> list[types.TextContent]:
    """Discovery tool (GET); requires ``materializedViewId`` (query) — default from workspace MV."""
    if arguments is None:
        arguments = {}
    mv = _resolve_materialized_view_id_str(arguments)
    return _run_graph_tool(
        "lineai-graph-capabilities",
        "/capabilities",
        method="GET",
        json_body=None,
        query_params={"materializedViewId": mv},
    )


GRAPH_TOOL_DISPATCH = {
    "lineai-graph-search": handle_lineai_graph_search,
    "lineai-graph-impact": handle_lineai_graph_impact,
    "lineai-graph-path-explain": handle_lineai_graph_path_explain,
    "lineai-graph-validate-change-scope": handle_lineai_graph_validate_change_scope,
    "lineai-graph-owners": handle_lineai_graph_owners,
    "lineai-graph-capabilities": handle_lineai_graph_capabilities,
}


def handle_graph_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    fn = GRAPH_TOOL_DISPATCH.get(name)
    if not fn:
        raise ValueError(f"Unknown graph tool: {name}")
    return fn(arguments)
