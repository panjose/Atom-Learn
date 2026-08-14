# Relation-aware concept routing

Use this workflow when the current explanation mentions a concept the learner does not understand, or when the learner asks how a related concept fits into the course. The goal is to answer the navigation question before adding more content.

## Contents

- Learner-facing contract
- Five relations
- Classification procedure
- Relationship card
- Action matrix
- CLI payload and commands
- Required prerequisite insertion
- Optional branches
- Teaching boundaries
- Interaction with expansion and RAG
- Uncertainty and safety rules

## Learner-facing contract

Every unfamiliar related concept gets a visible answer to four questions:

1. What is its relationship to the Active Atom?
2. Does not knowing it block the current objective?
3. If it is already planned, where and when will it be learned?
4. Which actions are available without losing the current learning position?

Show a compact card before changing the path:

```text
Relation: Required prerequisite
Why: The current derivation uses this operation and cannot be followed without it.
Effect: It blocks this Atom; learning it will temporarily pause the current Atom.
Destination: vector.dot-product (proposed or existing)
Recommended: Learn the prerequisite, then return automatically.
Choices: Learn first | Quick diagnostic
```

Translate labels into the learner's language, but preserve canonical relation and action IDs in state.

When an adjacent technical term is unavoidable and likely unfamiliar, its first mention may carry a small inline cue such as `（后续 Atom 会讲）`, `（若不熟，这是必要前置）`, or `（可选拓展）`. Do not label ordinary vocabulary or turn every sentence into navigation chrome. If the learner asks, replace the cue with the full relationship card.

## Five relations

### `inside_current`

The concept is a term, boundary, example, or local move already owned by the Active Atom. Explain one focused move now and keep the same Active Atom.

Use this only when the answer does not introduce another independently checkable objective. If two or more objectives are needed, use detailed expansion instead.

### `required_prerequisite`

The learner cannot explain, apply, or discriminate the Active Atom without this concept. It is blocking even if the original course map omitted it.

Recommend learning the prerequisite. A quick diagnostic is the alternative when the learner may already know it. Both actions preserve a return frame to the interrupted Atom.

### `scheduled_successor`

An existing mapped Atom owns the concept and is intended for later study. Show its title, module, current status, and prerequisites. Park the question by default and do not teach its mechanism early.

Definition-only context is allowed when the current sentence would otherwise be unreadable. State that the complete treatment remains scheduled later.

### `optional_extension`

The concept helps with history, intuition, a special case, an application, or broader context, but is not required by the current mastery rubric. It never silently becomes required work.

Offer four actions: definition-only context, add an optional branch, park it, or dismiss it. Adding the branch requires confirmation.

### `out_of_scope`

The concept does not support the declared course goal. Offer to park or dismiss it. A later explicit goal change may reclassify it, but the current question alone does not expand scope.

## Classification procedure

Apply these checks in order:

1. Restate the Active Atom's objective and mastery dimensions.
2. Ask whether the concept is merely vocabulary or a local step inside that objective. If yes, use `inside_current`.
3. Ask whether a learner can satisfy every current mastery dimension without understanding it. If no, use `required_prerequisite`.
4. Search the current Atom DAG and aliases. If an existing later Atom owns it, use `scheduled_successor`.
5. Ask whether it materially supports the goal without being necessary. If yes, use `optional_extension`.
6. Otherwise use `out_of_scope`.

Surface vocabulary is not enough. A question about a later term can reveal a genuine current prerequisite gap; a term mentioned in the current explanation can still be optional.

If uncertain between required and non-blocking, ask one diagnostic question about what the learner needs to do with the concept. Keep the Active Atom and DAG unchanged until the answer resolves the distinction.

## Relationship card

The `route-concept` preview returns:

- `concept` and canonical `relation`;
- a learner-readable `label`;
- `why`, copied from the routing rationale;
- `blocking` and the effect on progress;
- an existing or proposed destination Atom;
- a deterministic recommended action;
- all permitted choices and whether they require confirmation;
- a response contract limiting how much to explain now.

Preview is read-only. It does not increment the course revision, create a Question, or modify the DAG.

## Action matrix

| Relation | Recommended | Other choices | Path mutation |
| --- | --- | --- | --- |
| `inside_current` | `explain_now` | none | no |
| `required_prerequisite` | `learn_prerequisite` | `diagnose_prerequisite` | yes, confirmed |
| `scheduled_successor` | `park` | `brief_context` | no |
| `optional_extension` | `brief_context` | `add_optional_branch`, `park`, `dismiss` | branch only |
| `out_of_scope` | `park` | `dismiss` | no |

