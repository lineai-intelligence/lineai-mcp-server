"""
Test the test environment setup itself.
"""
import unittest
import os
from test.test_env import TestCase


class TestEnvironmentSetup(TestCase):
    """Test that the test environment setup is working correctly."""

    def test_environment_variables_set(self):
        """Test that environment variables are set correctly."""
        self.assertEqual(os.environ.get('LINEAI_SERVER_HOST'), 'https://example.lineai.test')
        self.assertEqual(os.environ.get('LINEAI_TEST_MODE'), 'true')

    def test_basic_unittest_functions(self):
        """Test that basic unittest functions work."""
        self.assertTrue(True)
        self.assertEqual(1, 1)


if __name__ == '__main__':
    unittest.main()
