# Knowledge lineage schema

`graph-view-v1` is defined separately by [graph-view.schema.json](../assets/schemas/graph-view.schema.json). It is a generated read model, not canonical lineage state. Its `revision` binds lineage state, `course_revision` binds course state, and `activation_edge_kind: prerequisite` prevents clients from treating semantic, containment, branch, scheduled, or citation edges as unlock rules.

## Contents

- Canonical files
- Revision model
- Annotation records
- Semantic relation records
- Conceptual thread records
- Import payload
- Events
- Query outputs
- Validation invariants

## Canonical files

The optional lineage subsystem stores canonical state under:

```text
.atomlearn/lineage/
├── state.yaml
├── map.yaml
└── events.ndjson
```

`KNOWLEDGE_LINEAGE.md` is a generated projection. Never edit it to mutate state.

`state.yaml` has exactly these fields:

```yaml
schema_version: 1
revision: 0
created_at: "2026-01-01T00:00:00Z"
updated_at: "2026-01-01T00:00:00Z"
```

`map.yaml` has exactly:

```yaml
schema_version: 1
revision: 0
annotations: []
relations: []
threads: []
```

## Revision model

Lineage revision is independent from the course revision. Each successful import increments lineage revision once and appends one event. The map and state revisions must match the number of valid events.

Use `--expected-lineage-revision` on imports. A stale expected value fails without changing canonical files.

## Annotation records

An annotation adds semantic function to an existing Atom:

```yaml
atom_id: calculus.derivative.definition
roles: [definition, principle]
central_question: How can instantaneous change be defined without dividing by zero?
contribution: Turns an interval rate into a local quantity through a limit.
boundaries:
  - The relevant limit may not exist.
```

Fields are exact:

- `atom_id`: stable Atom ID;
- `roles`: one to ten unique allowlisted roles;
- `central_question`: non-empty, at most 1,000 characters;
- `contribution`: non-empty, at most 1,000 characters;
- `boundaries`: up to 20 unique strings, each at most 1,000 characters.

Allowed roles are `foundation`, `motivation`, `definition`, `principle`, `mechanism`, `method`, `example`, `application`, `boundary`, `synthesis`, and `historical_milestone`.

Annotations use upsert semantics keyed by `atom_id`. Importing a later annotation for the same Atom replaces the prior canonical annotation while preserving the event history.

## Semantic relation records

A relation explains a conceptual connection:

```yaml
id: average-rate-motivates-derivative
from_atom_id: calculus.rate.average
to_atom_id: calculus.derivative.definition
type: motivates
rationale: Shrinking the interval motivates the derivative limit.
confidence: 0.95
source_refs:
  - source_id: calculus-notes
    locator: derivative definition
```

Fields are exact:

- `id`: unique stable lowercase ID;
- `from_atom_id` and `to_atom_id`: different, existing Atom IDs;
- `type`: allowlisted relation type;
- `rationale`: non-empty, at most 1,500 characters;
- `confidence`: number from `0.5` through `1.0`;
- `source_refs`: zero to 20 source references.

Allowed types are `motivates`, `defines`, `derives`, `generalizes`, `specializes`, `contrasts`, `analogous_to`, `extends`, `refines`, `supersedes`, `applies_to`, `implements`, `evaluates`, and `bridges`.

`contrasts`, `analogous_to`, and `bridges` are symmetric for duplicate detection. The other relation types are directional.

Each source reference contains exactly `source_id` and `locator`. `source_id` must be registered in the course or RAG source registry, or equal `synthesized`. A confidence above `0.7` requires at least one source reference.

Relation IDs and semantic signatures cannot be imported twice. To revise a material relation, create a new ID only after deciding how the superseded interpretation will be represented; this version does not silently overwrite relation history.

## Conceptual thread records

A thread is an ordered, learner-facing route:

```yaml
id: rate-to-derivative-application
title: From average rate to application
kind: problem_to_solution
goal: Explain why the derivative is needed and how it is used.
atom_ids:
  - calculus.rate.average
  - calculus.derivative.definition
  - calculus.derivative.compute-square
narrative: Refine average change with a limit, then apply the definition.
confidence: 0.9
```

Fields are exact. `atom_ids` must contain at least two unique, existing Atoms and at most 100 entries. The order is intentional but does not create prerequisite edges.

Allowed kinds are `learning_spine`, `problem_to_solution`, `derivation`, `comparison`, `application`, `historical`, `exam`, `research`, and `custom`.

`title` is limited to 500 characters, `goal` to 1,000, and `narrative` to 2,000. `confidence` ranges from `0.5` through `1.0`. Thread IDs are append-only and unique.

## Import payload

An import file contains exactly three lists:

```yaml
annotations: []
relations: []
threads: []
```

At least one list must be non-empty. Use `assets/templates/lineage-import.yaml` as a starting point:

```text
python <SKILL_DIR>/scripts/atomlearn.py lineage import <workspace> --input <payload.yaml> --expected-lineage-revision <revision>
```

The import is normalized and validated before canonical state changes. Unknown fields, unknown Atoms, malformed IDs, unsupported enums, duplicate relations, and ungrounded high-confidence relations fail closed.

## Events

Each NDJSON event has exactly:

```json
{"event_id":"levt-000001","revision":1,"type":"lineage.map_imported","at":"2026-01-01T00:00:00Z","course_revision":2,"details":{"annotation_atom_ids":[],"relation_ids":[],"thread_ids":[]}}
```

Event IDs and revisions are contiguous. `course_revision` records the course snapshot observed by that import. Details contain only unique string ID lists and do not duplicate semantic text.

## Query outputs

`overview` returns schema, lineage revision, course revision, selected lens, generation time, and lens-specific projections.

The structure projection includes roots, leaves, main learning spine, hubs, explicit branch and convergence points, module summaries, bridges, and topological order. The learning projection includes statuses and the Active Atom. The conceptual projection includes annotations, relations, threads, type counts, and annotation coverage. Exam and research projections identify whether those subsystems are enabled and expose their bounded overlays.

`trace` returns one resolved Atom, its main prerequisite path, upstream and downstream neighborhoods, annotation, touching semantic relations, containing threads, and optional exam/research records.

`route` returns `connected`, endpoint IDs, ordered Atom IDs, and typed steps. Prerequisite steps retain their canonical direction plus a `traversal` direction used by the route query.

## Validation invariants

- State and map fields are exact and use schema version 1.
- State/map revisions match and equal event count.
- Timestamps parse and event revisions are contiguous.
- All records normalize exactly to their schema.
- IDs and relation signatures are unique.
- Atoms resolve to active canonical IDs or valid aliases.
- Thread members remain resolvable after restructures.
- High-confidence relation provenance is present and registered.
- The underlying course workspace itself is valid and acyclic.
