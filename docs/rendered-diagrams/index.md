# Rendered architecture diagrams

Committed SVG renders of every Mermaid diagram in
[../architecture-diagrams.md](../architecture-diagrams.md), one per section,
rendered with the digest-pinned `mermaid-cli` container
(`ghcr.io/mermaid-js/mermaid-cli/mermaid-cli@sha256:062edb08dcc7f95841c15620241b6934af93aa75c27f223ebe2e81fd0b4da4c9`).
Added for Linear ticket **LIN-737**; diagrams 01–05 re-rendered for **LIN-699**
(PR #61 / neo4cape #1860). If you edit a diagram, re-render its SVG so the two
stay in sync.

| # | Diagram | SVG |
|---|---------|-----|
| 01 | Ecosystem: where lineai-mcp-server sits | [01-ecosystem-context.svg](01-ecosystem-context.svg) |
| 02 | Module map | [02-module-map.svg](02-module-map.svg) |
| 03 | `lineai-method-impact` call sequence (with error branches) | [03-method-impact-sequence.svg](03-method-impact-sequence.svg) |
| 04 | Authentication and the four caches | [04-auth-and-caching.svg](04-auth-and-caching.svg) |
| 05 | Error-handling taxonomy (honest since LIN-699 / PR #61) | [05-error-taxonomy.svg](05-error-taxonomy.svg) |
