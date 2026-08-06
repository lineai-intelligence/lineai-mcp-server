# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Entry point script for the Lineai MCP Server.

This script initializes and runs the Model Context Protocol (MCP) server
with debugging capabilities. It sets up debugging via debugpy,
configures the Python path, and handles any exceptions that may occur
during server execution.
"""

import os
import sys
import asyncio
from lineai_mcp_server.server import main
import debugpy

# Add the src directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    print("Starting MCP Server...", file=sys.stderr)

    # Set up debugging
    try:
        debugpy.listen(("127.0.0.1", 5679))
        print("Debugpy listening on port 5679", file=sys.stderr)
    except Exception as e:
        print(f"Debug setup failed: {e}", file=sys.stderr)

    # Run the server
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        print(f"Error in MCP server: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
