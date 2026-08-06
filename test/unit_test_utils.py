import unittest
from unittest import mock
from unittest.mock import Mock
import json
from datetime import datetime, timedelta
from io import StringIO
from lineai_mcp_server.utils import strip_unused_properties, find_api_endpoints
from lineai_mcp_server import utils
from test.test_env import TestCase


class TestUtils(TestCase):

    def test_strip_unused_properties(self):
        response_mock = Mock()
        response_mock.text = json.dumps({
            "data": {
                "nodes": [
                    {
                        "properties": {
                            "otherProperty1": "should_remain",
                            "agentIds": [
                                "eee5b2fa-966a-442f-9dff-06612062eb4c"
                            ],
                            "sourceScanContextIds": [
                                "845742f8-5bda-4c8a-a3ba-24da824e7b3d"
                            ],
                            "isScanRoot": False,
                            "transitiveSourceNodeId": "927d520a-2118-44f5-9088-0a0141a86b38",
                            "dataSourceId": "netCape",
                            "scanContextId": "845742f8-5bda-4c8a-a3ba-24da824e7b3d",
                            "id": "f1a6b838-cc55-41a1-a43c-32de78722ffa",
                            "shortName": "SubscriptionCreatedDomainEvent",
                            "materializedViewId": "a15b3f42-93e9-4c91-8a36-a465e865436e",
                            "otherProperty2": "should_remain",
                            "statistics.impactScore": 0
                        }
                    }
                ]
            }
        })

        expected_output = json.dumps({
            "data": {
                "nodes": [
                    {
                        "properties": {
                            "otherProperty1": "should_remain",
                            "otherProperty2": "should_remain"
                        }
                    }
                ]
            }
        })

        result = strip_unused_properties(response_mock)
        self.assertEqual(result, expected_output)

    def test_strip_unused_properties_empty_nodes(self):
        response_mock = Mock()
        response_mock.text = json.dumps({
            "data": {
                "nodes": []
            }
        })

        expected_output = json.dumps({
            "data": {
                "nodes": []
            }
        })

        result = strip_unused_properties(response_mock)
        self.assertEqual(result, expected_output)

    def test_strip_unused_properties_no_data(self):
        response_mock = Mock()
        response_mock.text = json.dumps({})

        expected_output = json.dumps({})

        result = strip_unused_properties(response_mock)
        self.assertEqual(result, expected_output)