`brief_context` means one definition plus one sentence describing the relationship. It is not permission to teach the mechanism or enumerate a new concept chain.

## CLI payload and commands

Start from `assets/templates/concept-route.yaml`. Required fields are `text`, `concept`, `relation`, and `rationale`.

Use `related_atom_id` for an existing destination. Use `new_atom` for a proposed prerequisite or optional branch. `required_prerequisite` and `optional_extension` require exactly one of these fields. `scheduled_successor` requires an existing Atom. `inside_current` and `out_of_scope` accept neither.

```yaml
text: "Why does this use a Jacobian?"
concept: Jacobian
relation: required_prerequisite
rationale: "The current change-of-variables step cannot be applied without interpreting the Jacobian."
new_atom:
  id: calculus.jacobian.interpretation
  title: Interpreting a Jacobian
  objective: Explain what the Jacobian measures in a change of variables.
  difficulty: 2
  estimated_minutes: 20
```

Preview and then apply:

```text
python <SKILL_DIR>/scripts/atomlearn.py route-concept <workspace> --input <route.yaml>
python <SKILL_DIR>/scripts/atomlearn.py route-concept <workspace> --input <route.yaml> --action learn_prerequisite --confirmed --expected-revision <revision>
```

Structural actions require `--confirmed`. Non-structural actions still persist a routed Question so the decision is recoverable across sessions.

## Required prerequisite insertion

For an existing Atom, the runtime rejects downstream targets that would create a cycle. The target must be available for remediation; if it is locked, repair its own prerequisites first.

For a new Atom, the runtime:

1. inherits the Active Atom's module and source locators unless the payload narrows them;
2. gives the new Atom the Active Atom's currently satisfied prerequisites;
3. adds the new Atom as a direct prerequisite of the Active Atom;
4. records a `blocking_prerequisite` Question with routing metadata;
5. pushes the interrupted Atom onto the backtrack stack;
6. activates the prerequisite for teaching or diagnosis.

After prerequisite mastery, run `resume`. The original Atom, question, and next action are restored, and the blocking Question is resolved.

## Optional branches

An optional branch Atom stores:

```yaml
optional: true
branch:
  kind: optional_extension
  anchor_atom_id: calculus.derivative.definition
  origin_question_id: q-000004
  created_at: "2026-08-14T10:00:00+00:00"
```

The branch depends on its anchor, appears in `graph.branches`, and is labeled `[optional branch]` in the learning map. Knowledge lineage reports optional branches separately from detailed-expansion containment.

Optional branches:

- do not count toward required course completion;
- rank after required new work in ordinary recommendations;
- may be activated later by learner choice;
- may themselves be atomically expanded after they exist;
- cannot be silently converted into a required prerequisite.

Split and merge refuse Atoms participating in an optional branch so the anchor and origin Question cannot be orphaned.

## Teaching boundaries

Use the response contract returned by preview:

- `inside_current`: answer only the requested local boundary;
- `required_prerequisite`: explain why it blocks, then teach only the prerequisite Atom;
- `scheduled_successor`: name the planned location and avoid its mechanism;
- `optional_extension`: ask the learner whether to branch before expanding it;
- `out_of_scope`: protect the declared goal and offer parking.

Do not list several new terms as an answer. If answering the routed concept exposes another unfamiliar term, route that term independently from the currently Active Atom.

Prefer progressive disclosure: an inline cue on first mention, a relationship card when questioned, definition-only context when chosen, and a full Atom only after the appropriate current/prerequisite/optional action.

## Interaction with expansion and RAG

A detailed-expansion child can discover a missing external prerequisite. Insert it with `route-concept`; the expansion and backtrack stacks remain distinct. After remediation, resume the same child before moving to later siblings.

An expanded parent or child may have additional routed prerequisites. The mandatory child sequence remains present, but validation allows these explicit extra prerequisite edges.

Before creating a new Atom, retrieve evidence for its boundary and relationship. Reuse the Active Atom's sources when they support the concept. If they do not, run the normal RAG corrective-search loop and attach bounded, source-traceable evidence. Do not use Web Search merely to make an optional tangent larger.

## Uncertainty and safety rules

- Never infer `required_prerequisite` only because a term sounds technical.
- Never classify an already mapped successor as a prerequisite if that creates a cycle.
- Never create a new prerequisite or optional branch during preview.
- Never claim that a parked future concept was mastered.
- Never let optional work block course completion.
- Never hide the fact that the path changed.
- Persist the original question wording, rationale, chosen action, impact, timestamps, and related Atom when one exists.
