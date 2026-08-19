# Knowledge lineage workflow

## Contents

- Purpose
- Two graph layers
- Initialize and inspect
- Build semantic context
- Query the map
- Export graph-view-v1
- Apply the lenses
- Keep it current
- Quality and safety rules

## Purpose

A learner asking to "梳理知识脉络" usually needs more than a chapter list. They need to see:

- what the field is trying to explain or solve;
- which ideas are foundations and which are consequences;
- why a definition or method appears at a particular point;
- how one result is derived, generalized, contrasted, or applied;
- which route matters for their current learning, exam, or paper-reading goal.

AtomLearn answers these questions with a multi-lens lineage map. The structural layer is automatic; the semantic layer is source-grounded and editable. Both reuse the existing Knowledge Atoms and learner state.

## Two graph layers

The prerequisite DAG remains the authority for learning order. Its edges require a mastery-like state or an explicit provisional skip before a successor can be activated. The latter is a disclosed traversal assumption, not understanding. Lineage analysis derives roots, leaves, topological order, the longest learning spine, hubs, module boundaries, and cross-module bridges from this DAG.

Detailed expansion adds a separate containment view: `parent_atom_id` and `graph.expansions` show which fine-grained Atoms belong inside a parent objective, while the derived prerequisite chain controls their teaching order. Lineage reports these trees separately so containment is not mistaken for a semantic or prerequisite relation.

Relation-aware routing adds a third structural projection for learner-chosen `optional_extension` branches. `graph.branches` and lineage `optional_branches` connect each optional Atom to its anchor and origin question without treating it as required completion work or detailed-expansion containment.

Semantic relations explain conceptual meaning without changing activation rules. Supported relation types are:

- `motivates`: an earlier problem or limitation creates the need for the target;
- `defines`: the source establishes the target's definition;
- `derives`: the target follows through a derivation;
- `generalizes` and `specializes`: scope expands or narrows;
- `contrasts` and `analogous_to`: comparison relations;
- `extends`, `refines`, and `supersedes`: intellectual or historical development;
- `applies_to` and `implements`: use and realization relations;
- `evaluates`: the source provides an assessment criterion;
- `bridges`: a meaningful cross-module or cross-tradition connection.

Semantic relations never unlock Atoms, satisfy prerequisites, or alter mastery. If evidence suggests a true prerequisite change, use the confirmed `restructure` workflow or an approved evolution proposal.

## Initialize and inspect

Import a valid course plan before initializing lineage:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage init <workspace>
python <SKILL_DIR>/scripts/atomlearn.py lineage overview <workspace> --lens structure
```

Initialization creates `.atomlearn/lineage/state.yaml`, `map.yaml`, and `events.ndjson`, then renders `KNOWLEDGE_LINEAGE.md`. Structural output is useful immediately, even when no semantic record exists.

Read the structural overview before curating the map. Check:

- roots that introduce the field;
- leaves that represent current endpoints or outcomes;
- the main learning spine;
- high-degree hubs where several dependencies meet or branch;
- cross-module prerequisite edges;
- unexpectedly isolated or overly central Atoms.

The longest spine is descriptive, not automatically the best route for every learner.

## Build semantic context

Read [LINEAGE_SCHEMA.md](LINEAGE_SCHEMA.md) and start from `assets/templates/lineage-import.yaml`. Add only useful semantics; do not annotate every Atom with generic prose.

For an important Atom, record:

- one or more roles such as `foundation`, `motivation`, `definition`, `principle`, `mechanism`, `method`, `example`, `application`, `boundary`, `synthesis`, or `historical_milestone`;
- the central question it answers;
- its distinctive contribution;
- boundaries that prevent overgeneralization.

Then add relations and curated threads. A thread is a purposeful ordered explanation, not necessarily a prerequisite path. Typical threads include:

- problem to solution;
- definition to derivation;
- comparison of competing approaches;
- historical development or replacement of ideas;
- foundation to application;
- exam-focused reasoning chain;
- research-paper concept repair chain.

Use RAG to retrieve evidence for non-obvious relations. A relation with confidence above `0.7` must have at least one source reference. Use `synthesized` only when the statement is explicitly an analyst synthesis rather than a direct source claim, and keep its confidence calibrated.

```text
python <SKILL_DIR>/scripts/atomlearn.py rag search <workspace> --input <lineage-query.yaml>
python <SKILL_DIR>/scripts/atomlearn.py lineage import <workspace> --input <lineage-import.yaml> --expected-lineage-revision <revision>
python <SKILL_DIR>/scripts/atomlearn.py lineage validate <workspace>
```

## Query the map

Use `overview` for the field-level picture:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage overview <workspace> --lens all
```