class TestTokenCaching(TestCase):
    """Test caching of authentication tokens."""

    def setUp(self):
        super().setUp()  # Set up clean test environment
        # Reset cached values
        utils._cached_token = None
        utils._token_expiry = None
        # No need to set environment variables - handled by TestCase

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_authenticate_caches_token(self, mock_datetime, mock_post):
        """Test that authenticate() caches the token and returns it."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'access_token': 'test_token'}
        mock_post.return_value = mock_response

        # Call authenticate
        token = utils.authenticate()

        # Verify token is cached and returned
        self.assertEqual(token, 'test_token')
        self.assertEqual(utils._cached_token, 'test_token')
        self.assertEqual(utils._token_expiry, now + timedelta(seconds=utils.TOKEN_CACHE_TTL))

        # Verify request was made correctly
        mock_post.assert_called_once()
        url_arg = mock_post.call_args[0][0]
        self.assertEqual(url_arg, 'https://example.lineai.test/codelogic/server/authenticate')

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_authenticate_uses_cached_token(self, mock_datetime, mock_post):
        """Test that authenticate() returns cached token without making requests."""
        # Set up initial token cache
        now = datetime(2023, 1, 1, 12, 0, 0)
        utils._cached_token = 'cached_token'
        utils._token_expiry = now + timedelta(seconds=3600)  # Valid for 1 hour

        # Set current time to be before expiry
        mock_datetime.now.return_value = now + timedelta(seconds=1800)  # 30 minutes later

        # Call authenticate
        token = utils.authenticate()

        # Verify cached token is returned without making requests
        self.assertEqual(token, 'cached_token')
        mock_post.assert_not_called()

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_authenticate_refreshes_expired_token(self, mock_datetime, mock_post):
        """Test that authenticate() refreshes token when the cached one expires."""
        # Set up initial expired token cache
        now = datetime(2023, 1, 1, 12, 0, 0)
        utils._cached_token = 'expired_token'
        utils._token_expiry = now - timedelta(seconds=60)  # Expired 1 minute ago

        # Set current time to be after expiry
        mock_datetime.now.return_value = now

        # Set up mock response for new token
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'access_token': 'new_token'}
        mock_post.return_value = mock_response

        # Call authenticate
        token = utils.authenticate()

        # Verify new token is fetched and cached
        self.assertEqual(token, 'new_token')
        self.assertEqual(utils._cached_token, 'new_token')
        mock_post.assert_called_once()


class TestMethodNodesCaching(TestCase):
    """Test caching of method nodes."""

    def setUp(self):
        super().setUp()  # Set up clean test environment
        # Reset cached values before each test
        utils._method_nodes_cache = {}

        # Mock stderr to capture logging
        self.stderr_patcher = mock.patch('sys.stderr', new_callable=StringIO)
        self.mock_stderr = self.stderr_patcher.start()
        # No need to set environment variables - handled by TestCase

    def tearDown(self):
        self.stderr_patcher.stop()
        super().tearDown()  # Call parent tearDown to restore environment

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_method_nodes_caches_results(self, mock_datetime, mock_post, mock_authenticate):
        """Test that get_method_nodes() caches and returns method nodes."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now

        # Set up mock token
        mock_authenticate.return_value = 'test_token'

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'data': [{'id': '1', 'name': 'test_method'}]}
        mock_post.return_value = mock_response

        # Call get_method_nodes
        nodes, err = utils.get_method_nodes('mv-123', 'test.method')
        cache_key = 'mv-123:test.method'

        # Verify results are cached and returned
        self.assertIsNone(err)
        self.assertEqual(nodes, [{'id': '1', 'name': 'test_method'}])
        self.assertIn(cache_key, utils._method_nodes_cache)
        cached_nodes, expiry = utils._method_nodes_cache[cache_key]
        self.assertEqual(cached_nodes, [{'id': '1', 'name': 'test_method'}])
        self.assertEqual(expiry, now + timedelta(seconds=utils.METHOD_CACHE_TTL))

        # Verify logging message
        self.assertIn(f"Method nodes cached for test.method with TTL {utils.METHOD_CACHE_TTL}s",
                      self.mock_stderr.getvalue())

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_method_nodes_uses_cache(self, mock_datetime, mock_post, mock_authenticate):
        """Test that get_method_nodes() uses cached values."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)
        future = now + timedelta(seconds=60)  # 1 minute later

        # Set up initial cache with data valid for 5 minutes
        cache_key = 'mv-123:test.method'
        cached_data = [{'id': '1', 'name': 'cached_method'}]
        utils._method_nodes_cache[cache_key] = (cached_data, now + timedelta(seconds=300))

        # Set current time to 1 minute after now (cache still valid)
        mock_datetime.now.return_value = future

        # Call get_method_nodes
        nodes, err = utils.get_method_nodes('mv-123', 'test.method')

        # Verify cached data is returned without making requests
        self.assertIsNone(err)
        self.assertEqual(nodes, cached_data)
        mock_authenticate.assert_not_called()
        mock_post.assert_not_called()

        # Verify cache hit message
        self.assertIn("Method nodes cache hit for test.method", self.mock_stderr.getvalue())

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_method_nodes_refreshes_expired_cache(self, mock_datetime, mock_post, mock_authenticate):
        """Test that get_method_nodes() refreshes expired cache."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)

        # Set up expired cache
        cache_key = 'mv-123:test.method'
        cached_data = [{'id': '1', 'name': 'expired_method'}]
        utils._method_nodes_cache[cache_key] = (cached_data, now - timedelta(seconds=60))

        # Set current time
        mock_datetime.now.return_value = now

        # Set up mock token
        mock_authenticate.return_value = 'test_token'

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'data': [{'id': '2', 'name': 'new_method'}]}
        mock_post.return_value = mock_response

        # Call get_method_nodes
        nodes, err = utils.get_method_nodes('mv-123', 'test.method')

        # Verify new data is fetched, cached and returned
        self.assertIsNone(err)
        self.assertEqual(nodes, [{'id': '2', 'name': 'new_method'}])
        self.assertIn(cache_key, utils._method_nodes_cache)
        new_cached_nodes, _ = utils._method_nodes_cache[cache_key]
        self.assertEqual(new_cached_nodes, [{'id': '2', 'name': 'new_method'}])

        # Verify cache expired message
        self.assertIn("Method nodes cache expired for test.method", self.mock_stderr.getvalue())

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_method_nodes_404_returns_not_found(self, mock_datetime, mock_post, mock_authenticate):
        """404 from shortname search yields empty list and not_found (no raise_for_status)."""
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now
        mock_authenticate.return_value = 'test_token'
        mock_response = mock.MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "status": "error",
            "error": {
                "code": "NOT_FOUND",
                "message": 'Could not find any nodes with shortname "foo".',
                "details": [],
            },
        }
        mock_post.return_value = mock_response

        nodes, err = utils.get_method_nodes('mv-123', 'foo')

        self.assertEqual(nodes, [])
        self.assertEqual(err, "not_found")
        mock_response.raise_for_status.assert_not_called()


