# Copyright (C) 2025 Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Handler for the lineai-method-impact tool.

LIN-870: the method node is selected by **class match rank** (fully-qualified
name > simple name > substring; ambiguity is reported, never guessed) and the
impact comes from the bounded graph impact endpoint
(``POST /api/ai-retrieval/graph/impact``, direction ``upstream``), falling back
to the legacy ``/api/dependency/impact/full`` list only when the graph
endpoint is not deployed or ``LINEAI_METHOD_IMPACT_BACKEND=legacy``.
"""

import json
import os
import sys
import time
import mcp.types as types
from .common import error_result, write_json_to_file, log_timing, DEBUG_MODE, LOGS_DIR
from .. import utils as lineai_utils
from ..graph_client import get_graph_impact
from ..utils import (
    extract_nodes,
    extract_relationships,
    extract_unresolved_references,
    find_api_endpoints,
    find_node_by_id,
    format_unresolved_references_section,
    get_impact,
    get_method_nodes,
    is_unresolved_reference,
    resolve_mv_id,
    strip_unused_node_properties,
)

DEFAULT_IMPACT_DEPTH = 3
MAX_IMPACT_DEPTH = 10
GRAPH_IMPACT_ENDPOINT = "/api/ai-retrieval/graph/impact"
LEGACY_IMPACT_ENDPOINT = "/api/dependency/impact/full/{id}/list"

# Class-match ranks (higher wins).
RANK_FQN = 3
RANK_SIMPLE = 2
RANK_SUBSTRING = 1
RANK_NONE = 0


# --- class selection (pure helpers, unit-tested) ------------------------------

def class_key(identity: str | None) -> str:
    """The identity minus its last ``|`` segment (the method signature) — one key per class."""
    identity = identity or ""
    if "|" not in identity:
        return identity
    return identity.rsplit("|", 1)[0]


def _normalize_class_path(value: str) -> str:
    """``pkg|Outer$Inner`` / ``pkg.Outer.Inner`` → ``pkg.outer.inner`` for comparison."""
    return value.replace("|", ".").replace("$", ".").strip(".").lower()


def class_simple_name(class_name: str | None) -> str | None:
    """Last component of a dotted / ``$`` class name (``pkg.Outer$Inner`` → ``Inner``)."""
    if not class_name:
        return None
    return _normalize_class_path(class_name).rsplit(".", 1)[-1] if class_name else None


def class_match_rank(identity: str | None, class_name: str) -> int:
    """
    How well a method node's identity matches the requested class.

    * ``RANK_FQN`` — the identity's class path equals the fully-qualified name
      (``com.acme.shell|AnalyzeCommands|run()``, ``com.acme.shell.AnalyzeCommands|run()``
      and ``com.acme|shell|AnalyzeCommands|run()`` all match ``com.acme.shell.AnalyzeCommands``);
    * ``RANK_SIMPLE`` — the class path's last component equals the simple name (case-insensitive);
    * ``RANK_SUBSTRING`` — the simple name occurs somewhere in the identity (legacy behaviour);
    * ``RANK_NONE`` otherwise.
    """
    if not identity or not class_name:
        return RANK_NONE
    key = _normalize_class_path(class_key(identity))
    wanted = _normalize_class_path(class_name)
    simple = wanted.rsplit(".", 1)[-1]
    if key == wanted:
        return RANK_FQN
    if key.rsplit(".", 1)[-1] == simple:
        return RANK_SIMPLE
    if simple and simple in identity.lower():
        return RANK_SUBSTRING
    return RANK_NONE


def select_method_node(nodes: list[dict], class_name: str | None) -> tuple[dict | None, str, list[dict]]:
    """
    Pick the method node to analyze.

    Returns ``(chosen, outcome, candidates)`` where ``outcome`` is one of
    ``single`` (one class, one node), ``overloads`` (one class, several
    signatures — the first is used), ``fqn`` / ``simple`` / ``substring`` (how
    the class argument matched), ``ambiguous`` (several classes — nothing is
    chosen), ``fqn_not_found`` (a fully-qualified class was asked for and no
    node belongs to it — nothing is chosen, never another repository's class)
    or ``no_class_match``. Unresolved-reference rows are never chosen.
    """
    concrete = [n for n in nodes if isinstance(n, dict) and not is_unresolved_reference(n) and n.get("identity")]
    if not concrete:
        return None, "no_class_match", []

    if not class_name:
        keys = {class_key(n.get("identity")) for n in concrete}
        if len(keys) > 1:
            return None, "ambiguous", concrete
        return concrete[0], ("overloads" if len(concrete) > 1 else "single"), concrete

    ranked = [(class_match_rank(n.get("identity"), class_name), n) for n in concrete]
    best = max(rank for rank, _ in ranked)
    if "." in class_name or "$" in class_name:
        if best < RANK_FQN:
            same_simple = [n for rank, n in ranked if rank >= RANK_SIMPLE]
            return None, "fqn_not_found", same_simple or concrete
        matches = [n for rank, n in ranked if rank == RANK_FQN]
        return matches[0], ("overloads" if len(matches) > 1 else "fqn"), matches

    if best == RANK_NONE:
        return None, "no_class_match", concrete
    matches = [n for rank, n in ranked if rank == best]
    keys = {class_key(n.get("identity")) for n in matches}
    if len(keys) > 1:
        return None, "ambiguous", matches
    if len(matches) > 1:
        return matches[0], "overloads", matches
    return matches[0], ("simple" if best == RANK_SIMPLE else "substring"), matches


def _candidate_lines(candidates: list[dict], limit: int = 20) -> str:
    """Candidates grouped by class, as Markdown bullets (at most ``limit`` identities)."""
    by_class: dict[str, list[str]] = {}
    for n in candidates:
        identity = n.get("identity") or ""
        by_class.setdefault(class_key(identity), []).append(identity)
    lines: list[str] = []
    shown = 0
    for key in sorted(by_class):
        for identity in sorted(by_class[key]):
            if shown >= limit:
                break
            lines.append(f"- `{identity}`")
            shown += 1
    total = sum(len(v) for v in by_class.values())
    if total > limit:
        lines.append(f"- *...and {total - limit} more*")
    return "\n".join(lines)


def _class_names_of(candidates: list[dict]) -> list[str]:
    return sorted({class_key(n.get("identity") or "").replace("|", ".") for n in candidates})


# --- impact fetch (graph first, legacy fallback) ------------------------------

def clamp_depth(value) -> int:
    """``depth`` argument → 1..MAX_IMPACT_DEPTH (default DEFAULT_IMPACT_DEPTH)."""
    if value is None or value == "":
        return DEFAULT_IMPACT_DEPTH
    try:
        depth = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"`depth` must be an integer between 1 and {MAX_IMPACT_DEPTH} (got {value!r})")
    return max(1, min(depth, MAX_IMPACT_DEPTH))


def graph_impact_to_impact_data(payload) -> dict:
    """
    Map a graph impact payload (enveloped or bare) to the legacy
    ``{"data": {"nodes": [...], "relationships": [...]}}`` shape the report
    builder reads, stripped like the legacy response.
    """
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    data = data if isinstance(data, dict) else {}
    nodes = []
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nodes.append({
            "id": node.get("id"),
            "identity": node.get("identity"),
            "name": node.get("name"),
            "primaryLabel": node.get("primaryLabel"),
            "properties": dict(node.get("properties") or {}),
        })
    relationships = []
    for rel in data.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        relationships.append({
            "id": rel.get("id"),
            "startId": rel.get("startId"),
            "endId": rel.get("endId"),
            "type": rel.get("type"),
            "contains": rel.get("contains", False),
            "group": rel.get("group", False),
        })
    return strip_unused_node_properties({"data": {"nodes": nodes, "relationships": relationships}})


_GRAPH_KIND_TO_API_KIND = {
    "timeout": "timeout",
    "gateway_timeout": "gateway_timeout",
    "invalid_json": "invalid_response",
    "http_error": "http_error",
}


def fetch_impact(target_id: str, mv_id, depth: int) -> tuple[dict, dict]:
    """
    The impact subgraph for ``target_id`` plus a ``meta`` dict describing where
    it came from and how complete it is.

    Backend order: ``LINEAI_METHOD_IMPACT_BACKEND=legacy`` → legacy only;
    otherwise the graph impact endpoint (``direction=upstream``, ``depth``),
    falling back to the legacy list when the graph endpoint answers 404
    (not deployed, or the node is unknown to it). Transport / server errors
    from the graph endpoint raise :class:`LineaiApiError` (rendered by the
    dispatcher) — they are not silently retried on the unbounded legacy path.
    """
    meta = {
        "source": "legacy",
        "status": "ok",
        "assumptions": [],
        "truncations": [],
        "static_analysis_gap": None,
        "node_count": None,
        "depth": depth,
        "direction": "upstream",
        "fallback_reason": None,
    }
    backend = (os.getenv("LINEAI_METHOD_IMPACT_BACKEND") or "graph").strip().lower()
    if backend == "legacy":
        meta["fallback_reason"] = "LINEAI_METHOD_IMPACT_BACKEND=legacy"
    else:
        payload, status, kind, snippet = get_graph_impact(target_id, mv_id, direction="upstream", depth=depth)
        if kind is None:
            data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
            data = data if isinstance(data, dict) else {}
            assumptions = [str(a) for a in (data.get("assumptionsApplied") or [])]
            meta.update({
                "source": "graph",
                "status": str(data.get("status") or "ok"),
                "assumptions": assumptions,
                "truncations": [a for a in assumptions if a.startswith("truncated_at_")],
                "static_analysis_gap": bool(data.get("staticAnalysisGap")),
                "node_count": data.get("nodeCount"),
                "depth": data.get("depth", depth),
                "direction": data.get("direction") or "upstream",
            })
            return graph_impact_to_impact_data(data), meta
        if kind == "not_deployed":
            meta["fallback_reason"] = (
                f"graph impact endpoint returned HTTP {status or 404} (not deployed on this server, or the node is unknown to it)"
            )
            sys.stderr.write(f"Graph impact unavailable for {target_id} ({status}); falling back to legacy impact\n")
        else:
            # via the module attribute so the live class is used even after a test-time reload
            raise lineai_utils.LineaiApiError(
                _GRAPH_KIND_TO_API_KIND.get(kind, "http_error"), status=status or None,
                detail=snippet or "", endpoint=GRAPH_IMPACT_ENDPOINT,
            )
    impact_data = json.loads(get_impact(target_id, mv_id))
    meta["depth"] = None
    meta["direction"] = "upstream (full incoming chain)"
    return impact_data, meta


def _impact_scope_section(meta: dict, outcome: str, chosen_identity: str | None, overloads: list[str]) -> str:
    """The ``## Impact scope`` report block: source, completeness, server notes, class match."""
    if meta["source"] == "graph":
        source = f"graph (`POST {GRAPH_IMPACT_ENDPOINT}`, direction `{meta['direction']}`, depth {meta['depth']})"
    else:
        source = f"legacy (`GET {LEGACY_IMPACT_ENDPOINT}`, full incoming chain)"
        if meta.get("fallback_reason"):
            source += f" — fallback reason: {meta['fallback_reason']}"
    lines = ["## Impact scope", f"- **Source**: {source}"]
    if meta["status"] != "ok" or meta["truncations"]:
        markers = f" ({', '.join(meta['truncations'])})" if meta["truncations"] else ""
        lines.append(
            f"- **Status**: `{meta['status']}`{markers} — the counts below are a LOWER BOUND, not the full blast radius; "
            "re-run with a smaller `depth` or use `lineai-graph-impact` on specific node ids to tighten it"
        )
    else:
        lines.append(f"- **Status**: `{meta['status']}`")
    if meta["source"] == "graph":
        gap = "yes — no `INVOKES*` edges in this subgraph, call-site impact may be under-reported" if meta["static_analysis_gap"] else "no"
        lines.append(f"- **Static-analysis gap**: {gap}")
        if meta.get("node_count") is not None:
            lines.append(f"- **Nodes reached**: {meta['node_count']}")
        notes = [a for a in meta["assumptions"] if not a.startswith("truncated_at_")]
        if notes:
            lines.append(f"- **Server notes**: {', '.join(f'`{a}`' for a in notes)}")
    match = {
        "single": "the only method with that short name",
        "overloads": "several signatures in one class — the first is analyzed",
        "fqn": "fully-qualified class name matched exactly",
        "simple": "simple class name matched exactly (unique in the view)",
        "substring": "class name matched as a substring (unique in the view)",
    }.get(outcome, outcome)
    lines.append(f"- **Class match**: {match}" + (f" — `{chosen_identity}`" if chosen_identity else ""))
    if overloads:
        lines.append("- **Overloads in this class**: " + ", ".join(f"`{o}`" for o in overloads))
    return "\n".join(lines) + "\n"


def handle_method_impact(arguments: dict | None) -> list[types.TextContent] | types.CallToolResult:
    """Handle the lineai-method-impact tool for method/function analysis"""
    if not arguments:
        sys.stderr.write("Missing arguments\n")
        raise ValueError("Missing arguments")

    method_name = arguments.get("method")
    class_name = (arguments.get("class") or "").strip() or None
    # LIN-870: the simple name is only used for report filters; selection uses the full argument.
    simple_name = class_simple_name(class_name)

    if not (method_name):
        sys.stderr.write("Method must be provided\n")
        raise ValueError("Method must be provided")
    depth = clamp_depth(arguments.get("depth"))

    # Resolve the materialized view: explicit id -> workspace arg -> env -> server default
    mv_id = resolve_mv_id(arguments)

    start_time = time.time()
    nodes, method_lookup_error = get_method_nodes(mv_id, method_name)
    end_time = time.time()
    duration = end_time - start_time
    log_timing(f"get_method_nodes for method '{method_name}' in class '{class_name}'", duration)

    if not nodes:
        if method_lookup_error == "not_found" or method_lookup_error is None:
            # 404 from the search API and an empty 200 are the same honest answer:
            # the view resolved, the method is not in it. Not an infrastructure problem.
            error_message = f"""# Unable to Analyze Method: `{method_name}`

## Result
The materialized view resolved successfully (view `{mv_id}`), but no method matched the short name `{method_name}` in it. This is a definitive no-match result from the Lineai search API, not an index or infrastructure problem.

## Recommendations:
1. Check the spelling of the method short name.
2. Confirm the `workspace` / `materialized_view_id` arguments (or `LINEAI_WORKSPACE_NAME`) point at the workspace that contains this code.
3. If the method was added recently, rebuild the materialized view on the Lineai server and retry.
"""
        elif method_lookup_error == "timeout":
            error_message = f"""# Unable to Analyze Method: `{method_name}`

## Error
The shortname search request timed out after {os.getenv('LINEAI_REQUEST_TIMEOUT', '120.0')}s (client timeout).

## Recommendations:
1. Retry when the server is less busy, or raise `LINEAI_REQUEST_TIMEOUT` if appropriate.
2. Verify network access to: {os.getenv('LINEAI_SERVER_HOST')}
"""
        elif method_lookup_error == "gateway_timeout":
            error_message = f"""# Unable to Analyze Method: `{method_name}`

## Error
The Lineai API returned **504 Gateway Timeout** while searching for method nodes (upstream did not respond in time).

## Recommendations:
1. Try again in a few minutes; transient load or cold queries often cause this.
2. If Swagger returns 404 quickly for the same method and workspace, report the discrepancy to Lineai — the authenticated or MCP request path may differ from browser/Swagger.
3. Server: {os.getenv('LINEAI_SERVER_HOST')}
"""
        else:
            error_message = f"""# Unable to Analyze Method: `{method_name}`

## Error
The request to retrieve method information from the Lineai server failed (HTTP error or unexpected response). This is an infrastructure problem — it says nothing about whether the method exists.

## Recommendations:
1. Check MCP stderr logs for the exact HTTP status and message
2. Retry; if the failure persists, check the health of the Lineai server
3. Server: {os.getenv('LINEAI_SERVER_HOST')}
"""
        return error_result(error_message)

    node, outcome, candidates = select_method_node(nodes, class_name)
    if node is None:
        candidate_lines = _candidate_lines(candidates)
        if outcome == "ambiguous":
            classes = _class_names_of(candidates)
            class_lines = "\n".join(f"- `{c}`" for c in classes[:20])
            return error_result(f"""# Ambiguous Method: `{method_name}`

## Result
The short name `{method_name}` exists in **{len(classes)} classes** in materialized view `{mv_id}`{f" matching `{class_name}`" if class_name else ""}. Analyzing an arbitrary one would report another repository's impact as if it were yours, so nothing was analyzed. This is a disambiguation request, not an infrastructure problem.

## Classes containing `{method_name}`
{class_lines}

## Candidate identities
{candidate_lines}

## Recommendations:
1. Re-run with the fully-qualified class name in `class` (for example `class: "{classes[0]}"`).
2. Or call `lineai-graph-impact` directly on the node id you mean.
""")
        if outcome == "fqn_not_found":
            return error_result(f"""# Unable to Analyze Method: `{method_name}`

## Result
The short name `{method_name}` matched in materialized view `{mv_id}`, but **no match belongs to the class `{class_name}`**. The same simple name exists in other classes (listed below); they were NOT analyzed in its place. This is a no-match on the fully-qualified class, not an infrastructure problem.

## Candidate identities
{candidate_lines}

## Recommendations:
1. Check the package / outer-class part of `class`; the candidates show the identities that exist in this view.
2. If the class was added or moved recently, rebuild the materialized view on the Lineai server and retry.
3. Do not report `{class_name}` as "not indexed" unless `lineai-graph-search` for it also returns no class hit.
""")
        return error_result(f"""# Unable to Analyze Method: `{method_name}`

## Result
The short name `{method_name}` matched in materialized view `{mv_id}`, but none of the matches belong to a class matching `{class_name}` (fully-qualified, simple or substring, case-insensitive). This is a no-match on the class filter, not an infrastructure problem.

## Candidate identities
{candidate_lines}

## Recommendations:
1. Pick the intended match from the candidates above and pass its fully-qualified class in the `class` argument.
2. Or omit `class` when the method name is unique to one class.
""")
    chosen_identity = node.get('identity')
    overloads = [n.get('identity') for n in candidates if n is not node and class_key(n.get('identity')) == class_key(chosen_identity)] if outcome == "overloads" else []

    # Capture the search node's id BEFORE strip_unused_properties removes properties.id
    target_id = (node.get('properties') or {}).get('id') or node.get('id')

    start_time = time.time()
    impact_data, impact_meta = fetch_impact(target_id, mv_id, depth)
    end_time = time.time()
    duration = end_time - start_time
    log_timing(f"fetch_impact[{impact_meta['source']}] for node '{node.get('name')}'", duration)

    if DEBUG_MODE:
        method_file_name = os.path.join(LOGS_DIR, f"impact_data_method_{simple_name}_{method_name}.json") if simple_name else os.path.join(LOGS_DIR, f"impact_data_method_{method_name}.json")
        write_json_to_file(method_file_name, impact_data)

    nodes = extract_nodes(impact_data)
    relationships = extract_relationships(impact_data)
    unresolved_references = extract_unresolved_references(impact_data)

    # LIN-870: the selected node is known by id — look it up first (the graph payload
    # carries the seed as its first node); the name/class heuristics only serve the
    # legacy payload, whose node ids may be strings of a different shape.
    target_node = next((n for n in nodes if n.get('id') is not None and str(n.get('id')) == str(target_id)), None)

    # Support both Java and DotNet method entities
    method_entity_types = ['JavaMethodEntity', 'DotNetMethodEntity']
    method_nodes = []

    # First look for method nodes of any supported language
    for entity_type in method_entity_types:
        language_method_nodes = [n for n in nodes if n['primaryLabel'] == entity_type and method_name.lower() in (n.get('name') or '').lower()]
        method_nodes.extend(language_method_nodes)

    # If we have class name, further filter to find nodes that contain it
    if simple_name:
        class_filtered_nodes = [n for n in method_nodes if simple_name in (n.get('identity') or '').lower()]
        if class_filtered_nodes:
            method_nodes = class_filtered_nodes

    # Find the node with complexity metrics (prefer this)
    if not target_node:
        for n in method_nodes:
            if n['properties'].get('statistics.cyclomaticComplexity') is not None:
                target_node = n
                break

    # If not found, take the first method node
    if not target_node and method_nodes:
        target_node = method_nodes[0]

    # Extract key metrics
    complexity = target_node['properties'].get('statistics.cyclomaticComplexity', 'N/A') if target_node else 'N/A'
    instruction_count = target_node['properties'].get('statistics.instructionCount', 'N/A') if target_node else 'N/A'

    # Extract code owners and reviewers
    code_owners = target_node['properties'].get('lineai.owners', []) if target_node else []
    code_reviewers = target_node['properties'].get('lineai.reviewers', []) if target_node else []

    # If target node doesn't have owners/reviewers, try to find them from the class or file node
    if not code_owners or not code_reviewers:
        class_node = None
        if simple_name:
            class_node = next((n for n in nodes if (n.get('primaryLabel') or '').endswith('ClassEntity') and simple_name in (n.get('name') or '').lower()), None)

        if class_node:
            if not code_owners:
                code_owners = class_node['properties'].get('lineai.owners', [])
            if not code_reviewers:
                code_reviewers = class_node['properties'].get('lineai.reviewers', [])

    # Identify dependents (systems that depend on this method)
    dependents = []

    for rel in impact_data.get('data', {}).get('relationships', []):
        start_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['startId'])
        end_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['endId'])

        if start_node and end_node and end_node['id'] == target_id:
            # This is an incoming relationship (dependent)
            dependents.append({
                "name": start_node.get('name'),
                "type": start_node.get('primaryLabel'),
                "relationship": rel.get('type')
            })

    # Identify applications that depend on this method
    affected_applications = set()
    app_nodes = [n for n in nodes if n['primaryLabel'] == 'Application']
    app_id_to_name = {app['id']: app['name'] for app in app_nodes}

    # Add all applications found in the impact analysis as potentially affected
    for app in app_nodes:
        affected_applications.add(app['name'])

    # Map nodes to their applications via groupIds (Java approach)
    for node_item in nodes:
        if 'groupIds' in node_item['properties']:
            for group_id in node_item['properties']['groupIds']:
                if group_id in app_id_to_name:
                    affected_applications.add(app_id_to_name[group_id])

    # Count direct and indirect application dependencies
    app_dependencies = {}

    # Check both REFERENCES_GROUP and GROUPS relationships
    for rel in impact_data.get('data', {}).get('relationships', []):
        if rel.get('type') in ['REFERENCES_GROUP', 'GROUPS']:
            start_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['startId'])
            end_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['endId'])

            # For GROUPS relationships - application groups a component
            if rel.get('type') == 'GROUPS' and start_node and start_node.get('primaryLabel') == 'Application':
                app_name = start_node.get('name')
                affected_applications.add(app_name)

            # For REFERENCES_GROUP - one application depends on another
            if rel.get('type') == 'REFERENCES_GROUP' and start_node and end_node and start_node.get('primaryLabel') == 'Application' and end_node.get('primaryLabel') == 'Application':
                app_name = start_node.get('name')
                depends_on = end_node.get('name')
                if app_name:
                    affected_applications.add(app_name)
                    if app_name not in app_dependencies:
                        app_dependencies[app_name] = []
                    app_dependencies[app_name].append(depends_on)

    # Use the new utility function to detect API endpoints and controllers
    endpoint_nodes, rest_endpoints, api_controllers, endpoint_dependencies = find_api_endpoints(nodes, impact_data.get('data', {}).get('relationships', []))

    # Format nodes with metrics in markdown table format
    nodes_table = "| Name | Type | Complexity | Instruction Count | Method Count | Outgoing Refs | Incoming Refs |\n"
    nodes_table += "|------|------|------------|-------------------|-------------|---------------|---------------|\n"

    for node_item in nodes:
        if is_unresolved_reference(node_item):
            continue  # URs carry no metrics; they get their own report section
        name = node_item['name']
        node_type = node_item['primaryLabel']
        node_complexity = node_item['properties'].get('statistics.cyclomaticComplexity', 'N/A')
        node_instructions = node_item['properties'].get('statistics.instructionCount', 'N/A')
        node_methods = node_item['properties'].get('statistics.methodCount', 'N/A')
        outgoing_refs = node_item['properties'].get('statistics.outgoingExternalReferenceTotal', 'N/A')
        incoming_refs = node_item['properties'].get('statistics.incomingExternalReferenceTotal', 'N/A')

        # Mark high complexity items
        complexity_str = str(node_complexity)
        if node_complexity not in ('N/A', None) and float(node_complexity) > 10:
            complexity_str = f"**{complexity_str}** ⚠️"

        nodes_table += f"| {name} | {node_type} | {complexity_str} | {node_instructions} | {node_methods} | {outgoing_refs} | {incoming_refs} |\n"

    # Format relationships in a more structured way for table display
    relationship_rows = []

    for rel in impact_data.get('data', {}).get('relationships', []):
        start_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['startId'])
        end_node = find_node_by_id(impact_data.get('data', {}).get('nodes', []), rel['endId'])

        if start_node and end_node:
            relationship_rows.append({
                "type": rel.get('type', 'UNKNOWN'),
                "source": start_node.get('name', 'Unknown'),
                "source_type": start_node.get('primaryLabel', 'Unknown'),
                "target": end_node.get('name', 'Unknown'),
                "target_type": end_node.get('primaryLabel', 'Unknown')
            })

    # Also keep the relationships grouped by type for reference
    relationships_by_type = {}
    for rel in relationships:
        rel_parts = rel.split(" (")
        if len(rel_parts) >= 2:
            source = rel_parts[0]
            rel_type = "(" + rel_parts[1]
            if rel_type not in relationships_by_type:
                relationships_by_type[rel_type] = []
            relationships_by_type[rel_type].append(source)

    # Build the markdown output
    impact_description = f"""# Impact Analysis for Method: `{method_name}`

## Guidelines for AI
- Pay special attention to methods with Cyclomatic Complexity over 10 as they represent higher risk
- Consider the cross-application dependencies when making changes
- Prioritize testing for components that directly depend on this method
- Suggest refactoring when complexity metrics indicate poor maintainability
- Consider the full relationship map to understand cascading impacts
- Highlight REST API endpoints and external dependencies that may be affected by changes

## Summary
- **Method**: `{method_name}`
- **Class**: `{class_key(chosen_identity).replace('|', '.') if chosen_identity else (class_name or 'N/A')}`
- **Materialized View**: `{mv_id}`
"""

    # Add code ownership information if available
    if code_owners:
        impact_description += f"- **Code Owners**: {', '.join(code_owners)}\n"
    if code_reviewers:
        impact_description += f"- **Code Reviewers**: {', '.join(code_reviewers)}\n"

    impact_description += f"- **Complexity**: {complexity}\n"
    impact_description += f"- **Instruction Count**: {instruction_count}\n"
    impact_description += f"- **Affected Applications**: {len(affected_applications)}\n"

    # LIN-870: where the impact came from and whether it is complete
    impact_description += "\n" + _impact_scope_section(impact_meta, outcome, chosen_identity, overloads)

    # Add affected REST endpoints to the Summary section
    if endpoint_nodes:
        impact_description += "\n### Affected REST Endpoints\n"
        for endpoint in endpoint_nodes:
            impact_description += f"- `{endpoint['http_verb']} {endpoint['path']}`\n"

    # Start the Risk Assessment section
    impact_description += "\n## Risk Assessment\n"

    # Add complexity risk assessment
    if complexity not in ('N/A', None) and float(complexity) > 10:
        impact_description += f"⚠️ **Warning**: Cyclomatic complexity of {complexity} exceeds threshold of 10\n\n"
    else:
        impact_description += "✅ Complexity is within acceptable limits\n\n"

    # Add cross-application risk assessment
    if len(affected_applications) > 1:
        impact_description += f"⚠️ **Cross-Application Dependency**: This method is used by {len(affected_applications)} applications:\n"
        for app in sorted(affected_applications):
            deps = app_dependencies.get(app, [])
            if deps:
                impact_description += f"- `{app}` (depends on: {', '.join([f'`{d}`' for d in deps])})\n"
            else:
                impact_description += f"- `{app}`\n"
        impact_description += "\nChanges to this method may cause widespread impacts across multiple applications. Consider careful testing across all affected systems.\n"
    else:
        impact_description += "✅ Method is used within a single application context\n"

    # Add REST API risk assessment (now as a subsection of Risk Assessment)
    if rest_endpoints or api_controllers or endpoint_nodes:
        impact_description += "\n### REST API Risk Assessment\n"
        impact_description += "⚠️ **API Impact Alert**: This method affects REST endpoints or API controllers\n"

        if rest_endpoints:
            impact_description += "\n#### REST Methods with Annotations\n"
            for endpoint in rest_endpoints:
                impact_description += f"- `{endpoint['name']}` ({endpoint['annotation']})\n"

        if api_controllers:
            impact_description += "\n#### Affected API Controllers\n"
            for controller in api_controllers:
                impact_description += f"- `{controller['name']}` ({controller['type']})\n"

        # Add endpoint dependencies as a subsection of Risk Assessment
        if endpoint_dependencies:
            impact_description += "\n### REST API Dependencies\n"
            impact_description += "⚠️ **Chained API Risk**: Changes may affect multiple interconnected endpoints\n\n"
            for dep in endpoint_dependencies:
                impact_description += f"- `{dep['source']}` depends on `{dep['target']}`\n"

        # Add API Change Risk Factors as a subsection of Risk Assessment
        impact_description += """
### API Change Risk Factors
- Changes may affect external consumers and services
- Consider versioning strategy for breaking changes
- API contract changes require thorough documentation
- Update API tests and client libraries as needed
- Consider backward compatibility requirements
- **Chained API calls**: Changes may have cascading effects across multiple endpoints
- **Cross-application impact**: API changes could affect dependent systems
"""
    else:
        impact_description += "\n### REST API Risk Assessment\n"
        impact_description += "✅ No direct impact on REST endpoints or API controllers detected\n"

    # Ownership-based consultation recommendation
    if code_owners or code_reviewers:
        impact_description += "\n### Code Ownership\n"
        if code_owners:
            impact_description += f"👤 **Code Owners**: Changes to this code should be reviewed by: {', '.join(code_owners)}\n"
        if code_reviewers:
            impact_description += f"👁️ **Preferred Reviewers**: Consider getting reviews from: {', '.join(code_reviewers)}\n"

        if code_owners:
            impact_description += "\nConsult with the code owners before making significant changes to ensure alignment with original design intent.\n"

    impact_description += f"""
## Method Impact
This analysis focuses on systems that depend on `{method_name}`. Modifying this method could affect these dependents:

"""

    if dependents:
        for dep in dependents:
            impact_description += f"- `{dep['name']}` ({dep['type']}) via `{dep['relationship']}`\n"
    elif impact_meta["status"] != "ok" or impact_meta["truncations"]:
        impact_description += "No direct dependents were returned, but the impact result is PARTIAL (see Impact scope) — this is not evidence of isolation.\n"
    else:
        impact_description += "No components directly depend on this method. The change appears to be isolated.\n"

    # Unresolved references are dependency indicators; section omitted when none
    unresolved_section = format_unresolved_references_section(unresolved_references)
    if unresolved_section:
        impact_description += f"\n{unresolved_section}"

    impact_description += f"\n## Detailed Node Metrics\n{nodes_table}\n"

    # Create relationship table
    relationship_table = "| Relationship Type | Source | Source Type | Target | Target Type |\n"
    relationship_table += "|------------------|--------|-------------|--------|------------|\n"

    for row in relationship_rows:
        # Highlight relationships involving our target method
        highlight = ""
        if (method_name.lower() in row["source"].lower() or method_name.lower() in row["target"].lower()):
            if simple_name and (simple_name in row["source"].lower() or simple_name in row["target"].lower()):
                highlight = "**"  # Bold the important relationships

        relationship_table += f"| {highlight}{row['type']}{highlight} | {highlight}{row['source']}{highlight} | {row['source_type']} | {highlight}{row['target']}{highlight} | {row['target_type']} |\n"

    impact_description += "\n## Relationship Map\n"
    impact_description += relationship_table

    # Add application dependency visualization if multiple applications are affected
    if len(affected_applications) > 1:
        impact_description += "\n## Application Dependency Graph\n"
        impact_description += "```\n"
        for app in sorted(affected_applications):
            deps = app_dependencies.get(app, [])
            if deps:
                impact_description += f"{app} → {' → '.join(deps)}\n"
            else:
                impact_description += f"{app} (no dependencies)\n"
        impact_description += "```\n"

    return [
        types.TextContent(
            type="text",
            text=impact_description,
        )
    ]
