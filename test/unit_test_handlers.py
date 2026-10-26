# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Unit tests for the Lineai MCP handlers.

The sync handlers (method/database impact) are tested directly on
``TestCase``; the async ``handle_call_tool`` dispatcher is tested on
``IsolatedAsyncioTestCase`` so the coroutines are actually awaited (the
previous version of this file used plain ``TestCase`` with async tests,
which were never awaited and passed vacuously). Patches target the
handler modules' import sites (``handlers.method_impact.*`` etc.).
"""

import json
import os
import unittest
from unittest.mock import patch

import test.test_env  # noqa: F401 — apply DEFAULT_TEST_ENV before package imports
from test.test_env import TestCase

import mcp.types as types

# NOTE: test.test_env.TestCase reloads lineai_mcp_server.utils / .handlers in
# place for every test. Import-time name bindings of reload-affected classes
# would go stale, so the dispatch tests below take LineaiApiError from the
# live module (utils_module.LineaiApiError) instead of an import-time binding.
import lineai_mcp_server.utils as utils_module
from lineai_mcp_server.utils import LineaiApiError
from lineai_mcp_server.handlers import handle_call_tool, handle_list_tools
from lineai_mcp_server.handlers.method_impact import (
    class_fqn,
    class_key,
    class_match_rank,
    clamp_depth,
    graph_impact_to_impact_data,
    handle_method_impact,
    select_method_node,
)
from lineai_mcp_server.handlers.database_impact import handle_database_impact


def _text(result) -> str:
    """Markdown from a handler result (list of TextContent or CallToolResult)."""
    if isinstance(result, types.CallToolResult):
        return result.content[0].text
    return result[0].text


# --- fixtures -----------------------------------------------------------------

SEARCH_NODES = [
    {
        'id': 'n-1',
        'name': 'doThing',
        'identity': 'com.acme|Widget|doThing()',
        'properties': {'id': 'n-1'},
    },
    {
        'id': 'n-2',
        'name': 'doThing',
        'identity': 'com.acme|Gadget|doThing()',
        'properties': {'id': 'n-2'},
    },
]

IMPACT_DATA = {
    'data': {
        'nodes': [
            {
                'id': 'n-1',
                'name': 'doThing',
                'identity': 'com.acme|Widget|doThing()',
                'primaryLabel': 'JavaMethodEntity',
                'properties': {'statistics.cyclomaticComplexity': 2},
            },
            {
                'id': 'c-1',
                'name': 'callerMethod',
                'identity': 'com.acme|Caller|callerMethod()',
                'primaryLabel': 'JavaMethodEntity',
                'properties': {},
            },
        ],
        'relationships': [
            {'startId': 'c-1', 'endId': 'n-1', 'type': 'INVOKES_METHOD',
             'id': 'r-1', 'contains': False, 'group': False},
        ],
    }
}

UR_NODE = {
    'id': 'ur-1',
    'name': '[MethodEntity] identity EQUALS com.acme|Widget|missing()',
    'primaryLabel': 'SearchNode',
    'properties': {
        'primaryLabel': 'UnresolvedReference',
        'fuzzy': '{"shortName": "missing"}',
        'labels': ['MethodEntity', 'v-42'],
        'queryHash': 'qh-1',
        'id': 'ur-1',
        'name': '[MethodEntity] identity EQUALS com.acme|Widget|missing()',
    },
}


def _impact_with_ur() -> dict:
    data = json.loads(json.dumps(IMPACT_DATA))
    data['data']['nodes'].append(json.loads(json.dumps(UR_NODE)))
    data['data']['relationships'].append(
        {'startId': 'n-1', 'endId': 'ur-1', 'type': 'INVOKES_METHOD',
         'id': 'null', 'contains': False, 'group': False})
    return data


# One class only: the no-`class` success path (two classes is an ambiguity page since LIN-870).
WIDGET_ONLY = [SEARCH_NODES[0]]

# LIN-870: the graph impact endpoint's envelope for the same subgraph as IMPACT_DATA.
GRAPH_IMPACT_DATA = {
    'status': 'ok',
    'assumptionsApplied': ['depth_defaulted_to_3'],
    'truncated': False,
    'depth': 3,
    'direction': 'upstream',
    'nodeCount': 2,
    'staticAnalysisGap': False,
    'nodes': IMPACT_DATA['data']['nodes'],
    'relationships': IMPACT_DATA['data']['relationships'],
}


def _graph_ok(data=None):
    return ({'data': data if data is not None else GRAPH_IMPACT_DATA}, 200, None, '')


def _graph_partial():
    data = json.loads(json.dumps(GRAPH_IMPACT_DATA))
    data['status'] = 'partial'
    data['truncated'] = True
    data['assumptionsApplied'] = ['depth_defaulted_to_3', 'truncated_at_500_nodes']
    data['relationships'] = []
    return _graph_ok(data)


# The dogfood case behind LIN-870: `run()` on AnalyzeCommands exists in three agent repositories.
# Identities use neo4cape's real Java shape: <archive>.jar|<File>.class|<fqn>|<signature>
# (ClassFileAnalyzerUtil.getIdentityForMethodNode); 'alt-1' keeps the archive-less shape.
SQL_RUN = 'cc-sql-agent-1.0.jar|AnalyzeCommands.class|com.codelogic.agent.sql.shell.AnalyzeCommands|run()'
JAVA_RUN = 'cc-java-agent-1.0.jar|AnalyzeCommands.class|com.codelogic.agent.java.shell.AnalyzeCommands|run()'
SRC_RUN = 'codelogic-source-agent-1.0.jar|AnalyzeCommands.class|com.codelogic.agent.source.shell.shell.AnalyzeCommands|run()'
SRC_RUN_STRING = 'codelogic-source-agent-1.0.jar|AnalyzeCommands.class|com.codelogic.agent.source.shell.shell.AnalyzeCommands|run(String)'
ANALYZE_COMMANDS_NODES = [
    {'id': 'sql-1', 'name': 'run', 'identity': SQL_RUN, 'properties': {'id': 'sql-1'}},
    {'id': 'java-1', 'name': 'run', 'identity': JAVA_RUN, 'properties': {'id': 'java-1'}},
    {'id': 'src-1', 'name': 'run', 'identity': SRC_RUN, 'properties': {'id': 'src-1'}},
    {'id': 'src-2', 'name': 'run', 'identity': SRC_RUN_STRING, 'properties': {'id': 'src-2'}},
    {'id': 'alt-1', 'name': 'run', 'identity': 'com.acme.alt.AnalyzeCommands|run()',
     'properties': {'id': 'alt-1'}},
    json.loads(json.dumps(UR_NODE)),
]


# --- lineai-method-impact -----------------------------------------------------

class TestMethodImpactHandler(TestCase):

    def test_missing_arguments_raises(self):
        with self.assertRaises(ValueError):
            handle_method_impact(None)

    def test_missing_method_raises(self):
        with self.assertRaises(ValueError):
            handle_method_impact({'class': 'Widget'})

    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_not_found_is_honest_no_match(self, mock_resolve, mock_nodes):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = ([], 'not_found')

        result = handle_method_impact({'method': 'ghost'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('# Unable to Analyze Method: `ghost`', text)
        self.assertIn('mv-1', text)
        self.assertIn('no-match result', text)
        self.assertIn('not an index or infrastructure problem', text)
        self.assertNotIn('indexed', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_empty_200_routes_to_same_no_match_page(self, mock_resolve, mock_nodes):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = ([], None)  # empty 200: error_kind is None

        result = handle_method_impact({'method': 'ghost'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('no-match result', text)
        self.assertIn('mv-1', text)
        self.assertNotIn('indexed', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_timeout_renders_timeout_page(self, mock_resolve, mock_nodes):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = ([], 'timeout')

        result = handle_method_impact({'method': 'slow'})

        self.assertTrue(result.isError)
        self.assertIn('timed out', _text(result))

    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_http_error_is_infrastructure_only(self, mock_resolve, mock_nodes):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = ([], 'http_error')

        result = handle_method_impact({'method': 'broken'})

        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('infrastructure problem', text)
        # must not claim the method is absent
        self.assertNotIn('does not exist', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_unmatched_class_lists_candidates(self, mock_resolve, mock_nodes):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)

        result = handle_method_impact({'method': 'doThing', 'class': 'NoSuchClass'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('Candidate identities', text)
        self.assertIn('com.acme|Widget|doThing()', text)
        self.assertIn('com.acme|Gadget|doThing()', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_class_match_is_case_insensitive_simple_name(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)
        mock_impact.return_value = _graph_ok()

        result = handle_method_impact({'method': 'doThing', 'class': 'gadget'})

        # second search node matched; the bounded graph impact is fetched with ITS id, upstream, depth 3
        mock_impact.assert_called_once_with('n-2', 'mv-1', direction='upstream', depth=3)
        text = _text(result)
        self.assertIn('# Impact Analysis for Method: `doThing`', text)
        self.assertIn('- **Class**: `com.acme.Gadget`', text)
        self.assertIn('simple class name matched exactly', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_success_reports_dependents_and_mv(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_impact.return_value = _graph_ok()

        result = handle_method_impact({'method': 'doThing'})

        self.assertNotIsInstance(result, types.CallToolResult)
        text = _text(result)
        mock_impact.assert_called_once_with('n-1', 'mv-1', direction='upstream', depth=3)
        self.assertIn('# Impact Analysis for Method: `doThing`', text)
        self.assertIn('`mv-1`', text)
        self.assertIn('`callerMethod` (JavaMethodEntity) via `INVOKES_METHOD`', text)
        self.assertIn('## Impact scope', text)
        self.assertIn('- **Source**: graph (`POST /api/ai-retrieval/graph/impact`, direction `upstream`, depth 3)', text)
        self.assertIn('- **Status**: `ok`', text)
        self.assertIn('- **Static-analysis gap**: no', text)
        self.assertIn('- **Server notes**: `depth_defaulted_to_3`', text)
        self.assertIn('- **Class match**: the only method with that short name', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_no_class_with_two_classes_is_an_ambiguity_page(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)

        result = handle_method_impact({'method': 'doThing'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('# Ambiguous Method: `doThing`', text)
        self.assertIn('**2 classes**', text)
        self.assertIn('- `com.acme.Gadget`', text)
        self.assertIn('- `com.acme.Widget`', text)
        self.assertIn('fully-qualified class name', text)
        mock_impact.assert_not_called()

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_unresolved_references_section_present(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        with_ur = _impact_with_ur()['data']
        data = dict(GRAPH_IMPACT_DATA, nodes=with_ur['nodes'], relationships=with_ur['relationships'])
        mock_impact.return_value = _graph_ok(data)

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertIn('## Unresolved References (dependency indicators)', text)
        self.assertIn('MethodEntity', text)
        self.assertIn('INVOKES_METHOD', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_unresolved_references_section_absent_when_none(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_impact.return_value = _graph_ok()

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertNotIn('Unresolved References', text)

    # --- LIN-870: graph backend, partiality, fallback, depth -----------------

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_fqn_class_selects_the_right_repository(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (ANALYZE_COMMANDS_NODES, None)
        mock_impact.return_value = _graph_ok()

        text = _text(handle_method_impact({
            'method': 'run', 'class': 'com.codelogic.agent.source.shell.shell.AnalyzeCommands'}))

        # the source-agent node, not the sql-agent one that a substring match picks first
        mock_impact.assert_called_once_with('src-1', 'mv-1', direction='upstream', depth=3)
        self.assertIn('- **Class**: `com.codelogic.agent.source.shell.shell.AnalyzeCommands`', text)
        self.assertIn('several signatures in one class', text)
        self.assertIn(f'- **Overloads in this class**: `{SRC_RUN_STRING}`', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_simple_class_name_in_several_repositories_is_ambiguous(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (ANALYZE_COMMANDS_NODES, None)

        result = handle_method_impact({'method': 'run', 'class': 'AnalyzeCommands'})

        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('# Ambiguous Method: `run`', text)
        self.assertIn('**4 classes**', text)
        self.assertIn('com.codelogic.agent.source.shell.shell.AnalyzeCommands', text)
        self.assertNotIn('ur-1', text)
        mock_impact.assert_not_called()

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_foreign_fqn_is_not_silently_replaced(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (ANALYZE_COMMANDS_NODES, None)

        result = handle_method_impact({'method': 'run', 'class': 'com.elsewhere.AnalyzeCommands'})

        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('no match belongs to the class `com.elsewhere.AnalyzeCommands`', text)
        self.assertIn(SQL_RUN, text)
        self.assertIn('Do not report `com.elsewhere.AnalyzeCommands` as "not indexed"', text)
        mock_impact.assert_not_called()

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_partial_graph_result_is_rendered_as_a_lower_bound(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_impact.return_value = _graph_partial()

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertIn('- **Status**: `partial` (truncated_at_500_nodes) — the counts below are a LOWER BOUND', text)
        self.assertIn('the impact result is PARTIAL (see Impact scope) — this is not evidence of isolation', text)
        self.assertNotIn('The change appears to be isolated', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_static_analysis_gap_is_noted(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        data = dict(GRAPH_IMPACT_DATA, staticAnalysisGap=True)
        mock_impact.return_value = _graph_ok(data)

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertIn('- **Static-analysis gap**: yes', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_graph_404_falls_back_to_legacy(self, mock_resolve, mock_nodes, mock_graph, mock_legacy):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_graph.return_value = (None, 404, 'not_deployed', '')
        mock_legacy.return_value = json.dumps(IMPACT_DATA)

        text = _text(handle_method_impact({'method': 'doThing'}))

        mock_legacy.assert_called_once_with('n-1', 'mv-1')
        self.assertIn('- **Source**: legacy (`GET /api/dependency/impact/full/{id}/list`, full incoming chain) — fallback reason: graph impact endpoint returned HTTP 404', text)
        self.assertIn('`callerMethod` (JavaMethodEntity) via `INVOKES_METHOD`', text)
        self.assertNotIn('Static-analysis gap', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_legacy_backend_env_skips_the_graph_endpoint(self, mock_resolve, mock_nodes, mock_graph, mock_legacy):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_legacy.return_value = json.dumps(IMPACT_DATA)

        with patch.dict(os.environ, {'LINEAI_METHOD_IMPACT_BACKEND': 'legacy'}):
            text = _text(handle_method_impact({'method': 'doThing'}))

        mock_graph.assert_not_called()
        mock_legacy.assert_called_once_with('n-1', 'mv-1')
        self.assertIn('fallback reason: LINEAI_METHOD_IMPACT_BACKEND=legacy', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_graph_timeout_is_a_typed_error_not_a_legacy_retry(self, mock_resolve, mock_nodes, mock_graph, mock_legacy):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_graph.return_value = (None, 0, 'timeout', 'read timed out')

        with self.assertRaises(utils_module.LineaiApiError) as ctx:
            handle_method_impact({'method': 'doThing'})

        self.assertEqual(ctx.exception.kind, 'timeout')
        self.assertEqual(ctx.exception.endpoint, '/api/ai-retrieval/graph/impact')
        mock_legacy.assert_not_called()

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_graph_entity_404_is_a_no_match_page_not_a_legacy_fallback(self, mock_resolve, mock_nodes, mock_graph, mock_legacy):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_graph.return_value = (None, 404, 'not_found', "Unable to locate node WITH id 'n-1'")

        result = handle_method_impact({'method': 'doThing'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn("Unable to locate node WITH id 'n-1'", text)
        self.assertIn('the legacy impact endpoint was NOT used as a fallback', text)
        self.assertIn('not evidence that the method is isolated', text)
        mock_legacy.assert_not_called()

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_older_server_without_depth_is_reported_as_unbounded(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        # pre-LIN-870 GraphMcpImpactResponse: no depth/direction fields, reserved markers
        old = {
            'status': 'ok',
            'assumptionsApplied': [
                'direction_parameter_reserved_uses_full_impact_subgraph',
                'depth_parameter_reserved_uses_full_impact_subgraph',
            ],
            'nodeCount': 2,
            'staticAnalysisGap': False,
            'nodes': IMPACT_DATA['data']['nodes'],
            'relationships': IMPACT_DATA['data']['relationships'],
        }
        mock_impact.return_value = _graph_ok(old)

        text = _text(handle_method_impact({'method': 'doThing', 'depth': 2}))

        self.assertIn('on an OLDER server without LIN-870: `depth` and `direction` were ignored', text)
        self.assertNotIn('direction `upstream`, depth 2', text)
        self.assertNotIn('depth 3)', text)
        self.assertIn('- **Server notes**: `direction` was ignored (older server: full impact subgraph); `depth` was ignored (older server: full impact subgraph)', text)
        self.assertNotIn('`depth_parameter_reserved_uses_full_impact_subgraph`', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_graph_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_depth_is_forwarded_and_clamped(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (WIDGET_ONLY, None)
        mock_impact.return_value = _graph_ok()

        handle_method_impact({'method': 'doThing', 'depth': 2})
        mock_impact.assert_called_with('n-1', 'mv-1', direction='upstream', depth=2)

        handle_method_impact({'method': 'doThing', 'depth': 99})
        mock_impact.assert_called_with('n-1', 'mv-1', direction='upstream', depth=10)

        handle_method_impact({'method': 'doThing', 'depth': 0})
        mock_impact.assert_called_with('n-1', 'mv-1', direction='upstream', depth=1)

        with self.assertRaises(ValueError):
            handle_method_impact({'method': 'doThing', 'depth': 'deep'})


class TestMethodNodeSelection(unittest.TestCase):
    """LIN-870: pure class-selection rules behind lineai-method-impact."""

    def test_class_key_drops_the_signature_segment(self):
        self.assertEqual(class_key('com.acme|Widget|doThing()'), 'com.acme|Widget')
        self.assertEqual(class_key('com.acme.Widget|doThing(String)'), 'com.acme.Widget')
        self.assertEqual(class_key('nothing-here'), 'nothing-here')
        self.assertEqual(class_key(None), '')

    def test_class_fqn_drops_archive_and_class_file_segments(self):
        self.assertEqual(class_fqn(SRC_RUN), 'com.codelogic.agent.source.shell.shell.AnalyzeCommands')
        self.assertEqual(class_fqn('com.acme|Widget|doThing()'), 'com.acme.Widget')
        self.assertEqual(class_fqn('com.acme.alt.AnalyzeCommands|run()'), 'com.acme.alt.AnalyzeCommands')
        self.assertEqual(class_fqn('app.war|lib/x.jar|Outer$Inner.class|com.acme.Outer$Inner|m()'), 'com.acme.Outer$Inner')
        self.assertEqual(class_fqn(None), '')

    def test_class_match_rank_real_identity_shape(self):
        """The ticket's case: the identity carries the archive and .class prefix (review on PR #63)."""
        fqn = 'com.codelogic.agent.source.shell.shell.AnalyzeCommands'
        real = 'codelogic-source-agent-1.0.jar|AnalyzeCommands.class|com.codelogic.agent.source.shell.shell.AnalyzeCommands|analyze()'
        self.assertEqual(class_match_rank(real, fqn), 3)
        self.assertEqual(class_match_rank(SRC_RUN, fqn), 3)
        self.assertEqual(class_match_rank(SRC_RUN, fqn.upper()), 3)
        self.assertEqual(class_match_rank(SQL_RUN, fqn), 2)      # same simple name, other repository
        self.assertEqual(class_match_rank(JAVA_RUN, 'com.codelogic.agent.java.shell.AnalyzeCommands'), 3)
        self.assertEqual(class_match_rank(SRC_RUN, 'AnalyzeCommands'), 2)
        self.assertEqual(class_match_rank(SRC_RUN, 'Commands'), 1)
        # inner classes in the real shape
        inner = 'x-1.0.jar|Outer$Inner.class|com.acme.Outer$Inner|m()'
        self.assertEqual(class_match_rank(inner, 'com.acme.Outer.Inner'), 3)
        self.assertEqual(class_match_rank(inner, 'com.acme.Outer$Inner'), 3)
        # the archive name must never satisfy the FQN (it is dotted, so it exercises the FQN rules)
        self.assertLess(class_match_rank(SRC_RUN, 'codelogic-source-agent-1.0.jar'), 3)
        self.assertLess(class_match_rank(SRC_RUN, 'AnalyzeCommands.class'), 3)

    def test_class_match_rank_table(self):
        src = 'com.codelogic.agent.source.shell.shell|AnalyzeCommands|run()'
        self.assertEqual(class_match_rank(src, 'com.codelogic.agent.source.shell.shell.AnalyzeCommands'), 3)
        self.assertEqual(class_match_rank(src, 'COM.codelogic.agent.source.shell.shell.analyzecommands'), 3)
        self.assertEqual(class_match_rank('com.acme.alt.AnalyzeCommands|run()', 'com.acme.alt.AnalyzeCommands'), 3)
        self.assertEqual(class_match_rank('com.acme|Outer$Inner|run()', 'com.acme.Outer.Inner'), 3)
        self.assertEqual(class_match_rank('com.acme|Outer|Inner|run()', 'com.acme.Outer$Inner'), 3)
        self.assertEqual(class_match_rank(src, 'com.codelogic.agent.sql.shell.AnalyzeCommands'), 2)
        self.assertEqual(class_match_rank(src, 'AnalyzeCommands'), 2)
        self.assertEqual(class_match_rank(src, 'analyzecommands'), 2)
        self.assertEqual(class_match_rank(src, 'Commands'), 1)
        self.assertEqual(class_match_rank(src, 'shell'), 1)
        self.assertEqual(class_match_rank(src, 'Widget'), 0)
        self.assertEqual(class_match_rank(None, 'Widget'), 0)

    def test_fqn_selects_exactly_that_class_with_overloads(self):
        chosen, outcome, candidates = select_method_node(
            ANALYZE_COMMANDS_NODES, 'com.codelogic.agent.source.shell.shell.AnalyzeCommands')
        self.assertEqual(chosen['id'], 'src-1')
        self.assertEqual(outcome, 'overloads')
        self.assertEqual([c['id'] for c in candidates], ['src-1', 'src-2'])

    def test_fqn_single_signature(self):
        chosen, outcome, _ = select_method_node(ANALYZE_COMMANDS_NODES, 'com.codelogic.agent.java.shell.AnalyzeCommands')
        self.assertEqual(chosen['id'], 'java-1')
        self.assertEqual(outcome, 'fqn')

    def test_fqn_alternative_identity_shape(self):
        chosen, outcome, _ = select_method_node(ANALYZE_COMMANDS_NODES, 'com.acme.alt.AnalyzeCommands')
        self.assertEqual(chosen['id'], 'alt-1')
        self.assertEqual(outcome, 'fqn')

    def test_simple_name_across_repositories_is_ambiguous(self):
        chosen, outcome, candidates = select_method_node(ANALYZE_COMMANDS_NODES, 'AnalyzeCommands')
        self.assertIsNone(chosen)
        self.assertEqual(outcome, 'ambiguous')
        self.assertEqual(len(candidates), 5)

    def test_foreign_fqn_is_fqn_not_found_with_same_simple_name_candidates(self):
        chosen, outcome, candidates = select_method_node(ANALYZE_COMMANDS_NODES, 'com.elsewhere.AnalyzeCommands')
        self.assertIsNone(chosen)
        self.assertEqual(outcome, 'fqn_not_found')
        self.assertEqual(len(candidates), 5)

    def test_no_class_with_several_classes_is_ambiguous(self):
        chosen, outcome, _ = select_method_node(ANALYZE_COMMANDS_NODES, None)
        self.assertIsNone(chosen)
        self.assertEqual(outcome, 'ambiguous')

    def test_no_class_single_class_picks_first_and_lists_overloads(self):
        nodes = [ANALYZE_COMMANDS_NODES[2], ANALYZE_COMMANDS_NODES[3]]
        chosen, outcome, candidates = select_method_node(nodes, None)
        self.assertEqual(chosen['id'], 'src-1')
        self.assertEqual(outcome, 'overloads')
        self.assertEqual(len(candidates), 2)
        chosen, outcome, _ = select_method_node(nodes[:1], None)
        self.assertEqual(outcome, 'single')

    def test_unresolved_references_are_never_chosen(self):
        chosen, outcome, candidates = select_method_node([json.loads(json.dumps(UR_NODE))], None)
        self.assertIsNone(chosen)
        self.assertEqual(outcome, 'no_class_match')
        self.assertEqual(candidates, [])

    def test_substring_match_is_still_accepted_when_unique(self):
        chosen, outcome, _ = select_method_node(SEARCH_NODES, 'gadg')
        self.assertEqual(chosen['id'], 'n-2')
        self.assertEqual(outcome, 'substring')
        chosen, outcome, _ = select_method_node(SEARCH_NODES, 'NoSuchClass')
        self.assertIsNone(chosen)
        self.assertEqual(outcome, 'no_class_match')

    def test_clamp_depth(self):
        self.assertEqual(clamp_depth(None), 3)
        self.assertEqual(clamp_depth(''), 3)
        self.assertEqual(clamp_depth('2'), 2)
        self.assertEqual(clamp_depth(0), 1)
        self.assertEqual(clamp_depth(11), 10)
        with self.assertRaises(ValueError):
            clamp_depth('x')

    def test_graph_payload_maps_to_the_legacy_shape_and_is_stripped(self):
        payload = {'data': dict(GRAPH_IMPACT_DATA, nodes=[{
            'id': 'n-1', 'identity': 'com.acme|Widget|doThing()', 'name': 'doThing',
            'primaryLabel': 'JavaMethodEntity',
            'properties': {'id': 'n-1', 'shortName': 'doThing', 'statistics.cyclomaticComplexity': 2, 'sourceScanContextIds': ['x']},
        }])}
        mapped = graph_impact_to_impact_data(payload)
        self.assertEqual(set(mapped), {'data'})
        node = mapped['data']['nodes'][0]
        self.assertEqual(node['id'], 'n-1')
        self.assertEqual(node['properties'], {'statistics.cyclomaticComplexity': 2})
        self.assertEqual(mapped['data']['relationships'][0]['startId'], 'c-1')
        # bare (un-enveloped) payloads are tolerated
        self.assertEqual(graph_impact_to_impact_data(GRAPH_IMPACT_DATA)['data']['nodes'][0]['id'], 'n-1')