class TestImpactCaching(TestCase):
    """Test caching of impact data."""

    def setUp(self):
        super().setUp()  # Set up clean test environment
        # Reset cached values before each test
        utils._impact_cache = {}

        # Mock stderr to capture logging
        self.stderr_patcher = mock.patch('sys.stderr', new_callable=StringIO)
        self.mock_stderr = self.stderr_patcher.start()
        # No need to set environment variables - handled by TestCase

    def tearDown(self):
        self.stderr_patcher.stop()
        super().tearDown()  # Call parent tearDown to restore environment

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_impact_caches_results(self, mock_datetime, mock_get, mock_authenticate):
        """Test that get_impact() caches and returns stripped impact data."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now

        # Set up mock token
        mock_authenticate.return_value = 'test_token'

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = json.dumps({
            'data': {
                'nodes': [
                    {
                        'id': '1',
                        'name': 'test_node',
                        'primaryLabel': 'Method',
                        'properties': {
                            'agentIds': ['to-remove'],
                            'sourceScanContextIds': ['to-remove'],
                            'isScanRoot': True,
                            'keep': 'value'
                        }
                    }
                ]
            }
        })
        mock_get.return_value = mock_response

        # Call get_impact
        impact = utils.get_impact('node-123')

        # Verify results are cached and returned
        self.assertIn('node-123', utils._impact_cache)
        cached_impact, expiry = utils._impact_cache['node-123']

        # Verify the impact data is properly stripped
        impact_data = json.loads(impact)
        self.assertNotIn('agentIds', impact_data['data']['nodes'][0]['properties'])
        self.assertNotIn('sourceScanContextIds', impact_data['data']['nodes'][0]['properties'])
        self.assertNotIn('isScanRoot', impact_data['data']['nodes'][0]['properties'])
        self.assertIn('keep', impact_data['data']['nodes'][0]['properties'])

        # Verify expiry time
        self.assertEqual(expiry, now + timedelta(seconds=utils.IMPACT_CACHE_TTL))

        # Verify logging message
        self.assertIn(f"Impact cached for node-123 with TTL {utils.IMPACT_CACHE_TTL}s",
                      self.mock_stderr.getvalue())

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_impact_uses_cache(self, mock_datetime, mock_get, mock_authenticate):
        """Test that get_impact() uses cached values."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)
        future = now + timedelta(seconds=60)  # 1 minute later

        # Set up initial cache with data valid for 5 minutes
        cached_data = '{"data": {"nodes": [{"name": "cached_impact"}]}}'
        utils._impact_cache['node-123'] = (cached_data, now + timedelta(seconds=300))

        # Set current time to 1 minute after now (cache still valid)
        mock_datetime.now.return_value = future

        # Call get_impact
        impact = utils.get_impact('node-123')

        # Verify cached data is returned without making requests
        self.assertEqual(impact, cached_data)
        mock_authenticate.assert_not_called()
        mock_get.assert_not_called()

        # Verify cache hit message
        self.assertIn("Impact cache hit for node-123", self.mock_stderr.getvalue())

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_get_impact_refreshes_expired_cache(self, mock_datetime, mock_get, mock_authenticate):
        """Test that get_impact() refreshes expired cache."""
        # Set up mock datetime
        now = datetime(2023, 1, 1, 12, 0, 0)

        # Set up expired cache
        cached_data = '{"data": {"nodes": [{"name": "expired_impact"}]}}'
        utils._impact_cache['node-123'] = (cached_data, now - timedelta(seconds=60))

        # Set current time
        mock_datetime.now.return_value = now

        # Set up mock token
        mock_authenticate.return_value = 'test_token'

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = json.dumps({
            'data': {
                'nodes': [
                    {'id': '2', 'name': 'new_impact'}
                ]
            }
        })
        mock_get.return_value = mock_response

        # Call get_impact
        impact = utils.get_impact('node-123')

        # Verify new data is fetched, cached and returned
        impact_data = json.loads(impact)
        self.assertEqual(impact_data['data']['nodes'][0]['name'], 'new_impact')

        # Verify cache expired message
        self.assertIn("Impact cache expired for node-123", self.mock_stderr.getvalue())


