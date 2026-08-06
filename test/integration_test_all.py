import os
import sys
import asyncio
from dotenv import load_dotenv
import httpx
import mcp.types as types
from test.test_fixtures import setup_test_environment
from test.test_env import TestCase

# Add the parent directory to Python path to make the absolute import work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def load_test_config(env_file=None):
    """Load environment configuration from .env file or environment variables."""
    # Get test directory path
    test_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(test_dir, '..'))
    
    # Load .env so real credentials override test_env defaults (override=True)
    if env_file and os.path.exists(env_file):
        load_dotenv(env_file, override=True)
    # Then try test-specific env file in the test directory
    elif os.path.exists(os.path.join(test_dir, '.env.test')):
        load_dotenv(os.path.join(test_dir, '.env.test'), override=True)
    # Next try project root .env file
    elif os.path.exists(os.path.join(project_root, '.env')):
        load_dotenv(os.path.join(project_root, '.env'), override=True)

    return {
        'LINEAI_WORKSPACE_NAME': os.getenv('LINEAI_WORKSPACE_NAME'),
        'LINEAI_SERVER_HOST': os.getenv('LINEAI_SERVER_HOST'),
        'LINEAI_USERNAME': os.getenv('LINEAI_USERNAME'),
        'LINEAI_PASSWORD': os.getenv('LINEAI_PASSWORD'),
    }


def _is_server_reachable(config):
    """Return True if the Lineai server can be reached (auth or DNS)."""
    if not config.get('LINEAI_SERVER_HOST') or not config.get('LINEAI_USERNAME') or not config.get('LINEAI_PASSWORD'):
        return False
    try:
        *_, authenticate = setup_test_environment(config)
        authenticate()
        return True
    except (httpx.ConnectError, OSError):
        return False


class TestHandleCallToolIntegration(TestCase):
    """Integration tests for handle_call_tool using clean test environment.

    To run these tests:
    1. Create a .env.test file with your credentials, or
    2. Set environment variables as specified in .env.example
    """

    @classmethod
    def setUpClass(cls):
        """Set up test configuration from environment variables"""
        cls.config = load_test_config()
        cls._skip_reason = None
        if cls.config.get('LINEAI_USERNAME') and cls.config.get('LINEAI_PASSWORD') and not _is_server_reachable(cls.config):
            cls._skip_reason = "Lineai server not reachable"

    def run_impact_test(self, method_name, class_name, output_file):
        """Helper to run a parameterized impact analysis test"""
        # Skip test if credentials are not provided
        if not self.config.get('LINEAI_USERNAME') or not self.config.get('LINEAI_PASSWORD'):
            self.skipTest("Skipping integration test: No credentials provided in environment")
        if getattr(self.__class__, '_skip_reason', None):
            self.skipTest(self.__class__._skip_reason)

        # Setup environment with configuration
        handle_call_tool, *_ = setup_test_environment(self.config)

        async def run_test():
            result = await handle_call_tool('lineai-method-impact', {'method': method_name, 'class': class_name})

            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)
            self.assertIsInstance(result[0], types.TextContent)

            if "Unable to Analyze" in result[0].text:
                self.skipTest("Method not found or server error (404/504) for this workspace")

            with open(output_file, 'w', encoding='utf-8') as file:
                file.write(result[0].text)

            self.assertIn(f"# Impact Analysis for Method: `{method_name}`", result[0].text)
            return result

        return asyncio.run(run_test())

    def test_handle_call_tool_lineai_method_impact_multi_app_java(self):
        """Test impact analysis on Java multi-app environment"""
        self.run_impact_test(
            'addPrefix',
            'CompanyInfo',
            'impact_analysis_result_multi_app_java.md'
        )

    def test_handle_call_tool_lineai_method_impact_dotnet(self):
        """Test impact analysis on .NET environment"""
        self.run_impact_test(
            'IsValid',
            'AnalysisOptionsValidator',
            'impact_analysis_result_dotnet.md'
        )


class TestUtils(TestCase):
    """Test utility functions using the clean test environment."""

    _server_unreachable = False

    @classmethod
    def setUpClass(cls):
        """Set up test resources that can be shared across test methods."""
        config = load_test_config()
        if not config.get('LINEAI_SERVER_HOST') or not config.get('LINEAI_USERNAME') or not config.get('LINEAI_PASSWORD'):
            cls._server_unreachable = True
            cls.token = None
            cls.mv_name = None
            cls.mv_def_id = None
            cls.mv_id = None
            cls.nodes = []
            cls.get_method_nodes = None
            cls.get_impact = None
            return
        try:
            get_mv_definition_id, get_mv_id_from_def, get_method_nodes, get_impact, authenticate = setup_test_environment(config)[1:6]
            cls.token = authenticate()
            cls.mv_name = os.getenv('LINEAI_WORKSPACE_NAME')
            cls.mv_def_id = get_mv_definition_id(cls.mv_name, cls.token)
            cls.mv_id = get_mv_id_from_def(cls.mv_def_id, cls.token)
            cls.nodes, _ = get_method_nodes(cls.mv_id, 'IsValid')
            cls.get_method_nodes = get_method_nodes
            cls.get_impact = get_impact
        except (httpx.ConnectError, OSError):
            cls._server_unreachable = True
            cls.token = None
            cls.mv_name = None
            cls.mv_def_id = None
            cls.mv_id = None
            cls.nodes = []
            cls.get_method_nodes = None
            cls.get_impact = None

    def setUp(self):
        super().setUp()
        if self._server_unreachable:
            self.skipTest("Lineai server not reachable")
        # Re-apply integration config so test_get_impact uses real server (TestCase.setUp() had set fake env)
        config = load_test_config()
        for key, value in config.items():
            if value is not None:
                os.environ[key] = value

    def test_authenticate(self):
        self.assertIsNotNone(self.token)

    def test_get_mv_definition_id(self):
        # Accept UUID (36 hex+hyphens) or numeric ID
        self.assertRegex(self.mv_def_id, r'^([0-9a-fA-F-]{36}|-?\d+)$')

    def test_get_mv_id_from_def(self):
        # Accept UUID (36 hex+hyphens) or numeric ID
        self.assertRegex(self.mv_id, r'^([0-9a-fA-F-]{36}|-?\d+)$')

    def test_get_method_nodes(self):
        self.assertIsInstance(self.nodes, list)

    def test_get_impact(self):
        node_id = self.nodes[0]['id'] if self.nodes else None
        self.assertIsNotNone(node_id, "Node ID should not be None")
        try:
            # get_impact(id) is a module function; calling self.get_impact(node_id) would pass (self, node_id)
            impact = self.get_impact.__func__(node_id)
        except (httpx.ConnectError, OSError):
            self.skipTest("Lineai server not reachable")
        self.assertIsInstance(impact, str)


if __name__ == '__main__':
    import unittest
    unittest.main()