# --- lineai-database-impact ---------------------------------------------------

DB_SEARCH_RESULTS = [{'id': 'db-1', 'name': 'ORDERS', 'schema': 'SALES'}]

DB_IMPACT_DATA = {
    'data': {
        'nodes': [
            {'id': 'db-1', 'name': 'ORDERS', 'identity': 'db|SALES|ORDERS',
             'primaryLabel': 'Table', 'properties': {}},
        ],
        'relationships': [],
    }
}


class TestDatabaseImpactHandler(TestCase):

    def test_missing_arguments_raises(self):
        with self.assertRaises(ValueError):
            handle_database_impact(None)

    def test_column_without_table_raises(self):
        with self.assertRaises(ValueError):
            handle_database_impact({'entity_type': 'column', 'name': 'ID'})

    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_search_http_error_is_error_page(self, mock_resolve, mock_search):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = ([], 'http_error')

        result = handle_database_impact({'entity_type': 'table', 'name': 'ORDERS'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('infrastructure problem', text)
        self.assertNotIn('No tables found', text)

    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_search_timeout_is_error_page(self, mock_resolve, mock_search):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = ([], 'timeout')

        result = handle_database_impact({'entity_type': 'table', 'name': 'ORDERS'})

        self.assertTrue(result.isError)
        self.assertIn('timed out', _text(result))

    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_no_match_cites_mv_id(self, mock_resolve, mock_search):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = ([], None)  # empty 200

        result = handle_database_impact({'entity_type': 'table', 'name': 'GHOST'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn("No tables found matching 'GHOST'", text)
        self.assertIn('mv-1', text)
        self.assertIn('no-match result', text)

    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_search_404_is_same_no_match_page(self, mock_resolve, mock_search):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = ([], 'not_found')

        result = handle_database_impact({'entity_type': 'table', 'name': 'GHOST'})

        self.assertTrue(result.isError)
        self.assertIn('no-match result', _text(result))

    @patch('lineai_mcp_server.handlers.database_impact.get_impact')
    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_success_scopes_impact_to_mv(self, mock_resolve, mock_search, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = (DB_SEARCH_RESULTS, None)
        mock_impact.return_value = json.dumps(DB_IMPACT_DATA)

        result = handle_database_impact({'entity_type': 'table', 'name': 'ORDERS'})

        self.assertNotIsInstance(result, types.CallToolResult)
        mock_impact.assert_called_once_with('db-1', 'mv-1')
        self.assertIn('# Database Impact Analysis', _text(result))

    @patch('lineai_mcp_server.handlers.database_impact.get_impact')
    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_per_entity_failures_are_rendered(self, mock_resolve, mock_search, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = (DB_SEARCH_RESULTS + [{'id': 'db-2', 'name': 'ORDER_ITEMS'}], None)
        mock_impact.side_effect = [
            json.dumps(DB_IMPACT_DATA),
            LineaiApiError('gateway_timeout', status=504, endpoint='x'),
        ]

        result = handle_database_impact({'entity_type': 'table', 'name': 'ORDER'})

        # overall report still a success; the failure is rendered, not swallowed
        self.assertNotIsInstance(result, types.CallToolResult)
        text = _text(result)
        self.assertIn('## Impact retrieval failures', text)
        self.assertIn('ORDER_ITEMS', text)
        self.assertIn('gateway_timeout', text)

    @patch('lineai_mcp_server.handlers.database_impact.get_impact')
    @patch('lineai_mcp_server.handlers.database_impact.search_database_entity')
    @patch('lineai_mcp_server.handlers.database_impact.resolve_mv_id')
    def test_unresolved_references_section_present(self, mock_resolve, mock_search, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_search.return_value = (DB_SEARCH_RESULTS, None)
        db_impact = json.loads(json.dumps(DB_IMPACT_DATA))
        db_impact['data']['nodes'].append(json.loads(json.dumps(UR_NODE)))
        mock_impact.return_value = json.dumps(db_impact)

        text = _text(handle_database_impact({'entity_type': 'table', 'name': 'ORDERS'}))

        self.assertIn('## Unresolved References (dependency indicators)', text)


# --- handle_call_tool dispatch ------------------------------------------------

class TestHandleCallToolDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_unknown_tool_is_error_result(self):
        result = await handle_call_tool('unknown-tool', {})
        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn('Unknown tool: unknown-tool', _text(result))

    @patch('lineai_mcp_server.handlers.handle_method_impact')
    async def test_lineai_api_error_renders_taxonomy_page(self, mock_handler):
        mock_handler.side_effect = utils_module.LineaiApiError(
            'mv_not_found', status=404, detail="No materialized view definition named 'nope'",
            endpoint='https://example.lineai.test/api/materialized-view-definition/name')

        result = await handle_call_tool('lineai-method-impact', {'method': 'x', 'workspace': 'nope'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        text = _text(result)
        self.assertIn('Workspace / materialized view not found', text)
        self.assertIn("No materialized view definition named 'nope'", text)

    @patch('lineai_mcp_server.handlers.handle_method_impact')
    async def test_auth_error_renders_auth_page(self, mock_handler):
        mock_handler.side_effect = utils_module.LineaiApiError('auth', status=401)

        result = await handle_call_tool('lineai-method-impact', {'method': 'x'})

        self.assertTrue(result.isError)
        self.assertIn('Authentication failed', _text(result))

    @patch('lineai_mcp_server.handlers.handle_database_impact')
    async def test_generic_exception_renders_generic_page(self, mock_handler):
        mock_handler.side_effect = RuntimeError('boom')

        result = await handle_call_tool('lineai-database-impact', {'entity_type': 'table', 'name': 'T'})

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn('Error executing tool: lineai-database-impact', _text(result))
        self.assertIn('boom', _text(result))

    @patch('lineai_mcp_server.handlers.handle_method_impact')
    async def test_method_impact_success_passthrough(self, mock_handler):
        payload = [types.TextContent(type='text', text='# Impact Analysis for Method: `x`')]
        mock_handler.return_value = payload

        result = await handle_call_tool('lineai-method-impact', {'method': 'x'})

        self.assertEqual(result, payload)
        mock_handler.assert_called_once_with({'method': 'x'})

    @patch('lineai_mcp_server.handlers.handle_graph_tool')
    async def test_graph_tool_dispatch_passthrough(self, mock_handler):
        payload = [types.TextContent(type='text', text='# lineai-graph-search')]
        mock_handler.return_value = payload

        result = await handle_call_tool('lineai-graph-search', {'query': 'x'})

        self.assertEqual(result, payload)
        mock_handler.assert_called_once_with('lineai-graph-search', {'query': 'x'})


# --- tool schemas -------------------------------------------------------------

class TestToolSchemas(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        tools = await handle_list_tools()
        self.tools = {tool.name: tool for tool in tools}

    async def test_all_eight_tools_listed(self):
        self.assertEqual(
            set(self.tools),
            {
                'lineai-method-impact',
                'lineai-database-impact',
                'lineai-graph-capabilities',
                'lineai-graph-search',
                'lineai-graph-impact',
                'lineai-graph-path-explain',
                'lineai-graph-validate-change-scope',
                'lineai-graph-owners',
            },
        )

    async def test_method_impact_requires_only_method(self):
        schema = self.tools['lineai-method-impact'].inputSchema
        self.assertEqual(schema['required'], ['method'])
        self.assertIn('class', schema['properties'])
        # LIN-870: bounded depth, fully-qualified class guidance
        self.assertEqual(schema['properties']['depth']['minimum'], 1)
        self.assertEqual(schema['properties']['depth']['maximum'], 10)
        self.assertIn('fully-qualified', schema['properties']['class']['description'])
        self.assertIn('partial', self.tools['lineai-method-impact'].description)

    async def test_graph_descriptions_explain_ranking_and_partiality(self):
        search = self.tools['lineai-graph-search']
        self.assertIn('RANKED', search.description)
        self.assertIn('matchKind', search.description)
        self.assertIn('totalHitCount', search.description)
        self.assertIn("report 'not indexed' ONLY when", search.description)
        self.assertIn('description', search.inputSchema['properties']['prefer_latest_scan'])
        impact = self.tools['lineai-graph-impact']
        self.assertIn('LOWER BOUND', impact.description)
        self.assertEqual(impact.inputSchema['properties']['depth']['maximum'], 10)
        self.assertIn('upstream', impact.inputSchema['properties']['direction']['description'])
        for name, tool in self.tools.items():
            self.assertTrue(tool.description.strip(), f'{name} has an empty description')

    async def test_all_tools_accept_workspace_and_mv_id(self):
        for name, tool in self.tools.items():
            properties = tool.inputSchema['properties']
            self.assertIn('workspace', properties, f'{name} lacks workspace')
            self.assertIn('materialized_view_id', properties, f'{name} lacks materialized_view_id')

    async def test_capabilities_has_camel_alias_and_stays_closed(self):
        schema = self.tools['lineai-graph-capabilities'].inputSchema
        self.assertIn('materializedViewId', schema['properties'])
        self.assertIs(schema.get('additionalProperties'), False)

    async def test_descriptions_document_precedence(self):
        for name, tool in self.tools.items():
            self.assertIn('Scope resolution precedence', tool.description, name)


if __name__ == '__main__':
    unittest.main()