Use `trace` when the learner asks for one concept's "来龙去脉":

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage trace <workspace> <atom-id> --depth 3
```

The trace returns a longest prerequisite chain ending at the target, bounded upstream and downstream neighborhoods, its semantic annotation, touching relations, curated threads, and any exam or research overlay.

Use `route` when the learner asks how two concepts connect:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage route <workspace> <from-atom-id> <to-atom-id>
```

The shortest route may traverse prerequisite or semantic edges. Every step exposes its relation type and traversal direction. Explain reverse prerequisite traversal as navigation only; it does not reverse the learning dependency.

## Export graph-view-v1

Use `graph-view` when a UI or harness needs a stable read model:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage graph-view <workspace> --focus atom-current
python <SKILL_DIR>/scripts/atomlearn.py lineage graph-view <workspace> --hide-optional --include-research
```

The result is validated against [graph-view.schema.json](../assets/schemas/graph-view.schema.json). It keeps `prerequisite`, `containment`, `scheduled-successor`, `optional-branch`, `citation`, and `semantic-related` edges distinct. Required and optional Atom filters are independent, research papers are excluded by default, and `activation_edge_kind` is always `prerequisite`.

The optional adapter writes a standalone HTML view with no external runtime dependency:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage interactive <workspace> --include-research
```

It supports search, focus, edge filtering, and node inspection but does not own state or change course/lineage revisions. `KNOWLEDGE_LINEAGE.md`, `overview`, `trace`, and `route` remain the stable fallback.

## Apply the lenses

`--lens structure` shows the prerequisite architecture. Use it for orientation and course diagnosis.

`--lens learning` overlays current status, available, review-due, skipped, and deferred Atoms, the Active Atom, and status along the main spine. Use it to turn a map into a next action and to make provisional assumptions visible.

`--lens conceptual` shows annotations, relation counts, grounded semantic edges, and curated threads. Use it to explain why ideas appear and how schools, methods, or applications connect.

`--lens exam` overlays sample-contained Atom emphasis and unmapped coverage gaps from the exam subsystem. Use `trace` on a high-priority target before teaching it, so required foundations and conceptual context remain visible.

`--lens research` shows which Atoms are required by mapped papers and how many papers depend on each. Use it to build a concept-repair route without losing the Active Paper.

`--lens all` combines the lenses for generated documentation. Do not overwhelm a learner with the entire payload when one trace or thread answers the question.

## Keep it current

Course, lineage, exam, research, and adaptation revisions are independent. A course restructure may leave lineage references resolvable through aliases; validation rejects references that resolve only to missing or archived Atoms.

After course structure changes:

1. run root `validate`;
2. inspect `lineage status`;
3. replace obsolete annotations with a new import for the same Atom ID;
4. add new relations or threads with new stable IDs;
5. render the view again.

Relation and thread IDs are append-only within lineage import history. An annotation is an upsert keyed by Atom ID. Pass `--expected-lineage-revision` to prevent concurrent lost updates.

## Quality and safety rules

- Keep prerequisites and semantic relations distinct.
- Ground high-confidence semantic claims in registered course or RAG sources.
- Store bounded rationale and locators, not large source passages.
- Use the learner's requested lens and goal; a global map is not always the clearest answer.
- Label corpus emphasis as exam-sample evidence, not prediction.
- Label paper demand as mapped-reading demand, not field-wide importance.
- Preserve uncertainty and competing interpretations through confidence, contrasts, and boundaries.
- Never infer mastery from seeing a map or following a route.
- Validate after every import and after any structural recovery.
