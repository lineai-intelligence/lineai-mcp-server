# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Common utilities and shared functions for Lineai MCP handlers.
"""

import json
import os
import tempfile
from datetime import datetime

import mcp.types as types


DEBUG_MODE = os.getenv("LINEAI_DEBUG_MODE", "false").lower() == "true"

# Use a user-specific temporary directory for logs to avoid permission issues when running via uvx
# Only create the directory when debug mode is enabled
LOGS_DIR = os.path.join(tempfile.gettempdir(), "lineai-mcp-server")
if DEBUG_MODE:
    os.makedirs(LOGS_DIR, exist_ok=True)


def ensure_logs_dir():
    """Ensure the logs directory exists when needed for debug mode."""
    if DEBUG_MODE:
        os.makedirs(LOGS_DIR, exist_ok=True)


def get_workspace_name():
    """Deprecated: workspace resolution now goes through ``utils.resolve_mv_id``."""
    import sys
    workspace_name = os.getenv("LINEAI_WORKSPACE_NAME")
    if not workspace_name:
        sys.stderr.write("Warning: LINEAI_WORKSPACE_NAME environment variable not set. Using default workspace.\n")
        workspace_name = "default-workspace"
    return workspace_name


def error_result(markdown: str) -> types.CallToolResult:
    """Build a CallToolResult that is marked as an error (isError=True)."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=markdown)],
        isError=True,
    )


def render_lineai_api_error(tool_name: str, error) -> str:
    """
    Render a LineaiApiError as an honest, per-kind markdown error page.

    Args:
        tool_name (str): Name of the tool that failed.
        error (LineaiApiError): The typed error to render.

    Returns:
        str: Markdown describing what failed and what to do about it.
    """
    host = os.getenv("LINEAI_SERVER_HOST") or "(LINEAI_SERVER_HOST not set)"
    status_text = f"HTTP {error.status}" if getattr(error, "status", None) else "no HTTP status received"
    endpoint = getattr(error, "endpoint", "") or "(unknown endpoint)"
    detail = getattr(error, "detail", "") or ""
    detail_block = f"\n```\n{detail}\n```\n" if detail else ""
    kind = getattr(error, "kind", "http_error")

    if kind == "auth":
        body = (
            f"Authentication with the Lineai server failed ({status_text}).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Verify `LINEAI_USERNAME` / `LINEAI_PASSWORD` are correct for this server.\n"
            f"2. Confirm `LINEAI_SERVER_HOST` points at the right instance: {host}\n"
            "3. If credentials were rotated, restart the MCP server to clear cached state.\n"
        )
        title = "Authentication failed"
    elif kind == "mv_not_found":
        body = (
            f"The requested workspace / materialized view could not be resolved ({status_text}).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Check the `workspace` argument (or `LINEAI_WORKSPACE_NAME`) for typos.\n"
            "2. List the workspaces on the server to confirm the name exists and has a finished view.\n"
            "3. Omit the workspace entirely to use the server's default workspace.\n"
        )
        title = "Workspace / materialized view not found"
    elif kind == "mv_no_default":
        body = (
            f"No workspace or materialized view was specified, and the server has no default "
            f"materialized view available ({status_text}).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Specify a `workspace` (name) or `materialized_view_id` argument explicitly.\n"
            "2. Or create/build a default (primary) workspace view on the Lineai server.\n"
        )
        title = "No default materialized view"
    elif kind == "timeout":
        body = (
            f"The request to `{endpoint}` exceeded the HTTP client timeout "
            f"(`LINEAI_REQUEST_TIMEOUT`, currently {os.getenv('LINEAI_REQUEST_TIMEOUT', '120.0')}s).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Retry when the server is less busy, or raise `LINEAI_REQUEST_TIMEOUT` if appropriate.\n"
            f"2. Verify network access to: {host}\n"
        )
        title = "Request timed out"
    elif kind == "gateway_timeout":
        body = (
            f"The Lineai server returned **504 Gateway Timeout** for `{endpoint}` "
            "(an upstream component did not respond in time).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Try again in a few minutes; transient load or cold queries often cause this.\n"
            f"2. If it persists, check the health of the Lineai deployment at {host}.\n"
        )
        title = "Gateway timeout"
    elif kind == "invalid_response":
        body = (
            f"The Lineai server returned an unexpected payload from `{endpoint}` ({status_text}).\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Confirm the MCP server version matches the deployed Lineai server API.\n"
            "2. Check the server logs for errors around this request.\n"
        )
        title = "Unexpected server response"
    else:  # http_error and anything unexpected
        body = (
            f"The request to `{endpoint}` failed ({status_text}). This is an infrastructure or "
            "server error, not a statement about your code.\n"
            f"{detail_block}\n"
            "## Recommendations\n"
            "1. Check MCP stderr logs for the exact HTTP status and message.\n"
            f"2. Verify the Lineai server health at {host} and retry.\n"
        )
        title = "Lineai API error"

    return f"# {title}: `{tool_name}`\n\n{body}"


def write_json_to_file(file_path, data):
    """Write JSON data to a file with improved formatting."""
    ensure_logs_dir()
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, separators=(", ", ": "), ensure_ascii=False, sort_keys=True)


def log_timing(operation, duration, details=""):
    """Log timing information for operations."""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ensure_logs_dir()
        with open(os.path.join(LOGS_DIR, "timing_log.txt"), "a") as log_file:
            log_file.write(f"{timestamp} - {operation} took {duration:.4f} seconds {details}\n")
