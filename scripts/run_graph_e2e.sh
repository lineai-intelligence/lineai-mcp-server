#!/usr/bin/env bash
# Run MCP graph tool E2E tests against LINEAI_SERVER_HOST (see test/integration_test_graph.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec uv run python -m unittest test.integration_test_graph -v "$@"
