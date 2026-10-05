import os
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
        self.assertEqual(url_arg, 'https://example.lineai.test/api/authenticate')

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
        mock_response.status_code = 200
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

        # Verify results are cached and returned (cache key includes the mv id)
        self.assertIn('node-123:None', utils._impact_cache)
        cached_impact, expiry = utils._impact_cache['node-123:None']

        # Verify the impact data is properly stripped
        impact_data = json.loads(impact)
        self.assertNotIn('agentIds', impact_data['data']['nodes'][0]['properties'])
        self.assertNotIn('sourceScanContextIds', impact_data['data']['nodes'][0]['properties'])
        self.assertNotIn('isScanRoot', impact_data['data']['nodes'][0]['properties'])
        self.assertIn('keep', impact_data['data']['nodes'][0]['properties'])

        # Verify expiry time
        self.assertEqual(expiry, now + timedelta(seconds=utils.IMPACT_CACHE_TTL))

        # Verify logging message
        self.assertIn(f"Impact cached for node-123:None with TTL {utils.IMPACT_CACHE_TTL}s",
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
        utils._impact_cache['node-123:None'] = (cached_data, now + timedelta(seconds=300))

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
        utils._impact_cache['node-123:None'] = (cached_data, now - timedelta(seconds=60))

        # Set current time
        mock_datetime.now.return_value = now

        # Set up mock token
        mock_authenticate.return_value = 'test_token'

        # Set up mock response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
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


def _mock_response(status_code=200, json_data=None, text=None):
    """Build a mock httpx response with a real integer status code."""
    response = mock.MagicMock()
    response.status_code = status_code
    if json_data is not None:
        response.json.return_value = json_data
        response.text = json.dumps(json_data)
    if text is not None:
        response.text = text
    return response


class TestTokenExpiresIn(TestCase):
    """authenticate() must honor a server-provided expires_in with a margin."""

    def setUp(self):
        super().setUp()
        utils._cached_token = None
        utils._token_expiry = None

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_expires_in_applies_margin(self, mock_datetime, mock_post):
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now
        # DEFAULT_TEST_ENV pins LINEAI_TOKEN_CACHE_TTL=60, so use a shorter
        # expires_in to observe the margin below the cap.
        mock_post.return_value = _mock_response(
            200, {'access_token': 'tok', 'token_type': 'bearer', 'expires_in': 50})

        token = utils.authenticate()

        self.assertEqual(token, 'tok')
        self.assertEqual(utils._token_expiry,
                         now + timedelta(seconds=50 - utils.TOKEN_EXPIRY_MARGIN))

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_expires_in_capped_at_configured_ttl(self, mock_datetime, mock_post):
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now
        mock_post.return_value = _mock_response(
            200, {'access_token': 'tok', 'expires_in': 999999})

        utils.authenticate()

        self.assertEqual(utils._token_expiry,
                         now + timedelta(seconds=utils.TOKEN_CACHE_TTL))

    @mock.patch('lineai_mcp_server.utils._client.post')
    @mock.patch('lineai_mcp_server.utils.datetime')
    def test_tiny_expires_in_floors_at_zero(self, mock_datetime, mock_post):
        now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = now
        mock_post.return_value = _mock_response(
            200, {'access_token': 'tok', 'expires_in': 10})

        utils.authenticate()

        self.assertEqual(utils._token_expiry, now)  # 10s - 30s margin floors at 0


class TestAuthedRequest(TestCase):
    """_authed_request: 401 invalidate-retry and transport error mapping."""

    @mock.patch('lineai_mcp_server.utils.invalidate_token')
    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    def test_single_401_retries_once_with_fresh_token(self, mock_get, mock_auth, mock_invalidate):
        mock_auth.side_effect = ['stale-token', 'fresh-token']
        ok = _mock_response(200, {'data': 'fine'})
        mock_get.side_effect = [_mock_response(401), ok]

        response = utils._authed_request('GET', 'https://example.lineai.test/api/x')

        self.assertIs(response, ok)
        self.assertEqual(mock_get.call_count, 2)
        mock_invalidate.assert_called_once()
        second_headers = mock_get.call_args_list[1].kwargs['headers']
        self.assertEqual(second_headers['Authorization'], 'Bearer fresh-token')

    @mock.patch('lineai_mcp_server.utils.invalidate_token')
    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    def test_persistent_401_raises_auth_without_looping(self, mock_get, mock_auth, mock_invalidate):
        mock_auth.return_value = 'token'
        mock_get.side_effect = [_mock_response(401), _mock_response(401)]

        with self.assertRaises(utils.LineaiApiError) as ctx:
            utils._authed_request('GET', 'https://example.lineai.test/api/x')

        self.assertEqual(ctx.exception.kind, 'auth')
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(mock_get.call_count, 2)  # exactly one retry, no loop

    @mock.patch('lineai_mcp_server.utils.invalidate_token')
    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    def test_single_403_retries_once_with_fresh_token(self, mock_get, mock_auth, mock_invalidate):
        # The Lineai server answers bad/expired tokens with 403, not 401
        # (verified live during LIN-699 e2e) — the retry must cover it too.
        mock_auth.side_effect = ['stale-token', 'fresh-token']
        ok = _mock_response(200, {'data': 'fine'})
        mock_get.side_effect = [_mock_response(403), ok]

        response = utils._authed_request('GET', 'https://example.lineai.test/api/x')

        self.assertIs(response, ok)
        self.assertEqual(mock_get.call_count, 2)
        mock_invalidate.assert_called_once()
        second_headers = mock_get.call_args_list[1].kwargs['headers']
        self.assertEqual(second_headers['Authorization'], 'Bearer fresh-token')

    @mock.patch('lineai_mcp_server.utils.invalidate_token')
    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    def test_persistent_403_raises_auth_without_looping(self, mock_get, mock_auth, mock_invalidate):
        mock_auth.return_value = 'token'
        mock_get.side_effect = [_mock_response(403), _mock_response(403)]

        with self.assertRaises(utils.LineaiApiError) as ctx:
            utils._authed_request('GET', 'https://example.lineai.test/api/x')

        self.assertEqual(ctx.exception.kind, 'auth')
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(mock_get.call_count, 2)  # exactly one retry, no loop

    @mock.patch('lineai_mcp_server.utils.authenticate')
    @mock.patch('lineai_mcp_server.utils._client.get')
    def test_timeout_maps_to_lineai_api_error(self, mock_get, mock_auth):
        mock_auth.return_value = 'token'
        mock_get.side_effect = utils.httpx.ConnectTimeout('boom')

        with self.assertRaises(utils.LineaiApiError) as ctx:
            utils._authed_request('GET', 'https://example.lineai.test/api/x')

        self.assertEqual(ctx.exception.kind, 'timeout')


class TestResolveMvId(TestCase):
    """resolve_mv_id precedence: explicit id -> workspace arg -> env -> server default."""

    @mock.patch('lineai_mcp_server.utils.get_default_mv_id')
    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_explicit_snake_case_id_wins(self, mock_get_mv_id, mock_default):
        result = utils.resolve_mv_id({'materialized_view_id': 'mv-explicit', 'workspace': 'ignored'})
        self.assertEqual(result, 'mv-explicit')
        mock_get_mv_id.assert_not_called()
        mock_default.assert_not_called()

    @mock.patch('lineai_mcp_server.utils.get_default_mv_id')
    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_explicit_camel_case_id_wins(self, mock_get_mv_id, mock_default):
        result = utils.resolve_mv_id({'materializedViewId': 'mv-camel'})
        self.assertEqual(result, 'mv-camel')
        mock_get_mv_id.assert_not_called()
        mock_default.assert_not_called()

    @mock.patch('lineai_mcp_server.utils.get_default_mv_id')
    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_workspace_argument_overrides_env(self, mock_get_mv_id, mock_default):
        mock_get_mv_id.return_value = 'mv-from-arg'
        result = utils.resolve_mv_id({'workspace': 'Arg Workspace'})
        self.assertEqual(result, 'mv-from-arg')
        mock_get_mv_id.assert_called_once_with('Arg Workspace')
        mock_default.assert_not_called()

    @mock.patch('lineai_mcp_server.utils.get_default_mv_id')
    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_env_workspace_used_when_no_arguments(self, mock_get_mv_id, mock_default):
        mock_get_mv_id.return_value = 'mv-from-env'
        result = utils.resolve_mv_id(None)  # DEFAULT_TEST_ENV sets LINEAI_WORKSPACE_NAME
        self.assertEqual(result, 'mv-from-env')
        mock_get_mv_id.assert_called_once_with('test_workspace')
        mock_default.assert_not_called()

    @mock.patch('lineai_mcp_server.utils.get_default_mv_id')
    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_server_default_when_nothing_specified(self, mock_get_mv_id, mock_default):
        del os.environ['LINEAI_WORKSPACE_NAME']
        mock_default.return_value = 'mv-default'
        result = utils.resolve_mv_id({})
        self.assertEqual(result, 'mv-default')
        mock_default.assert_called_once()
        mock_get_mv_id.assert_not_called()

    @mock.patch('lineai_mcp_server.utils.get_mv_id')
    def test_name_resolution_is_cached(self, mock_get_mv_id):
        mock_get_mv_id.return_value = 'mv-cached'
        first = utils.resolve_mv_id({'workspace': 'WS'})
        second = utils.resolve_mv_id({'workspace': 'WS'})
        self.assertEqual(first, 'mv-cached')
        self.assertEqual(second, 'mv-cached')
        mock_get_mv_id.assert_called_once()


class TestDefaultMvId(TestCase):
    """GET /api/materialized-view/default returns the id as a bare data value."""

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_bare_id_payload(self, mock_request):
        mock_request.return_value = _mock_response(200, {'status': 'success', 'data': 123456789})
        self.assertEqual(utils.get_default_mv_id(), '123456789')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_404_maps_to_mv_no_default(self, mock_request):
        mock_request.return_value = _mock_response(404, {'status': 'error'})
        with self.assertRaises(utils.LineaiApiError) as ctx:
            utils.get_default_mv_id()
        self.assertEqual(ctx.exception.kind, 'mv_no_default')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_mv_name_404_maps_to_mv_not_found(self, mock_request):
        mock_request.return_value = _mock_response(404, {'status': 'error'})
        with self.assertRaises(utils.LineaiApiError) as ctx:
            utils.get_mv_definition_id('nope')
        self.assertEqual(ctx.exception.kind, 'mv_not_found')


class TestGetImpactViewId(TestCase):
    """get_impact sends viewId and keys its cache on id:mv_id."""

    def setUp(self):
        super().setUp()
        utils._impact_cache = {}

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_view_id_param_and_cache_key(self, mock_request):
        mock_request.return_value = _mock_response(200, {'data': {'nodes': []}})

        utils.get_impact('node-9', 'mv-7')

        _args, kwargs = mock_request.call_args
        self.assertEqual(kwargs['params'], {'viewId': 'mv-7'})
        self.assertIn('node-9:mv-7', utils._impact_cache)

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_no_view_id_sends_no_params(self, mock_request):
        mock_request.return_value = _mock_response(200, {'data': {'nodes': []}})

        utils.get_impact('node-9')

        _args, kwargs = mock_request.call_args
        self.assertIsNone(kwargs['params'])
        self.assertIn('node-9:None', utils._impact_cache)


class TestGetMethodNodesEmpty(TestCase):
    """Empty and malformed 200s are empty results — and are not cached."""

    def setUp(self):
        super().setUp()
        utils._method_nodes_cache = {}

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_empty_200_is_success_and_not_cached(self, mock_request):
        mock_request.return_value = _mock_response(200, {'data': []})

        nodes, err = utils.get_method_nodes('mv-1', 'ghost')

        self.assertEqual(nodes, [])
        self.assertIsNone(err)
        self.assertEqual(utils._method_nodes_cache, {})

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_missing_data_key_is_empty_success(self, mock_request):
        mock_request.return_value = _mock_response(200, {'status': 'success'})

        nodes, err = utils.get_method_nodes('mv-1', 'ghost')

        self.assertEqual(nodes, [])
        self.assertIsNone(err)


class TestSearchDatabaseEntityErrors(TestCase):
    """search_database_entity returns (results, error_kind) instead of swallowing."""

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_success_returns_data_and_none(self, mock_request):
        mock_request.return_value = _mock_response(200, {'data': [{'id': 't1', 'name': 'ORDERS'}]})
        results, err = utils.search_database_entity('table', 'ORDERS', 'mv-1')
        self.assertEqual(results, [{'id': 't1', 'name': 'ORDERS'}])
        self.assertIsNone(err)
        _args, kwargs = mock_request.call_args
        self.assertEqual(kwargs['params']['materializedViewId'], 'mv-1')
        self.assertEqual(kwargs['params']['tableName'], 'ORDERS')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_404_is_not_found(self, mock_request):
        mock_request.return_value = _mock_response(404)
        results, err = utils.search_database_entity('table', 'NOPE', 'mv-1')
        self.assertEqual(results, [])
        self.assertEqual(err, 'not_found')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_504_is_gateway_timeout(self, mock_request):
        mock_request.return_value = _mock_response(504)
        results, err = utils.search_database_entity('view', 'V', 'mv-1')
        self.assertEqual(results, [])
        self.assertEqual(err, 'gateway_timeout')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_500_is_http_error(self, mock_request):
        mock_request.return_value = _mock_response(500)
        results, err = utils.search_database_entity('column', 'C', 'mv-1', 'T')
        self.assertEqual(results, [])
        self.assertEqual(err, 'http_error')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_transport_timeout_is_timeout(self, mock_request):
        mock_request.side_effect = utils.LineaiApiError('timeout', detail='slow')
        results, err = utils.search_database_entity('table', 'T', 'mv-1')
        self.assertEqual(results, [])
        self.assertEqual(err, 'timeout')

    @mock.patch('lineai_mcp_server.utils._authed_request')
    def test_auth_error_propagates(self, mock_request):
        mock_request.side_effect = utils.LineaiApiError('auth', status=401)
        with self.assertRaises(utils.LineaiApiError):
            utils.search_database_entity('table', 'T', 'mv-1')


# Real-shaped UR fixture: top-level SearchNode, properties carry the UR data,
# fuzzy is a JSON *string*, labels include the per-view "v-..." label, endpoint
# fields appear as flat "endpoint."-prefixed keys, relationship id is "null".
UR_NODE = {
    'id': 'ur-1',
    'name': '[MethodEntity] identity EQUALS com.acme|Widget|doThing()',
    'primaryLabel': 'SearchNode',
    'properties': {
        'primaryLabel': 'UnresolvedReference',
        'fuzzy': '{"shortName": "doThing"}',
        'labels': ['MethodEntity', 'v-42'],
        'queryHash': 'qh-1',
        'materializedViewId': 42,
        'id': 'ur-1',
        'name': '[MethodEntity] identity EQUALS com.acme|Widget|doThing()',
        'endpoint.httpMethod': 'GET',
        'endpoint.path': '/widgets',
    },
}

CALLER_NODE = {
    'id': 'n-1',
    'name': 'caller',
    'identity': 'com.acme|Caller|caller()',
    'primaryLabel': 'JavaMethodEntity',
    'properties': {},
}

UR_IMPACT_DATA = {
    'data': {
        'nodes': [CALLER_NODE, UR_NODE],
        'relationships': [
            {'startId': 'n-1', 'endId': 'ur-1', 'type': 'INVOKES_METHOD',
             'id': 'null', 'contains': False, 'group': False},
        ],
    }
}


class TestUnresolvedReferences(unittest.TestCase):
    """UR detection, extraction, strip-skip, and rendering."""

    def test_is_unresolved_reference_search_node_convention(self):
        self.assertTrue(utils.is_unresolved_reference(UR_NODE))

    def test_is_unresolved_reference_normalized_convention(self):
        node = {'id': 'x', 'primaryLabel': 'UnresolvedReference', 'properties': {}}
        self.assertTrue(utils.is_unresolved_reference(node))

    def test_is_unresolved_reference_regular_node(self):
        self.assertFalse(utils.is_unresolved_reference(CALLER_NODE))

    def test_extract_unresolved_references(self):
        urs = utils.extract_unresolved_references(UR_IMPACT_DATA)
        self.assertEqual(len(urs), 1)
        ur = urs[0]
        self.assertEqual(ur['sought'], '[MethodEntity] identity EQUALS com.acme|Widget|doThing()')
        self.assertEqual(ur['target_labels'], ['MethodEntity'])  # v-42 filtered out
        self.assertEqual(ur['fuzzy'], {'shortName': 'doThing'})  # JSON string parsed
        self.assertEqual(ur['endpoint'], {'httpMethod': 'GET', 'path': '/widgets'})
        self.assertEqual(ur['referenced_by'],
                         [{'name': 'caller', 'type': 'JavaMethodEntity', 'relationship': 'INVOKES_METHOD'}])

    def test_extract_tolerates_unparseable_fuzzy_and_nested_endpoint(self):
        node = json.loads(json.dumps(UR_NODE))
        node['properties']['fuzzy'] = 'not json'
        del node['properties']['endpoint.httpMethod']
        del node['properties']['endpoint.path']
        node['properties']['endpoint'] = {'httpMethod': 'POST', 'path': '/x', 'host': None}
        urs = utils.extract_unresolved_references({'data': {'nodes': [node], 'relationships': []}})
        self.assertEqual(urs[0]['fuzzy'], 'not json')
        self.assertEqual(urs[0]['endpoint'], {'httpMethod': 'POST', 'path': '/x'})
        self.assertEqual(urs[0]['referenced_by'], [])

    def test_extract_returns_empty_without_urs(self):
        data = {'data': {'nodes': [CALLER_NODE], 'relationships': []}}
        self.assertEqual(utils.extract_unresolved_references(data), [])

    def test_strip_unused_properties_skips_ur_nodes(self):
        response = Mock()
        response.text = json.dumps(UR_IMPACT_DATA)
        stripped = json.loads(utils.strip_unused_properties(response))
        nodes = {n['id']: n for n in stripped['data']['nodes']}
        # UR node keeps its report-relevant properties
        self.assertEqual(nodes['ur-1']['properties']['id'], 'ur-1')
        self.assertIn('name', nodes['ur-1']['properties'])
        self.assertIn('fuzzy', nodes['ur-1']['properties'])

    def test_format_section_renders_table(self):
        urs = utils.extract_unresolved_references(UR_IMPACT_DATA)
        section = utils.format_unresolved_references_section(urs)
        self.assertIn('## Unresolved References (dependency indicators)', section)
        self.assertIn('GET /widgets', section)
        self.assertIn('MethodEntity', section)
        self.assertIn('INVOKES_METHOD', section)
        # pipes in the synthetic name must be escaped for the markdown table
        self.assertIn('com.acme\\|Widget\\|doThing()', section)

    def test_format_section_empty_when_none(self):
        self.assertEqual(utils.format_unresolved_references_section([]), "")


if __name__ == '__main__':
    unittest.main()
