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
from lineai_mcp_server.handlers.method_impact import handle_method_impact
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

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_class_match_is_case_insensitive_substring(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)
        mock_impact.return_value = json.dumps(IMPACT_DATA)

        result = handle_method_impact({'method': 'doThing', 'class': 'gadget'})

        # second search node matched; impact fetched with ITS properties.id and the mv id
        mock_impact.assert_called_once_with('n-2', 'mv-1')
        self.assertIn('# Impact Analysis for Method: `doThing`', _text(result))

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_success_reports_dependents_and_mv(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)
        mock_impact.return_value = json.dumps(IMPACT_DATA)

        result = handle_method_impact({'method': 'doThing'})

        self.assertNotIsInstance(result, types.CallToolResult)
        text = _text(result)
        mock_impact.assert_called_once_with('n-1', 'mv-1')
        self.assertIn('# Impact Analysis for Method: `doThing`', text)
        self.assertIn('`mv-1`', text)
        self.assertIn('`callerMethod` (JavaMethodEntity) via `INVOKES_METHOD`', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_unresolved_references_section_present(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)
        mock_impact.return_value = json.dumps(_impact_with_ur())

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertIn('## Unresolved References (dependency indicators)', text)
        self.assertIn('MethodEntity', text)
        self.assertIn('INVOKES_METHOD', text)

    @patch('lineai_mcp_server.handlers.method_impact.get_impact')
    @patch('lineai_mcp_server.handlers.method_impact.get_method_nodes')
    @patch('lineai_mcp_server.handlers.method_impact.resolve_mv_id')
    def test_unresolved_references_section_absent_when_none(self, mock_resolve, mock_nodes, mock_impact):
        mock_resolve.return_value = 'mv-1'
        mock_nodes.return_value = (SEARCH_NODES, None)
        mock_impact.return_value = json.dumps(IMPACT_DATA)

        text = _text(handle_method_impact({'method': 'doThing'}))

        self.assertNotIn('Unresolved References', text)


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
