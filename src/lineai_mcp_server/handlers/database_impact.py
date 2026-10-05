# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Handler for the lineai-database-impact tool.
"""

import json
import os
import sys
import time
import mcp.types as types
from .common import error_result, write_json_to_file, log_timing, DEBUG_MODE, LOGS_DIR
from ..utils import (
    LineaiApiError,
    extract_unresolved_references,
    format_unresolved_references_section,
    generate_combined_database_report,
    get_impact,
    process_database_entity_impact,
    resolve_mv_id,
    search_database_entity,
)


def handle_database_impact(arguments: dict | None) -> list[types.TextContent] | types.CallToolResult:
    """Handle the database-impact tool for database entity analysis"""
    if not arguments:
        sys.stderr.write("Missing arguments\n")
        raise ValueError("Missing arguments")

    entity_type = arguments.get("entity_type")
    name = arguments.get("name")
    table_or_view = arguments.get("table_or_view")

    if not entity_type or not name:
        sys.stderr.write("Entity type and name must be provided\n")
        raise ValueError("Entity type and name must be provided")

    if entity_type not in ["column", "table", "view"]:
        sys.stderr.write(f"Invalid entity type: {entity_type}. Must be column, table, or view.\n")
        raise ValueError(f"Invalid entity type: {entity_type}")

    # Verify table_or_view is provided for columns
    if entity_type == "column" and not table_or_view:
        sys.stderr.write("Table or view name must be provided for column searches\n")
        raise ValueError("Table or view name must be provided for column searches")

    # Resolve the materialized view to search in
    mv_id = resolve_mv_id(arguments)

    # Search for the database entity
    start_time = time.time()
    search_results, search_error = search_database_entity(entity_type, name, mv_id, table_or_view)
    end_time = time.time()
    duration = end_time - start_time
    log_timing(f"search_database_entity for {entity_type} '{name}'", duration)

    table_view_text = f" in {table_or_view}" if table_or_view else ""

    if search_error is not None and search_error != "not_found":
        # Real failures are rendered honestly instead of masquerading as "no results"
        if search_error == "timeout":
            detail = (f"The database entity search timed out after "
                      f"{os.getenv('LINEAI_REQUEST_TIMEOUT', '120.0')}s (client timeout).")
        elif search_error == "gateway_timeout":
            detail = "The Lineai API returned **504 Gateway Timeout** while searching (upstream did not respond in time)."
        else:
            detail = "The Lineai API returned an HTTP error while searching (see MCP stderr logs for the exact status)."
        return error_result(f"""# Unable to Analyze {entity_type.capitalize()}: `{name}`

## Error
{detail}

This is an infrastructure problem — it says nothing about whether the {entity_type} exists.

## Recommendations:
1. Check MCP stderr logs for the exact HTTP status and message
2. Retry; if the failure persists, check the health of the Lineai server
3. Server: {os.getenv('LINEAI_SERVER_HOST')}
""")

    if not search_results:
        # 404 and an empty 200 are the same honest answer: the view resolved,
        # nothing in it matches. Not an index or infrastructure problem.
        return error_result(
            f"# No {entity_type}s found matching '{name}'{table_view_text}\n\n"
            f"The materialized view resolved successfully (view `{mv_id}`), but no database "
            f"{entity_type}s matched the name '{name}'"
            + (f" in {table_or_view}" if table_or_view else "")
            + ". This is a definitive no-match result, not an index or infrastructure problem.\n\n"
            "## Recommendations:\n"
            "1. Check the spelling of the entity name.\n"
            "2. Confirm the `workspace` / `materialized_view_id` arguments (or `LINEAI_WORKSPACE_NAME`) "
            "point at the workspace that contains this database.\n"
        )

    # Process each entity and get its impact
    all_impacts = []
    impact_failures = []
    all_unresolved = []
    seen_unresolved_ids = set()
    for entity in search_results[:5]:  # Limit to 5 to avoid excessive processing
        entity_id = entity.get("id")
        entity_name = entity.get("name")
        entity_schema = entity.get("schema", "Unknown")

        try:
            start_time = time.time()
            impact = get_impact(entity_id, mv_id)
            end_time = time.time()
            duration = end_time - start_time
            log_timing(f"get_impact for {entity_type} '{entity_name}'", duration)
        except LineaiApiError as e:
            # Collected into a rendered report section instead of a stderr-only swallow
            sys.stderr.write(f"Error getting impact for {entity_type} '{entity_name}': {e}\n")
            impact_failures.append({"name": entity_name, "error": e})
            continue

        try:
            if DEBUG_MODE:
                write_json_to_file(os.path.join(LOGS_DIR, f"impact_data_{entity_type}_{entity_name}.json"), json.loads(impact))
            impact_data = json.loads(impact)
            impact_summary = process_database_entity_impact(
                impact_data, entity_type, entity_name, entity_schema
            )
            all_impacts.append(impact_summary)
            for unresolved in extract_unresolved_references(impact_data):
                if unresolved.get("id") not in seen_unresolved_ids:
                    seen_unresolved_ids.add(unresolved.get("id"))
                    all_unresolved.append(unresolved)
        except Exception as e:
            sys.stderr.write(f"Error processing impact for {entity_type} '{entity_name}': {str(e)}\n")
            impact_failures.append({"name": entity_name, "error": e})

    # Combine all impacts into a single report
    combined_report = generate_combined_database_report(
        entity_type, name, table_or_view, search_results, all_impacts
    )

    # Unresolved references are dependency indicators; section omitted when none
    unresolved_section = format_unresolved_references_section(all_unresolved)
    if unresolved_section:
        combined_report += f"\n{unresolved_section}"

    if impact_failures:
        combined_report += "\n## Impact retrieval failures\n\n"
        combined_report += (
            f"Impact analysis could not be retrieved for {len(impact_failures)} of the matched "
            "entities. The sections above are complete for the remaining entities, but the "
            "overall picture may be incomplete.\n\n"
        )
        combined_report += "| Entity | Error |\n|--------|-------|\n"
        for failure in impact_failures:
            err = failure["error"]
            kind = getattr(err, "kind", None) or type(err).__name__
            status = getattr(err, "status", None)
            status_text = f", HTTP {status}" if status else ""
            combined_report += f"| `{failure['name']}` | {kind}{status_text} |\n"

    return [
        types.TextContent(
            type="text",
            text=combined_report
        )
    ]
