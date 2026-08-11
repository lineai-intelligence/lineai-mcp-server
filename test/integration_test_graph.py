# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
End-to-end integration tests for ``lineai-graph-*`` MCP tools against a real Lineai host.

Prerequisites (same as ``integration_test_all.py``):

- ``LINEAI_SERVER_HOST``, ``LINEAI_USERNAME``, ``LINEAI_PASSWORD``, ``LINEAI_WORKSPACE_NAME``
- Optional: ``.env`` / ``test/.env.test`` loaded by ``load_test_config()``

The server must expose ``POST/GET .../api/ai-retrieval/graph/*``. If graph routes
return 404, tests **skip** unless ``LINEAI_GRAPH_E2E_REQUIRED=1`` is set (then they **fail**).

Run::

    uv run python -m unittest test.integration_test_graph -v

Or from repo root with env loaded::

    set -a && source .env && set +a && uv run python -m unittest test.integration_test_graph -v
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unittest

import httpx
import mcp.types as types

from test.integration_test_all import load_test_config
from test.test_fixtures import setup_test_environment
from test.test_env import TestCase


def _graph_api_not_available(text: str) -> bool:
    return "Graph API not available" in text or "HTTP 404" in text


def _extract_json_from_mcp_markdown(text: str) -> dict:
    """Parse the ```json ... ``` block produced by graph MCP handlers."""
    m = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if not m:
        raise ValueError("No ```json``` block in MCP tool response")
    return json.loads(m.group(1))


def _is_server_reachable(config: dict) -> bool:
    if not config.get("LINEAI_SERVER_HOST") or not config.get("LINEAI_USERNAME") or not config.get("LINEAI_PASSWORD"):
        return False
    try:
        *_, authenticate = setup_test_environment(config)
        authenticate()
        return True
    except (httpx.ConnectError, OSError):
        return False


class TestGraphMcpE2E(TestCase):
    """E2E tests: MCP ``handle_call_tool`` → Lineai graph HTTP API."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_test_config()
        cls._skip_reason = None
        if cls.config.get("LINEAI_USERNAME") and cls.config.get("LINEAI_PASSWORD") and not _is_server_reachable(cls.config):
            cls._skip_reason = "Lineai server not reachable"

    def setUp(self):
        super().setUp()
        if not self.config.get("LINEAI_USERNAME") or not self.config.get("LINEAI_PASSWORD"):
            self.skipTest("Skipping graph E2E: no credentials in environment")
        if getattr(self.__class__, "_skip_reason", None):
            self.skipTest(self.__class__._skip_reason)
        for key, value in self.config.items():
            if value is not None:
                os.environ[key] = str(value)
        env_pass = {k: v for k, v in self.config.items() if v is not None}
        (
            self.handle_call_tool,
            self._get_mv_definition_id,
            self._get_mv_id_from_def,
            _get_method_nodes,
            _get_impact,
            _authenticate,
        ) = setup_test_environment(env_pass)

    def _require_graph_or_skip(self, result_text: str) -> dict:
        if _graph_api_not_available(result_text):
            msg = "Graph API not deployed on this Lineai host (404)"
            if os.environ.get("LINEAI_GRAPH_E2E_REQUIRED", "").strip() == "1":
                self.fail(msg)
            self.skipTest(msg)
        return _extract_json_from_mcp_markdown(result_text)

    async def _call_tool(self, name: str, arguments: dict | None):
        return await self.handle_call_tool(name, arguments)

    def test_graph_capabilities(self):
        async def run():
            return await self._call_tool("lineai-graph-capabilities", {})

        out = asyncio.run(run())
        self.assertIsInstance(out, list)
        self.assertIsInstance(out[0], types.TextContent)
        envelope = self._require_graph_or_skip(out[0].text)
        self.assertIn("data", envelope)
        data = envelope["data"]
        self.assertIsNotNone(data)
        self.assertIn("relationshipTypes", data)

    def test_graph_search_and_downstream(self):
        async def search():
            return await self._call_tool(
                "lineai-graph-search",
                {"query": "load", "limit": 15},
            )

        s_out = asyncio.run(search())
        envelope = self._require_graph_or_skip(s_out[0].text)
        nodes = (envelope.get("data") or {}).get("nodes") or []
        if not nodes:
            self.skipTest("No search hits for query 'load' in this workspace")

        node_id = str(nodes[0].get("id") or nodes[0].get("properties", {}).get("id"))
        if not node_id or node_id == "None":
            self.skipTest("Could not resolve node id from search hit")

        async def impact():
            return await self._call_tool(
                "lineai-graph-impact",
                {"seed_node_ids": [node_id]},
            )

        i_out = asyncio.run(impact())
        self._require_graph_or_skip(i_out[0].text)

        async def path():
            return await self._call_tool(
                "lineai-graph-path-explain",
                {"from_node_id": node_id, "to_node_id": node_id, "max_depth": 5},
            )

        p_out = asyncio.run(path())
        self._require_graph_or_skip(p_out[0].text)

        async def validate():
            return await self._call_tool(
                "lineai-graph-validate-change-scope",
                {
                    "seed_node_ids": [node_id],
                    "proposed_change_summary": "E2E graph MCP validate-change-scope",
                },
            )

        v_out = asyncio.run(validate())
        self._require_graph_or_skip(v_out[0].text)

        async def owners():
            return await self._call_tool("lineai-graph-owners", {"node_id": node_id})

        o_out = asyncio.run(owners())
        self._require_graph_or_skip(o_out[0].text)


if __name__ == "__main__":
    unittest.main()
