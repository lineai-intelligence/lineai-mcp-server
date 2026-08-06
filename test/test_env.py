"""
Test environment utilities for unittest tests.

Provides helpers to set up a clean test environment without exposing
sensitive information in test files.
"""
import os
import importlib
from contextlib import contextmanager
import unittest
import sys

# Default test environment variables that don't expose real credentials
DEFAULT_TEST_ENV = {
    "LINEAI_TEST_MODE": "true",
    "LINEAI_SERVER_HOST": "https://example.lineai.test",
    "LINEAI_USERNAME": "test_user",
    "LINEAI_PASSWORD": "test_password",
    "LINEAI_WORKSPACE_NAME": "test_workspace",
    "LINEAI_TOKEN_CACHE_TTL": "60",  # Short cache for tests
    "LINEAI_METHOD_CACHE_TTL": "60",
    "LINEAI_IMPACT_CACHE_TTL": "60"
}

# Apply test environment variables by default for VSCode test discovery
for key, value in DEFAULT_TEST_ENV.items():
    if key not in os.environ:
        os.environ[key] = value


@contextmanager
def test_environment(custom_env=None):
    """
    Set up a clean test environment with safe defaults.

    Args:
        custom_env (dict, optional): Custom environment variables to set

    Yields:
        None: Just the setup context

    Example:
        with test_environment({'CUSTOM_VAR': 'value'}):
            # Run tests in clean environment
    """
    # Store original environment
    original_env = {}
    env_vars = list(DEFAULT_TEST_ENV.keys())

    if custom_env:
        env_vars.extend(custom_env.keys())

    # Save original environment variables
    for var in env_vars:
        original_env[var] = os.environ.get(var)

    try:
        # Set default test environment
        for key, value in DEFAULT_TEST_ENV.items():
            os.environ[key] = value

        # Override with custom values if provided
        if custom_env:
            for key, value in custom_env.items():
                os.environ[key] = value

        # Reload modules that may have cached environment variables
        try:
            import lineai_mcp_server.utils
            importlib.reload(lineai_mcp_server.utils)

            import lineai_mcp_server.handlers
            importlib.reload(lineai_mcp_server.handlers)
        except ImportError:
            # Handle import errors during test discovery
            sys.stderr.write("Warning: Could not import/reload modules for testing. This is normal during test discovery.\n")

        yield

    finally:
        # Restore original environment
        for var, value in original_env.items():
            if value is None:
                if var in os.environ:
                    del os.environ[var]
            else:
                os.environ[var] = value


class TestCase(unittest.TestCase):
    """
    Base test case with clean environment setup.

    Provides a clean test environment with safe default values
    and helper methods for mocking integration points.
    """

    def setUp(self):
        """Set up test environment with safe defaults."""
        # Set up clean test environment
        self.env_patcher = test_environment()
        self.env_patcher.__enter__()
        super().setUp()  # Call parent setUp to ensure proper unittest setup

    def tearDown(self):
        """Restore original environment."""
        super().tearDown()  # Call parent tearDown first
        self.env_patcher.__exit__(None, None, None)
