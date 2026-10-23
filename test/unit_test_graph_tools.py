# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Unit tests for lineai-graph-* MCP handlers."""

import unittest

import test.test_env  # noqa: F401 — apply DEFAULT_TEST_ENV before package imports
from unittest.mock import patch

import mcp.types as types

from lineai_mcp_server.handlers.graph_tools import (
    graph_status_note,
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
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
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
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
    def test_search_uses_identity_prefix(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-9"
        mock_gr.return_value = ({}, 200, None, "")
        handle_lineai_graph_search({"identity_prefix": "com.acme|"})
        body = mock_gr.call_args.kwargs["json_body"]
        self.assertEqual(body.get("identityPrefix"), "com.acme|")
        self.assertIsNone(body.get("query"))

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
    def test_search_not_deployed(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        mock_gr.return_value = (None, 404, "not_deployed", "")
        result = handle_lineai_graph_search({"query": "x"})
        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn("Graph API not available", result.content[0].text)
        self.assertIn("/graph/search", result.content[0].text)

    def test_impact_requires_seeds(self):
        with self.assertRaises(ValueError):
            handle_lineai_graph_impact({"seed_node_ids": []})
        with self.assertRaises(ValueError):
            handle_lineai_graph_impact({})

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
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

    # --- LIN-870 ---

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
    def test_impact_forwards_depth_and_direction(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        mock_gr.return_value = ({"data": {"status": "ok", "nodes": [], "relationships": []}}, 200, None, "")
        result = handle_lineai_graph_impact({"seed_node_ids": ["1"], "depth": 2, "direction": "upstream"})
        body = mock_gr.call_args.kwargs["json_body"]
        self.assertEqual(body["depth"], 2)
        self.assertEqual(body["direction"], "upstream")
        self.assertEqual(body["seedNodeIds"], ["1"])
        self.assertNotIn("> **Status", result[0].text)

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
    def test_search_forwards_prefer_latest_scan_and_scan_space(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        mock_gr.return_value = ({"data": {"nodes": [], "hits": []}}, 200, None, "")
        handle_lineai_graph_search({"query": "x", "prefer_latest_scan": True, "scan_space": "main"})
        body = mock_gr.call_args.kwargs["json_body"]
        self.assertIs(body["preferLatestScan"], True)
        self.assertEqual(body["scanSpace"], "main")

    @patch("lineai_mcp_server.handlers.graph_tools.graph_request")
    @patch("lineai_mcp_server.handlers.graph_tools.resolve_mv_id")
    def test_partial_impact_is_noted_above_the_json(self, mock_mv, mock_gr):
        mock_mv.return_value = "mv-1"
        payload = {"data": {"status": "partial", "assumptionsApplied": ["truncated_at_500_nodes"], "nodes": []}}
        mock_gr.return_value = (payload, 200, None, "")
        text = handle_lineai_graph_impact({"seed_node_ids": ["1"]})[0].text
        note_at = text.index("> **Status: partial** (truncated_at_500_nodes)")
        self.assertLess(note_at, text.index("```json"))
        self.assertIn("lower bound", text)
        self.assertIn("smaller `depth`", text)

    def test_status_note_covers_old_server_markers_and_gap(self):
        note = graph_status_note({"data": {
            "status": "ok",
            "assumptionsApplied": [
                "depth_parameter_reserved_uses_full_impact_subgraph",
                "prefer_latest_scan_not_implemented",
                "scan_space_unknown:feature-x",
                "scan_space_not_applied_on_impact",
                "depth_defaulted_to_3",
            ],
            "staticAnalysisGap": True,
        }})
        self.assertIn("`depth` was ignored (older server", note)
        self.assertIn("`prefer_latest_scan` was ignored (older server)", note)
        self.assertIn("`scan_space` `feature-x` matched no scan space", note)
        self.assertIn("not applied to impact", note)
        self.assertIn("Static-analysis gap", note)
        self.assertNotIn("depth_defaulted_to_3", note)
        self.assertNotIn("Status:", note)

    def test_status_note_tolerates_bare_and_odd_payloads(self):
        self.assertEqual(graph_status_note({"status": "ok", "assumptionsApplied": []}), "")
        self.assertIn("Status: partial", graph_status_note({"status": "partial"}))
        self.assertEqual(graph_status_note(None), "")
        self.assertEqual(graph_status_note(["not", "a", "dict"]), "")
        self.assertEqual(graph_status_note({"data": None}), "")


if __name__ == "__main__":
    unittest.main()
