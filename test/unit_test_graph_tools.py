# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Unit tests for lineai-graph-* MCP handlers."""

import unittest

import test.test_env  # noqa: F401 — apply DEFAULT_TEST_ENV before package imports
from unittest.mock import patch

from lineai_mcp_server.handlers.graph_tools import (
    handle_lineai_graph_capabilities,
    handle_lineai_graph_impact,
    handle_lineai_graph_search,
    handle_graph_tool,
)


class TestGraphTools(unittest.TestCase):
    def test_search_missing_query(self):
        with self.assertRaises(ValueError):
            handle_lineai_graph_search({})

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.get_mv_id")
    def test_search_success(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        mock_gr.return_value = ({"hits": [], "status": "ok"}, 200, None, "")
        result = handle_lineai_graph_search({"query": "doThing"})
        self.assertEqual(len(result), 1)
        self.assertIn("lineai-graph-search", result[0].text)
        self.assertIn('"status": "ok"', result[0].text)
        mock_gr.assert_called_once()
        _args, kwargs = mock_gr.call_args
        self.assertEqual(_args[0], "POST")
        self.assertEqual(_args[1], "/search")
        body = kwargs.get("json_body") or {}
        self.assertEqual(body.get("materializedViewId"), "mv-1")
        self.assertEqual(body.get("query"), "doThing")

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.get_mv_id")
    def test_search_uses_identity_prefix(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-9"
        mock_gr.return_value = ({}, 200, None, "")
        handle_lineai_graph_search({"identity_prefix": "com.acme|"})
        body = mock_gr.call_args.kwargs["json_body"]
        self.assertEqual(body.get("identityPrefix"), "com.acme|")
        self.assertIsNone(body.get("query"))

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.get_mv_id")
    def test_search_not_deployed(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        mock_gr.return_value = (None, 404, "not_deployed", "")
        result = handle_lineai_graph_search({"query": "x"})
        self.assertIn("Graph API not available", result[0].text)
        self.assertIn("/graph/search", result[0].text)

    def test_impact_requires_seeds(self):
        with self.assertRaises(ValueError):
            handle_lineai_graph_impact({"seed_node_ids": []})
        with self.assertRaises(ValueError):
            handle_lineai_graph_impact({})

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.get_mv_id")
    def test_capabilities_get(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-cap"
        mock_gr.return_value = ({"labels": ["X"]}, 200, None, "")
        handle_lineai_graph_capabilities({})
        mock_gr.assert_called_with(
            "GET",
            "/capabilities",
            json_body=None,
            query_params={"materializedViewId": "mv-cap"},
        )

    def test_dispatch_unknown(self):
        with self.assertRaises(ValueError):
            handle_graph_tool("lineai-graph-unknown", {})


if __name__ == "__main__":
    unittest.main()
