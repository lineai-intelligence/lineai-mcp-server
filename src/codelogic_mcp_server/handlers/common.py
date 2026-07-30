# Copyright (C) 2025 CodeLogic Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Common utilities and shared functions for CodeLogic MCP handlers.
"""

import json
import os
import sys
import tempfile
from datetime import datetime


DEBUG_MODE = os.getenv("CODELOGIC_DEBUG_MODE", "false").lower() == "true"

# Use a user-specific temporary directory for logs to avoid permission issues when running via uvx
# Only create the directory when debug mode is enabled
LOGS_DIR = os.path.join(tempfile.gettempdir(), "codelogic-mcp-server")
if DEBUG_MODE:
    os.makedirs(LOGS_DIR, exist_ok=True)


def ensure_logs_dir():
    """Ensure the logs directory exists when needed for debug mode."""
    if DEBUG_MODE:
        os.makedirs(LOGS_DIR, exist_ok=True)


def get_workspace_name():
    """Get the CodeLogic workspace name from environment variable with fallback."""
    workspace_name = os.getenv("CODELOGIC_WORKSPACE_NAME")
    if not workspace_name:
        sys.stderr.write("Warning: CODELOGIC_WORKSPACE_NAME environment variable not set. Using default workspace.\n")
        workspace_name = "default-workspace"
    return workspace_name


def write_json_to_file(file_path, data):
    """Write JSON data to a file with improved formatting."""
    ensure_logs_dir()
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, separators=(", ", ": "), ensure_ascii=False, sort_keys=True)


def log_timing(operation, duration, details=""):
    """Log timing information for operations."""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ensure_logs_dir()
        with open(os.path.join(LOGS_DIR, "timing_log.txt"), "a") as log_file:
            log_file.write(f"{timestamp} - {operation} took {duration:.4f} seconds {details}\n")