class TestFindApiEndpoints(unittest.TestCase):
    """Test the find_api_endpoints utility function"""

    def test_find_api_endpoints_with_annotations(self):
        """Test finding API endpoints with annotations"""
        # Mock nodes with REST annotations
        nodes = [
            {
                'id': '1',
                'name': 'getUser',
                'primaryLabel': 'JavaMethodEntity',
                'properties': {
                    'annotations': ['@GetMapping("/api/users/{id}")']
                }
            },
            {
                'id': '2',
                'name': 'UserController',
                'primaryLabel': 'JavaClassEntity',
                'properties': {}
            }
        ]

        # Mock relationships
        relationships = [
            {
                'startId': '1',
                'endId': '2',
                'type': 'CONTAINS_METHOD'
            }
        ]

        # Call the function
        endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, relationships)

        # Assert results
        self.assertEqual(len(rest_endpoints), 1)
        self.assertEqual(rest_endpoints[0]['name'], 'getUser')
        self.assertIn('@GetMapping', rest_endpoints[0]['annotation'])

    def test_find_api_endpoints_with_controllers(self):
        """Test finding API controllers"""
        # Mock nodes with controller classes
        nodes = [
            {
                'id': '1',
                'name': 'UserController',
                'primaryLabel': 'JavaClassEntity',
                'properties': {}
            }
        ]

        # Call the function
        endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, relationships=[])

        # Assert no results because it's not a controller type
        self.assertEqual(len(api_controllers), 0)

        # Now test with a proper controller
        nodes = [
            {
                'id': '1',
                'name': 'UserController',
                'primaryLabel': 'RestController',
                'properties': {}
            }
        ]

        # Call the function
        endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, relationships=[])

        # Assert results
        self.assertEqual(len(api_controllers), 1)
        self.assertEqual(api_controllers[0]['name'], 'UserController')

    def test_find_explicit_endpoints(self):
        """Test finding explicit Endpoint nodes"""
        # Mock nodes with explicit Endpoint type
        nodes = [
            {
                'id': '1',
                'name': 'GET /api/users/{id}',
                'primaryLabel': 'Endpoint',
                'properties': {
                    'path': '/api/users/{id}',
                    'httpVerb': 'GET'
                }
            }
        ]

        # Call the function
        endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, relationships=[])

        # Assert results
        self.assertEqual(len(endpoint_nodes), 1)
        self.assertEqual(endpoint_nodes[0]['http_verb'], 'GET')
        self.assertEqual(endpoint_nodes[0]['path'], '/api/users/{id}')

    def test_find_endpoint_dependencies(self):
        """Test finding dependencies between endpoints"""
        # Mock nodes
        nodes = [
            {
                'id': '1',
                'name': 'UsersEndpoint',
                'primaryLabel': 'Endpoint',
                'properties': {}
            },
            {
                'id': '2',
                'name': 'OrdersEndpoint',
                'primaryLabel': 'Endpoint',
                'properties': {}
            }
        ]

        # Mock relationships with INVOKES_ENDPOINT
        relationships = [
            {
                'startId': '1',
                'endId': '2',
                'type': 'INVOKES_ENDPOINT'
            }
        ]

        # Call the function
        endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, relationships)

        # Assert results
        self.assertEqual(len(endpoint_dependencies), 1)
        self.assertEqual(endpoint_dependencies[0]['source'], 'UsersEndpoint')
        self.assertEqual(endpoint_dependencies[0]['target'], 'OrdersEndpoint')


if __name__ == '__main__':
    unittest.main()
