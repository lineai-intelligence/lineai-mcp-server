# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Utility functions for the Lineai MCP Server.

This module provides helper functions for authentication, data retrieval,
caching, and processing of code relationships from the Lineai server.
It handles API requests, caching of results, and data transformation for
impact analysis.
"""

import os
import sys
import httpx
import json
import toml
from datetime import datetime, timedelta
from typing import Dict, Any, List
import urllib.parse

def get_package_version() -> str:
    """
    Get the package version from pyproject.toml.
    
    Returns:
        str: The package version from pyproject.toml
        
    Raises:
        FileNotFoundError: If pyproject.toml cannot be found
        KeyError: If version cannot be found in pyproject.toml
    """
    try:
        # Get the directory containing this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up to the project root (where pyproject.toml is)
        project_root = os.path.dirname(os.path.dirname(current_dir))
        pyproject_path = os.path.join(project_root, 'pyproject.toml')
        
        with open(pyproject_path, 'r') as f:
            config = toml.load(f)
            return config['project']['version']
    except Exception as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}", file=sys.stderr)
        return "0.0.0"  # Fallback version if we can't read pyproject.toml

# Cache TTL settings from environment variables (in seconds)
TOKEN_CACHE_TTL = int(os.getenv('LINEAI_TOKEN_CACHE_TTL', '3600'))  # Default 1 hour
METHOD_CACHE_TTL = int(os.getenv('LINEAI_METHOD_CACHE_TTL', '300'))  # Default 5 minutes
IMPACT_CACHE_TTL = int(os.getenv('LINEAI_IMPACT_CACHE_TTL', '300'))  # Default 5 minutes

# Timeout settings from environment variables (in seconds)
REQUEST_TIMEOUT = float(os.getenv('LINEAI_REQUEST_TIMEOUT', '120.0'))
CONNECT_TIMEOUT = float(os.getenv('LINEAI_CONNECT_TIMEOUT', '30.0'))

# Cache storage
_cached_token = None
_token_expiry = None
_method_nodes_cache: Dict[str, tuple[List[Any], datetime]] = {}
_impact_cache: Dict[str, tuple[str, datetime]] = {}

# Configure HTTP client with improved settings
_client = httpx.Client(
    timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=30),
    transport=httpx.HTTPTransport(retries=3)
)

# Encode the workspace name to ensure it is safe for use in API calls
encoded_workspace_name = urllib.parse.quote(os.getenv("LINEAI_WORKSPACE_NAME") or "")


def find_node_by_id(nodes, id):
    """
    Find a node in a list of nodes by its ID.

    Args:
        nodes (List[Dict]): List of node dictionaries to search
        id (str): Node ID to find

    Returns:
        Dict or None: The node with the matching ID, or None if not found
    """
    for node in nodes:
        if node['id'] == id:
            return node
    return None


def get_mv_id(mv_name):
    """
    Get materialized view ID using its name.

    This is a helper function that combines authentication, getting the
    materialized view definition ID by name, and then retrieving the actual
    materialized view ID from the definition.

    Args:
        mv_name (str): The name of the materialized view

    Returns:
        str: The materialized view ID

    Raises:
        httpx.HTTPError: If API requests fail
    """
    token = authenticate()
    mv_def_id = get_mv_definition_id(mv_name, token)
    return get_mv_id_from_def(mv_def_id, token)


def get_mv_definition_id(mv_name, token):
    """
    Get materialized view definition ID by name.

    Args:
        mv_name (str): The name of the materialized view
        token (str): Authentication token

    Returns:
        str: The definition ID of the materialized view

    Raises:
        httpx.HTTPError: If API request fails
    """
    url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/materialized-view-definition/name?name={mv_name}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    response = _client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()['data']['id']


def get_mv_id_from_def(mv_def_id, token):
    """
    Get materialized view ID from its definition ID.

    Args:
        mv_def_id (str): The materialized view definition ID
        token (str): Authentication token

    Returns:
        str: The materialized view ID

    Raises:
        httpx.HTTPError: If API request fails
    """
    url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/materialized-view/latest?definitionId={mv_def_id}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    response = _client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()['data']['id']


def get_method_nodes(materialized_view_id, short_name):
    """
    Get nodes for a method by short name, with caching.

    This function searches for method nodes that match the given short name
    within the specified materialized view. Results are cached to improve
    performance for subsequent requests.

    Args:
        materialized_view_id (str): The ID of the materialized view to search in
        short_name (str): Short name of the method to find

    Returns:
        tuple[list, str | None]: ``(nodes, error_kind)``. ``nodes`` is the list of
        method dicts. ``error_kind`` is ``None`` on success (including an empty
        ``data`` array). On failure it is one of: ``not_found``, ``timeout``,
        ``gateway_timeout``, or ``http_error``.
    """
    cache_key = f"{materialized_view_id}:{short_name}"
    now = datetime.now()

    # Check cache
    if cache_key in _method_nodes_cache:
        nodes, expiry = _method_nodes_cache[cache_key]
        if now < expiry:
            sys.stderr.write(f"Method nodes cache hit for {short_name}\n")
            return nodes, None
        else:
            sys.stderr.write(f"Method nodes cache expired for {short_name}\n")

    try:
        token = authenticate()
        url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/ai-retrieval/search/shortname"
        # Match OpenAPI/Swagger: POST with query params and an empty body. Do not send
        # Content-Type: application/json with data={} — that can disagree with the
        # actual body and cause gateways or parsers to stall (504) while Swagger
        # succeeds quickly with an empty body.
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "*/*",
        }
        params = {
            "materializedViewId": materialized_view_id,
            "shortname": short_name
        }

        sys.stderr.write(f"Requesting method nodes for {short_name} with timeout {REQUEST_TIMEOUT}s\n")
        response = _client.post(url, headers=headers, params=params)

        if response.status_code == 404:
            detail = ""
            try:
                body = response.json()
                detail = (body.get("error") or {}).get("message", "") or ""
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            sys.stderr.write(f"No method nodes for shortname {short_name!r} (404){suffix}\n")
            return [], "not_found"

        response.raise_for_status()

        # Cache result
        nodes = response.json()['data']
        _method_nodes_cache[cache_key] = (nodes, now + timedelta(seconds=METHOD_CACHE_TTL))
        sys.stderr.write(f"Method nodes cached for {short_name} with TTL {METHOD_CACHE_TTL}s\n")
        return nodes, None
    except httpx.TimeoutException as e:
        sys.stderr.write(f"Timeout error fetching method nodes for {short_name}: {e}\n")
        return [], "timeout"
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        sys.stderr.write(f"HTTP error {code} fetching method nodes for {short_name}: {e}\n")
        if code == 504:
            return [], "gateway_timeout"
        return [], "http_error"
    except Exception as e:
        sys.stderr.write(f"Error fetching method nodes: {e}\n")
        return [], "http_error"


def extract_relationships(impact_data):
    """
    Extract relationship information from impact analysis data.

    Args:
        impact_data (Dict): Impact analysis data containing nodes and relationships

    Returns:
        List[str]: List of formatted relationship strings
    """
    relationships = []
    for rel in impact_data['data']['relationships']:
        start_node = find_node_by_id(impact_data['data']['nodes'], rel['startId'])
        end_node = find_node_by_id(impact_data['data']['nodes'], rel['endId'])
        if start_node and end_node:
            relationship = f"- {start_node['identity']} ({rel['type']}) -> {end_node['identity']}"
            relationships.append(relationship)
    return relationships


def get_impact(id):
    """
    Get impact analysis for a node, with caching.

    Retrieves the full dependency impact analysis for the specified node ID,
    caching the results for efficiency on subsequent requests.

    Args:
        id (str): The ID of the node for which to get impact analysis

    Returns:
        str: JSON string with impact analysis data

    Raises:
        httpx.HTTPError: If API request fails
    """
    now = datetime.now()

    # Check cache
    if id in _impact_cache:
        impact, expiry = _impact_cache[id]
        if now < expiry:
            sys.stderr.write(f"Impact cache hit for {id}\n")
            return impact
        else:
            sys.stderr.write(f"Impact cache expired for {id}\n")

    token = authenticate()
    url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/dependency/impact/full/{id}/list"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    response = _client.get(url, headers=headers)
    response.raise_for_status()

    result = strip_unused_properties(response)

    # Cache result
    _impact_cache[id] = (result, now + timedelta(seconds=IMPACT_CACHE_TTL))
    sys.stderr.write(f"Impact cached for {id} with TTL {IMPACT_CACHE_TTL}s\n")
    return result


def strip_unused_properties(response):
    """
    Remove unnecessary properties from impact analysis response.

    This optimizes the data size by removing fields that aren't needed
    for analysis.

    Args:
        response (httpx.Response): API response with impact analysis data

    Returns:
        str: Cleaned JSON string with optimized data
    """
    data = json.loads(response.text)

    # Strip out specific fields
    for node in data.get('data', {}).get('nodes', []):
        properties = node.get('properties', {})
        properties.pop('agentIds', None)
        properties.pop('sourceScanContextIds', None)
        properties.pop('isScanRoot', None)
        properties.pop('transitiveSourceNodeId', None)
        properties.pop('dataSourceId', None)
        properties.pop('scanContextId', None)
        properties.pop('id', None)
        properties.pop('shortName', None)
        properties.pop('materializedViewId', None)
        properties.pop('statistics.impactScore', None)
        properties.pop('lineai.quality.impactScore', None)
        properties.pop('identity', None)
        properties.pop('name', None)

    return json.dumps(data)


def extract_nodes(impact_data):
    """
    Extract node information from impact analysis data.

    Creates a standardized format for node data that's easier to process
    for impact analysis.

    Args:
        impact_data (Dict): Impact analysis data

    Returns:
        List[Dict]: List of standardized node dictionaries
    """
    nodes = []
    for node in impact_data.get('data', {}).get('nodes', []):
        node_info = {
            'id': node.get('id'),
            'identity': node.get('identity'),
            'name': node.get('name'),
            'primaryLabel': node.get('primaryLabel'),
            'properties': node.get('properties', {})
        }
        nodes.append(node_info)
    return nodes


def authenticate():
    """
    Authenticate with the Lineai server, with token caching.

    Uses credentials from environment variables to obtain an authentication token.
    Caches the token for future use to avoid unnecessary authentication requests.

    Returns:
        str: Authentication token

    Raises:
        Exception: If authentication fails
    """
    global _cached_token, _token_expiry
    now = datetime.now()

    # Return cached token if still valid
    if _cached_token is not None and _token_expiry is not None:
        if now < _token_expiry:
            sys.stderr.write("Using cached authentication token\n")
            return _cached_token
        else:
            sys.stderr.write("Authentication token expired\n")

    url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/authenticate"
    data = {
        "grant_type": "password",
        "username": os.getenv("LINEAI_USERNAME"),
        "password": os.getenv("LINEAI_PASSWORD")
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }

    try:
        response = _client.post(url, data=data, headers=headers)
        response.raise_for_status()
        _cached_token = response.json()['access_token']
        _token_expiry = now + timedelta(seconds=TOKEN_CACHE_TTL)
        sys.stderr.write(f"New authentication token cached with TTL {TOKEN_CACHE_TTL}s\n")
        return _cached_token
    except Exception as e:
        sys.stderr.write(f"Authentication error: {e}\n")
        raise


async def search_database_entity(entity_type, name, table_or_view=None):
    """
    Search for database entities using the Lineai API.

    Args:
        entity_type (str): Type of database entity (table, view, or column)
        name (str): Name of the database entity
        table_or_view (str, optional): Name of the table or view containing the column
            (required when entity_type is 'column')

    Returns:
        list: List of matching database entities
    """
    try:
        token = authenticate()
        url = f"{os.getenv('LINEAI_SERVER_HOST')}/api/ai-retrieval/search/{entity_type}"

        # Get materialized view ID (required parameter)
        mv_id = get_mv_id(encoded_workspace_name)

        # Create query parameters
        params = {
            "materializedViewId": mv_id
        }

        # Add the appropriate parameter name based on entity type
        if entity_type == "table":
            params["tableName"] = name
        elif entity_type == "column":
            params["columnName"] = name
            if table_or_view:
                params["tableOrViewName"] = table_or_view
        elif entity_type == "view":
            params["viewName"] = name

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        # Debug output
        sys.stderr.write(f"Calling {url} with params {params}\n")

        # Use POST as specified in the API
        response = _client.post(url, headers=headers, params=params, json={})
        response.raise_for_status()
        return response.json().get("data", [])
    except httpx.HTTPStatusError as e:
        sys.stderr.write(f"HTTP error {e.response.status_code} from API: {e}\n")
        sys.stderr.write(f"Response content: {e.response.text}\n")
        return []
    except Exception as e:
        sys.stderr.write(f"Error searching for {entity_type} '{name}': {str(e)}\n")
        return []  # Return empty list instead of propagating the error


def process_database_entity_impact(impact_data, entity_type, entity_name, entity_schema):
    """
    Process impact analysis data for a database entity.

    Args:
        impact_data: The impact analysis data from the API
        entity_type: The type of database entity (table, column, view)
        entity_name: The name of the entity
        entity_schema: The schema of the entity (may be "Unknown")

    Returns:
        Dict containing processed impact data
    """
    nodes = extract_nodes(impact_data)
    relationships = extract_relationships(impact_data)

    # Find the target entity node
    target_node = next((n for n in nodes if n['name'] == entity_name and n['primaryLabel'] == entity_type_to_label(entity_type)), None)
    if not target_node:
        return {
            "entity_type": entity_type,
            "name": entity_name,
            "schema": entity_schema,
            "dependent_code": [],
            "referencing_tables": [],
            "dependent_applications": [],
            "nodes": nodes,
            "relationships": relationships
        }

    # Get the actual schema name if available
    entity_schema = extract_schema_name(target_node, nodes) or entity_schema

    # Get the parent table for columns
    parent_table = None
    if entity_type == "column":
        parent_table = find_parent_table(target_node['id'], impact_data)

    # Find code dependencies
    direct_dependent_code = find_direct_dependent_code(target_node['id'], impact_data)

    # For columns, also include code that references the containing table
    table_dependent_code = []
    if entity_type == "column" and parent_table:
        table_dependent_code = find_direct_dependent_code(parent_table['id'], impact_data)
        # Mark these as indirect references
        for item in table_dependent_code:
            item["relationship_type"] = "indirect (via table)"

    # Combine direct and table dependencies, avoiding duplicates
    dependent_code = direct_dependent_code
    seen_ids = {item["id"] for item in dependent_code}
    for item in table_dependent_code:
        if item["id"] not in seen_ids:
            dependent_code.append(item)
            seen_ids.add(item["id"])

    # Find related database objects
    referencing_tables = find_referencing_database_objects(target_node['id'], impact_data)

    # Determine affected applications
    dependent_applications = extract_dependent_applications(dependent_code, impact_data)

    # Also include applications that directly group the database objects
    db_applications = find_database_applications(target_node['id'], impact_data)
    for app in db_applications:
        if app not in dependent_applications:
            dependent_applications.append(app)

    # Extract code owners and reviewers from the dependent code entities
    # Since database entities don't typically have ownership metadata directly,
    # we'll gather this information from the code entities that reference them
    code_owners = set()
    code_reviewers = set()

    # Check code entities that reference this database entity
    for code_item in dependent_code:
        code_id = code_item.get("id")
        code_node = next((n for n in nodes if n['id'] == code_id), None)
        if code_node:
            owners = code_node.get('properties', {}).get('lineai.owners', [])
            reviewers = code_node.get('properties', {}).get('lineai.reviewers', [])
            code_owners.update(owners)
            code_reviewers.update(reviewers)

            # Look for parent classes that might contain ownership info
            for rel in impact_data.get('data', {}).get('relationships', []):
                if rel.get('type').startswith('CONTAINS_') and rel.get('endId') == code_id:
                    parent_id = rel.get('startId')
                    parent_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), parent_id)
                    if parent_node and parent_node.get('primaryLabel', '').endswith('ClassEntity'):
                        parent_owners = parent_node.get('properties', {}).get('lineai.owners', [])
                        parent_reviewers = parent_node.get('properties', {}).get('lineai.reviewers', [])
                        code_owners.update(parent_owners)
                        code_reviewers.update(parent_reviewers)

    return {
        "entity_type": entity_type,
        "name": entity_name,
        "schema": entity_schema,
        "dependent_code": dependent_code,
        "referencing_tables": referencing_tables,
        "dependent_applications": dependent_applications,
        "parent_table": parent_table,
        "code_owners": list(code_owners),
        "code_reviewers": list(code_reviewers),
        "nodes": nodes,
        "relationships": relationships
    }


def entity_type_to_label(entity_type):
    """Convert entity_type parameter to node primaryLabel"""
    mapping = {
        "column": "Column",
        "table": "Table",
        "view": "View"
    }
    return mapping.get(entity_type, entity_type.capitalize())


def extract_schema_name(node, nodes):
    """Extract the schema name from a database entity node"""
    identity_parts = node.get('identity', '').split('|')
    if len(identity_parts) >= 2:
        schema_name = identity_parts[1]
        # Verify it's a schema by finding it in the nodes
        schema_node = next((n for n in nodes if n['name'] == schema_name and n['primaryLabel'] == 'Schema'), None)
        if schema_node:
            return schema_name
    return None


def find_parent_table(column_id, impact_data):
    """Find the table that contains a column"""
    for rel in impact_data.get('data', {}).get('relationships', []):
        if rel.get('type') == 'CONTAINS_COLUMN' and rel.get('endId') == column_id:
            table_id = rel.get('startId')
            table_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), table_id)
            if table_node and table_node.get('primaryLabel') == 'Table':
                return table_node
    return None


def find_direct_dependent_code(node_id, impact_data):
    """Find code that directly depends on the given database entity"""
    dependent_code = []
    for rel in impact_data.get('data', {}).get('relationships', []):
        # Check for code that references our target
        if rel.get('endId') == node_id and rel.get('type') in ['REFERENCES', 'USES', 'SELECTS', 'UPDATES', 'INSERTS', 'DELETES', 'REFERENCES_TABLE']:
            source_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel.get('startId'))
            if source_node and source_node.get('primaryLabel', '').endswith(('MethodEntity', 'ClassEntity')):
                dependent_code.append({
                    "id": source_node.get('id'),
                    "name": source_node.get('name'),
                    "type": source_node.get('primaryLabel'),
                    "relationship": rel.get('type'),
                    "relationship_type": "direct",
                    "complexity": source_node.get('properties', {}).get('statistics.cyclomaticComplexity', 'N/A')
                })
    return dependent_code


def find_referencing_database_objects(node_id, impact_data):
    """Find database objects that reference the given entity"""
    referencing_objects = []
    for rel in impact_data.get('data', {}).get('relationships', []):
        if rel.get('endId') == node_id and rel.get('type') in ['REFERENCES', 'FOREIGN_KEY']:
            source_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel.get('startId'))
            if source_node and source_node.get('primaryLabel') in ['Table', 'Column', 'View']:
                schema = extract_schema_name(source_node, impact_data.get('data', {}).get('nodes', [])) or 'Unknown'
                referencing_objects.append({
                    "id": source_node.get('id'),
                    "name": source_node.get('name'),
                    "type": source_node.get('primaryLabel'),
                    "schema": schema
                })
    return referencing_objects


def extract_dependent_applications(dependent_code, impact_data):
    """Extract application names that contain the dependent code"""
    applications = []

    # Build a map of node IDs to their containing applications
    node_to_app = {}
    app_nodes = {}

    # Find all Application nodes
    for node in impact_data.get('data', {}).get('nodes', []):
        if node.get('primaryLabel') == 'Application':
            app_nodes[node.get('id')] = node.get('name')

    # Map nodes to applications via GROUPS relationships
    for rel in impact_data.get('data', {}).get('relationships', []):
        if rel.get('type') == 'GROUPS' and rel.get('startId') in app_nodes:
            node_to_app[rel.get('endId')] = app_nodes[rel.get('startId')]

    # Find applications for each code element
    for code in dependent_code:
        code_id = code.get('id')
        if code_id in node_to_app:
            app_name = node_to_app[code_id]
            if app_name not in applications:
                applications.append(app_name)

        # Also check for containing elements that might be mapped to applications
        # (e.g., if a method belongs to a class that is grouped by an application)
        for rel in impact_data.get('data', {}).get('relationships', []):
            if rel.get('endId') == code_id and rel.get('type').startswith('CONTAINS_'):
                parent_id = rel.get('startId')
                if parent_id in node_to_app:
                    app_name = node_to_app[parent_id]
                    if app_name not in applications:
                        applications.append(app_name)

    return applications


def find_database_applications(node_id, impact_data):
    """
    Find applications that directly or indirectly group the database entity.

    This function traverses both direct grouping relationships and indirect
    relationships through containment chains to identify all applications
    that might be affected by changes to the database entity.

    Args:
        node_id (str): ID of the database entity node
        impact_data (dict): Impact analysis data from the API

    Returns:
        list: Names of applications that group this database entity
    """
    applications = []
    processed_nodes = set()  # Track processed nodes to avoid infinite recursion

    # Find all application nodes and create lookup map
    app_nodes = {}
    for node in impact_data.get('data', {}).get('nodes', []):
        if node.get('primaryLabel') == 'Application':
            app_nodes[node.get('id')] = node.get('name')

    # Recursive function to traverse containment and grouping relationships
    def traverse_relationships(current_id):
        if current_id in processed_nodes:
            return  # Avoid cycles

        processed_nodes.add(current_id)

        # Check direct grouping by applications
        for rel in impact_data.get('data', {}).get('relationships', []):
            # If an application directly groups this node
            if rel.get('type') == 'GROUPS' and rel.get('endId') == current_id and rel.get('startId') in app_nodes:
                app_name = app_nodes[rel.get('startId')]
                if app_name not in applications:
                    applications.append(app_name)

            # Check relationships that refer to or contain this node
            # These can lead us to components that might be grouped by applications
            if rel.get('endId') == current_id:
                # Follow containment and reference relationships up the chain
                if rel.get('type').startswith('CONTAINS_') or rel.get('type') in ['REFERENCES', 'REFERENCES_TABLE']:
                    # Recursively check the parent node
                    traverse_relationships(rel.get('startId'))

    # Start traversal from the target node
    traverse_relationships(node_id)

    # Additional check for database-specific structures
    # Some applications might group the database or schema containing our entity
    current_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), node_id)
    if current_node and current_node.get('primaryLabel') in ['Table', 'Column', 'View']:
        # Try to find the database node
        for rel in impact_data.get('data', {}).get('relationships', []):
            if rel.get('type').startswith('CONTAINS_') and rel.get('endId') == node_id:
                # This might be a schema containing our table or a table containing our column
                container_id = rel.get('startId')
                container_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), container_id)

                if container_node and container_node.get('primaryLabel') in ['Table', 'Schema', 'Database']:
                    # Recursively process this container to find applications
                    traverse_relationships(container_id)

    return applications


def find_api_endpoints(nodes, relationships):
    """
    Find API endpoints, controllers, and their dependencies in impact data.

    Args:
        nodes (list): List of nodes from impact analysis
        relationships (list): List of relationships from impact analysis

    Returns:
        tuple: (endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies)
            - endpoint_nodes: Explicit endpoint nodes
            - rest_endpoints: Methods with REST annotations
            - api_controllers: Controller classes
            - endpoint_dependencies: Dependencies between endpoints
    """
    # Find explicit endpoints
    endpoint_nodes = []
    for node_item in nodes:
        # Check for Endpoint primary label
        if node_item.get('primaryLabel') == 'Endpoint':
            endpoint_nodes.append({
                'name': node_item.get('name', ''),
                'path': node_item.get('properties', {}).get('path', ''),
                'http_verb': node_item.get('properties', {}).get('httpVerb', ''),
                'id': node_item.get('id')
            })

    # Find REST-annotated methods
    rest_endpoints = []
    api_controllers = []

    for node_item in nodes:
        # Check for controller types
        if any(term in node_item.get('primaryLabel', '').lower() for term in
               ['controller', 'restendpoint', 'apiendpoint', 'webservice']):
            api_controllers.append({
                'name': node_item.get('name', ''),
                'type': node_item.get('primaryLabel', '')
            })

        # Check for REST annotations on methods
        if node_item.get('primaryLabel') in ['JavaMethodEntity', 'DotNetMethodEntity']:
            annotations = node_item.get('properties', {}).get('annotations', [])
            if annotations and any(
                    anno.lower() in str(annotations).lower() for anno in
                    [
                        'getmapping', 'postmapping', 'putmapping', 'deletemapping',
                        'requestmapping', 'httpget', 'httppost', 'httpput', 'httpdelete'
                    ]):
                rest_endpoints.append({
                    'name': node_item.get('name', ''),
                    'annotation': str([a for a in annotations if any(m in a.lower() for m in ['mapping', 'http'])])
                })

    # Find endpoint dependencies
    endpoint_dependencies = []
    for rel in relationships:
        if rel.get('type') in ['INVOKES_ENDPOINT', 'REFERENCES_ENDPOINT']:
            start_node = find_node_by_id(nodes, rel.get('startId'))
            end_node = find_node_by_id(nodes, rel.get('endId'))

            if start_node and end_node:
                endpoint_dependencies.append({
                    'source': start_node.get('name', 'Unknown'),
                    'target': end_node.get('name', 'Unknown')
                })

    return endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies


def generate_combined_database_report(entity_type, search_name, table_or_view, search_results, all_impacts):
    """
    Generate a combined report for all database entities.
    """
    table_view_text = f" in {table_or_view}" if table_or_view else ""
    report = f"# Database Impact Analysis: {entity_type.capitalize()}s matching '{search_name}'{table_view_text}\n\n"
    report += f"## Overview\nFound {len(search_results)} {entity_type}(s) matching your search criteria.\n\n"
    if not all_impacts:
        report += "No impact analysis data could be retrieved for these entities.\n"
        return report

    # Collect all applications across all impacts
    all_apps = set()
    for impact in all_impacts:
        all_apps.update(impact.get("dependent_applications", []))

    report += "## Application Impact\n"
    if all_apps:
        report += f"Changes to these database objects could affect {len(all_apps)} applications:\n\n"
        for app in sorted(all_apps):
            report += f"- `{app}`\n"
    else:
        report += "No applications appear to directly depend on these database objects.\n"

    report += "\n## Detailed Analysis\n\n"
    for i, impact in enumerate(all_impacts):
        entity_name = impact.get("name", "Unknown")
        entity_schema = impact.get("schema", "Unknown")

        # Format the entity identifier differently based on entity type
        if entity_type == "column":
            parent_table = impact.get("parent_table", {})
            table_name = parent_table.get("name", "Unknown") if parent_table else "Unknown"
            entity_id = f"`{entity_schema}.{table_name}.{entity_name}`"
        else:
            entity_id = f"`{entity_schema}.{entity_name}`"

        report += f"### {i + 1}. {entity_type.capitalize()}: {entity_id}\n\n"

        # Add code ownership information if available
        code_owners = impact.get("code_owners", [])
        code_reviewers = impact.get("code_reviewers", [])

        if code_owners or code_reviewers:
            report += "#### Code Ownership\n"
            if code_owners:
                report += f"👤 **Code Owners**: {', '.join(code_owners)}\n"
            if code_reviewers:
                report += f"👁️ **Preferred Reviewers**: {', '.join(code_reviewers)}\n"
            if code_owners:
                report += "\nConsult with the code owners before making significant changes to ensure alignment with original design intent.\n\n"
            else:
                report += "\n"

        # For columns, show the parent table information
        parent_table = impact.get("parent_table")
        if parent_table and entity_type == "column":
            parent_table_name = parent_table.get("name", "Unknown")
            report += f"This column is part of the `{parent_table_name}` table.\n\n"

        # Show code dependencies
        dependent_code = impact.get("dependent_code", [])
        report += "#### Code Dependencies\n"
        if dependent_code:
            report += f"This database object is referenced by {len(dependent_code)} code elements:\n\n"
            report += "| Code Element | Type | Relationship | Reference Type | Complexity |\n|-------------|------|-------------|---------------|------------|\n"
            for code in dependent_code[:10]:
                reference_type = code.get("relationship_type", "direct")
                complexity = code.get("complexity", "N/A")
                report += f"| `{code['name']}` | {code['type']} | {code['relationship']} | {reference_type} | {complexity} |\n"
            if len(dependent_code) > 10:
                report += f"\n*...and {len(dependent_code) - 10} more*\n"
        else:
            report += "No code elements directly reference this database object.\n"

        # Show related database objects
        referencing_tables = impact.get("referencing_tables", [])
        if referencing_tables:
            report += "\n#### Related Database Objects\n"
            report += f"This database object is referenced by {len(referencing_tables)} other database objects:\n\n"
            report += "| Database Object | Type | Schema |\n|----------------|------|--------|\n"
            for table in referencing_tables[:10]:
                report += f"| `{table['name']}` | {table['type']} | {table['schema']} |\n"
            if len(referencing_tables) > 10:
                report += f"\n*...and {len(referencing_tables) - 10} more*\n"

        # Risk assessment
        report += "\n#### Risk Assessment\n"
        total_deps = len(dependent_code) + len(referencing_tables)
        if total_deps > 20:
            report += "⚠️ **High Risk**: This database object has numerous dependencies. Changes require careful planning and thorough testing.\n"
        elif total_deps > 5:
            report += "⚠️ **Medium Risk**: This database object has multiple dependencies. Changes should be tested across affected systems.\n"
        else:
            report += "✅ **Low Risk**: This database object has few dependencies. Changes are likely isolated.\n"

        # Cross-application impact warning
        if len(impact.get("dependent_applications", [])) > 1:
            report += "\n⚠️ **Cross-Application Impact**: Changes to this database object affect multiple applications.\n"
        report += "\n"

    # Add best practices section
    report += """
## Best Practices for Database Changes

### Guidelines for AI
- Suggest schema migration scripts when modifying database objects
- Consider backward compatibility when tables are used across multiple applications
- Recommend creating database views for frequently changed schemas
- Suggest unit and integration tests for any code that depends on changed database objects
- Recommend incremental migration approaches for high-risk changes

### Risk Mitigation Strategies
1. Create and test migration scripts thoroughly
2. Deploy database changes before code changes that depend on them
3. Consider implementing feature flags for risky changes
4. Plan for rollback procedures
5. Test all affected applications after changes
"""
    return report
