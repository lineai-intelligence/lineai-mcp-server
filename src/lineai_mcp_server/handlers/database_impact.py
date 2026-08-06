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
from .common import get_workspace_name, write_json_to_file, log_timing, DEBUG_MODE, LOGS_DIR
from ..utils import search_database_entity, get_impact, process_database_entity_impact, generate_combined_database_report


async def handle_database_impact(arguments: dict | None) -> list[types.TextContent]:
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

    # Get workspace name from environment variable
    workspace_name = get_workspace_name()
    
    # Search for the database entity
    start_time = time.time()
    search_results = await search_database_entity(entity_type, name, table_or_view)
    end_time = time.time()
    duration = end_time - start_time
    log_timing(f"search_database_entity for {entity_type} '{name}'", duration)

    if not search_results:
        table_view_text = f" in {table_or_view}" if table_or_view else ""
        return [
            types.TextContent(
                type="text",
                text=f"# No {entity_type}s found matching '{name}'{table_view_text}\n\nNo database {entity_type}s were found matching the name '{name}'"
                     + (f" in {table_or_view}" if table_or_view else "") + "."
            )
        ]

    # Process each entity and get its impact
    all_impacts = []
    for entity in search_results[:5]:  # Limit to 5 to avoid excessive processing
        entity_id = entity.get("id")
        entity_name = entity.get("name")
        entity_schema = entity.get("schema", "Unknown")

        try:
            start_time = time.time()
            impact = get_impact(entity_id)
            end_time = time.time()
            duration = end_time - start_time
            log_timing(f"get_impact for {entity_type} '{entity_name}'", duration)

            if DEBUG_MODE:
                write_json_to_file(os.path.join(LOGS_DIR, f"impact_data_{entity_type}_{entity_name}.json"), json.loads(impact))
            impact_data = json.loads(impact)
            impact_summary = process_database_entity_impact(
                impact_data, entity_type, entity_name, entity_schema
            )
            all_impacts.append(impact_summary)
        except Exception as e:
            sys.stderr.write(f"Error getting impact for {entity_type} '{entity_name}': {str(e)}\n")

    # Combine all impacts into a single report
    combined_report = generate_combined_database_report(
        entity_type, name, table_or_view, search_results, all_impacts
    )

    return [
        types.TextContent(
            type="text",
            text=combined_report
        )
    ]
