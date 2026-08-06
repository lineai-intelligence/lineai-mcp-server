import json
import unittest
import mcp.types as types
from unittest.mock import AsyncMock, patch
from lineai_mcp_server.handlers import handle_call_tool
from lineai_mcp_server.utils import extract_relationships


class TestHandleCallTool(unittest.TestCase):

    @patch('lineai_mcp_server.handlers.server.request_context')
    @patch('lineai_mcp_server.handlers.get_mv_id')
    @patch('lineai_mcp_server.handlers.get_method_entity')
    @patch('lineai_mcp_server.handlers.get_impact')
    @patch('lineai_mcp_server.handlers.find_node_by_id')
    async def test_handle_call_tool_method(self, mock_find_node_by_id, mock_get_impact, mock_get_method_entity, mock_get_mv_id, mock_request_context):
        # Setup mocks
        mock_request_context.session.send_log_message = AsyncMock()
        mock_get_mv_id.return_value = 'mv_id'
        mock_get_method_entity.return_value = [{'properties': {'id': 'node_id'}, 'name': 'node_name'}]
        mock_get_impact.return_value = json.dumps({
            'data': {
                'relationships': [{'startId': 'start_id', 'endId': 'end_id', 'type': 'type'}],
                'nodes': [{'id': 'start_id', 'name': 'start_name', 'primaryLabel': 'label'}, {'id': 'end_id', 'name': 'end_name', 'primaryLabel': 'label'}]
            }
        })
        mock_find_node_by_id.side_effect = lambda nodes, id: next(node for node in nodes if node['id'] == id)

        # Call the function
        result = await handle_call_tool('lineai-method-impact', {'method': 'method_name'})

        # Assertions
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Materialized view ID: mv_id")
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Node ID: node_id, Node Name: node_name")
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Impact analysis completed for method_name")
        self.assertEqual(result, [types.TextContent(type="text", text="Impact analysis for method: method_name\n- start_name (type) -> end_name (label)")])

    @patch('lineai_mcp_server.handlers.server.request_context')
    @patch('lineai_mcp_server.handlers.get_mv_id')
    @patch('lineai_mcp_server.handlers.get_method_entity')
    @patch('lineai_mcp_server.handlers.get_impact')
    @patch('lineai_mcp_server.handlers.find_node_by_id')
    async def test_handle_call_tool_function(self, mock_find_node_by_id, mock_get_impact, mock_get_method_entity, mock_get_mv_id, mock_request_context):
        # Setup mocks
        mock_request_context.session.send_log_message = AsyncMock()
        mock_get_mv_id.return_value = 'mv_id'
        mock_get_method_entity.return_value = [{'properties': {'id': 'node_id'}, 'name': 'node_name'}]
        mock_get_impact.return_value = json.dumps({
            'data': {
                'relationships': [{'startId': 'start_id', 'endId': 'end_id', 'type': 'type'}],
                'nodes': [{'id': 'start_id', 'name': 'start_name', 'primaryLabel': 'label'}, {'id': 'end_id', 'name': 'end_name', 'primaryLabel': 'label'}]
            }
        })
        mock_find_node_by_id.side_effect = lambda nodes, id: next(node for node in nodes if node['id'] == id)

        # Call the function
        result = await handle_call_tool('lineai-method-impact', {'function': 'function_name'})

        # Assertions
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Materialized view ID: mv_id")
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Node ID: node_id, Node Name: node_name")
        mock_request_context.session.send_log_message.assert_any_call(level="info", data="Impact analysis completed for function_name")
        self.assertEqual(result, [types.TextContent(type="text", text="Impact analysis for function: function_name\n- start_name (type) -> end_name (label)")])

    async def test_handle_call_tool_unknown_tool(self):
        with self.assertRaises(ValueError) as context:
            await handle_call_tool('unknown-tool', {'method': 'method_name'})
        self.assertEqual(str(context.exception), "Unknown tool: unknown-tool")

    async def test_handle_call_tool_missing_arguments(self):
        with self.assertRaises(ValueError) as context:
            await handle_call_tool('lineai-method-impact', None)
        self.assertEqual(str(context.exception), "Missing arguments")

    async def test_handle_call_tool_missing_method_function(self):
        with self.assertRaises(ValueError) as context:
            await handle_call_tool('lineai-method-impact', {})
        self.assertEqual(str(context.exception), "At least one of method or function must be provided")

    @patch('lineai_mcp_server.handlers.server.request_context')
    async def test_handle_call_tool_missing_request_context(self, mock_request_context):
        mock_request_context.session = None
        with self.assertRaises(LookupError) as context:
            await handle_call_tool('lineai-method-impact', {'method': 'method_name'})
        self.assertEqual(str(context.exception), "Request context is not set")


class TestExtractRelationships(unittest.TestCase):

    def setUp(self):
        self.impact_data = {
            'data': {
                'nodes': [
                    {'id': '1', 'identity': 'identity1', 'name': 'Node1', 'primaryLabel': 'Class'},
                    {'id': '2', 'identity': 'identity2', 'name': 'Node2', 'primaryLabel': 'Method'},
                ],
                'relationships': [
                    {'startId': '1', 'endId': '2', 'type': 'CALLS'},
                ]
            }
        }

    def test_extract_relationships(self):
        expected_output = ["- identity1 (CALLS) -> identity2"]
        result = extract_relationships(self.impact_data)
        self.assertEqual(result, expected_output)

    def test_extract_relationships_no_relationships(self):
        self.impact_data['data']['relationships'] = []
        expected_output = []
        result = extract_relationships(self.impact_data)
        self.assertEqual(result, expected_output)

    def test_extract_relationships_missing_node(self):
        self.impact_data['data']['relationships'] = [
            {'startId': '1', 'endId': '3', 'type': 'CALLS'},
        ]
        expected_output = []
        result = extract_relationships(self.impact_data)
        self.assertEqual(result, expected_output)


if __name__ == '__main__':
    unittest.main()
