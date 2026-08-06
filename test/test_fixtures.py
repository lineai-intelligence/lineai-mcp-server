import os
import importlib

# Base test environment setup
os.environ['LINEAI_TEST_MODE'] = 'true'


def setup_test_environment(env_vars):
    """Set environment variables and reload affected modules"""
    # Set environment variables
    for key, value in env_vars.items():
        os.environ[key] = value

    # Override LINEAI_SERVER_HOST only when not provided (unit tests use testserver)
    if not env_vars.get('LINEAI_SERVER_HOST'):
        os.environ['LINEAI_SERVER_HOST'] = 'http://testserver'

    # Reload the utils module to ensure it picks up the updated environment variables
    import lineai_mcp_server.utils
    importlib.reload(lineai_mcp_server.utils)

    # Graph MCP client reads LINEAI_SERVER_HOST at import/call time via utils.authenticate
    import lineai_mcp_server.graph_client
    importlib.reload(lineai_mcp_server.graph_client)

    import lineai_mcp_server.handlers.graph_tools
    importlib.reload(lineai_mcp_server.handlers.graph_tools)

    # Reinitialize the HTTP client in utils to use the updated environment variables
    lineai_mcp_server.utils._client = lineai_mcp_server.utils.httpx.Client(
        timeout=lineai_mcp_server.utils.httpx.Timeout(
            lineai_mcp_server.utils.REQUEST_TIMEOUT,
            connect=lineai_mcp_server.utils.CONNECT_TIMEOUT
        ),
        limits=lineai_mcp_server.utils.httpx.Limits(
            max_keepalive_connections=20,
            max_connections=30
        ),
        transport=lineai_mcp_server.utils.httpx.HTTPTransport(retries=3)
    )

    # Only import handlers after environment is properly configured
    import lineai_mcp_server.handlers
    importlib.reload(lineai_mcp_server.handlers)

    # Return the imported modules for convenience
    from lineai_mcp_server.handlers import handle_call_tool
    from lineai_mcp_server.utils import (
        get_mv_definition_id,
        get_mv_id_from_def,
        get_method_nodes,
        get_impact,
        authenticate
    )

    return handle_call_tool, get_mv_definition_id, get_mv_id_from_def, get_method_nodes, get_impact, authenticate
