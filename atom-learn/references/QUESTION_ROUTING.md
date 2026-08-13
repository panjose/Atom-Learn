# Question routing

## Contents

- Routing decision
- Categories
- Border cases
- Persistence rules

## Routing decision

Before answering, determine what concept the learner is asking about, whether it belongs to the Active Atom, whether it is necessary now, whether an existing Atom owns it, and what state action preserves the mainline. Save the classification and a short rationale.

## Categories

### `in_atom`

Use when the question directly explores the current objective or one of its misconceptions. Answer now, keep the same Active Atom, and update the current question/confusion.

### `blocking_prerequisite`

Use when the learner lacks a concept required to reason about the current Atom. Record the question, identify an existing prerequisite Atom, and backtrack. If no Atom exists, propose adding one before switching.

### `non_blocking`

Use when the question is useful but not needed for the current objective. Add it to the Parking Lot, acknowledge it briefly, and return to the saved next action.

### `future_atom`

Use when a mapped successor owns the question. Link it to that Atom and explain why answering fully now would depend on the current Atom. Avoid introducing the successor's mechanism.

### `out_of_scope`

Use when the question does not serve the declared course goal. Record it only if the learner wants it preserved. Ask before expanding scope.

## Border cases

- A definition mentioned inside an explanation is not automatically blocking; backtrack only if the learner cannot continue without it.
- A real-world application can be `in_atom` when it is the planned example, otherwise it is often `non_blocking`.
- A future-topic question can expose a current misconception. Route by the actual bottleneck, not surface vocabulary.
- If uncertain between blocking and non-blocking, ask one diagnostic question while keeping the current Atom.

## Persistence rules

Keep the original wording. Store `related_atom_id`, classification, rationale, priority, status, and timestamps. Resolve a question only after answering it or explicitly deciding it no longer matters. Never drop parked questions when advancing.
