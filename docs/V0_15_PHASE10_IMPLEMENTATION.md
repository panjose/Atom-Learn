# AtomLearn v0.15 Phase 10 Implementation

Phase 10 adds the stable `graph-view-v1` boundary for optional interactive knowledge-map clients.

## Delivered

- `lineage graph-view` exports a schema-validated, UI-agnostic read model with lineage and course revisions.
- Nodes expose Atom or paper kind, label, status, module/role, optional state, and focus state.
- Edges are typed as `prerequisite`, `containment`, `scheduled-successor`, `optional-branch`, `citation`, or `semantic-related`.
- Required, optional, and research filters are explicit. Research paper nodes and citation edges are disabled unless requested.
- `activation_edge_kind` is fixed to `prerequisite`; semantic or visual clients cannot turn a relation into an unlock rule.
- `lineage interactive` writes a dependency-free standalone HTML adapter with search, focus, edge filtering, and node inspection. It consumes the exported payload and never mutates canonical course or lineage state.
- Existing `KNOWLEDGE_LINEAGE.md`, `overview`, `trace`, and `route` remain the stable Markdown/CLI fallback.

## Verification

- Graph schema validation covers required fields, revisions, node identity, edge kinds, endpoints, and focus flags.
- Integration tests cover all six edge kinds, research overlays, filtering, read-only behavior, and standalone adapter output.
- Existing lineage, documentation, CLI contract, and fast suites remain compatible.

## Boundary

The adapter is intentionally not a browser-dependent product surface. It contains no external scripts, no network calls, and no course mutation API. A future frontend may consume `graph-view-v1`, but it must preserve the prerequisite-only activation boundary and Markdown fallback.
