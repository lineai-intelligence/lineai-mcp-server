# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
HTTP client for Lineai graph endpoints under ``/ai-retrieval/graph``.

These routes are consumed by ``lineai-graph-*`` MCP tools. The Lineai
server may return 404 until graph APIs are deployed; callers format a clear hint.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Literal, Optional

import httpx

from .utils import authenticate, _client

GraphErrorKind = Optional[
    Literal["not_deployed", "timeout", "gateway_timeout", "http_error", "invalid_json"]
]

# Same path layout as existing ai-retrieval usage in utils.py
_GRAPH_REL_PREFIX = "/api/ai-retrieval/graph"


def _graph_response_tuple(response: httpx.Response) -> tuple[Any | None, int, GraphErrorKind, str]:
    text = response.text or ""
    snippet = text[:2000] if text else ""
    code = response.status_code
    if code == 404:
        return None, 404, "not_deployed", snippet
    if code == 504:
        return None, 504, "gateway_timeout", snippet
    if code >= 400:
        return None, code, "http_error", snippet
    try:
        return response.json(), code, None, snippet
    except json.JSONDecodeError:
        return None, code, "invalid_json", snippet


def graph_api_base_url() -> str:
    """
    Base URL for graph HTTP calls: ``LINEAI_SERVER_HOST`` (same host as all
    other Lineai MCP API usage). No trailing slash.
    """
    raw = (os.getenv("LINEAI_SERVER_HOST") or "").strip()
    return raw.rstrip("/")


def graph_request(
    method: str,
    path_suffix: str,
    *,
    json_body: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> tuple[Any | None, int, GraphErrorKind, str]:
    """
    Perform an authenticated request to a graph API path.

    Args:
        method: ``GET`` or ``POST``.
        path_suffix: Path under ``.../ai-retrieval/graph``, e.g. ``/search`` or
            ``/capabilities``. Must start with ``/``.
        query_params: Optional query string parameters (e.g. ``materializedViewId`` for GET).

    Returns:
        Tuple of ``(parsed_body, status_code, error_kind, raw_text_snippet)``.
        On success, ``parsed_body`` is usually a dict/list from JSON and
        ``error_kind`` is ``None``. ``raw_text_snippet`` is truncated response
        text for diagnostics on parse errors.
    """
    base = graph_api_base_url()
    if not base:
        return None, 0, "http_error", "LINEAI_SERVER_HOST is not set"

    if not path_suffix.startswith("/"):
        path_suffix = "/" + path_suffix

    url = f"{base}{_GRAPH_REL_PREFIX}{path_suffix}"
    token = authenticate()
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    params: dict[str, str] | None = None
    if query_params:
        params = {k: str(v) for k, v in query_params.items() if v is not None}

    sys.stderr.write(f"Graph API {method} {url}\n")
    try:
        m = method.upper()
        if m == "GET":
            response = _client.get(url, headers=headers, params=params)
        elif m == "POST":
            response = _client.post(
                url,
                headers=headers,
                params=params,
                json=json_body if json_body is not None else {},
            )
        else:
            return None, 0, "http_error", f"Unsupported HTTP method: {method}"
        return _graph_response_tuple(response)
    except httpx.TimeoutException as e:
        sys.stderr.write(f"Graph API timeout: {e}\n")
        return None, 0, "timeout", str(e)
    except httpx.HTTPError as e:
        sys.stderr.write(f"Graph API HTTP error: {e}\n")
        return None, 0, "http_error", str(e)


def graph_not_deployed_message(tool_name: str, path_suffix: str, status_code: int, snippet: str) -> str:
    host = graph_api_base_url() or "(not configured)"
    tail = f"\n\nResponse excerpt:\n```\n{snippet}\n```\n" if snippet.strip() else ""
    return f"""# Graph API not available: `{tool_name}`

The MCP server called **HTTP {status_code}** on:

`{host}{_GRAPH_REL_PREFIX}{path_suffix}`

The Lineai **graph** endpoints under `/api/ai-retrieval/graph/` are not deployed on this host yet, or the path does not match the server build.

## What to do

1. Confirm your Lineai version exposes the graph agent API (see internal `context/graph-mcp-tooling-ideas.md`).
2. Verify `LINEAI_SERVER_HOST` and credentials.
3. Until the server implements these routes, use **`lineai-method-impact`** and **`lineai-database-impact`** for impact analysis.
{tail}"""


def graph_error_message(
    tool_name: str,
    path_suffix: str,
    kind: GraphErrorKind,
    status_code: int,
    snippet: str,
) -> str:
    if kind == "not_deployed":
        return graph_not_deployed_message(tool_name, path_suffix, status_code or 404, snippet)
    if kind == "timeout":
        return f"""# Graph request timed out: `{tool_name}`

The request to `{path_suffix}` exceeded the HTTP client timeout (`LINEAI_REQUEST_TIMEOUT`).

Try again later or increase the timeout if appropriate.
"""
    if kind == "gateway_timeout":
        return f"""# Graph gateway timeout: `{tool_name}`

The Lineai server returned **504** for `{path_suffix}`.

Retry when the server is less busy.
"""
    if kind == "invalid_json":
        return f"""# Invalid JSON from graph API: `{tool_name}`

The server returned a non-JSON body for `{path_suffix}`.

Excerpt:

```
{snippet[:1500]}
```
"""
    return f"""# Graph API error: `{tool_name}`

Request to `{path_suffix}` failed (`{kind or 'http_error'}`, HTTP **{status_code}**).

```
{snippet[:1500]}
```
"""
