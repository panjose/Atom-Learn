# Question routing

## Contents

- Routing decision
- Categories
- Border cases
- Persistence rules

## Routing decision

Before answering, determine what concept the learner is asking about, whether it belongs to the Active Atom, whether it is necessary now, whether an existing Atom owns it, and what state action preserves the mainline. Save the classification and a short rationale.

Also determine whether “explain in detail” still asks for one teachable move. If it requires multiple independently checkable ideas inside the current objective, treat it as a detailed expansion request rather than answering all parts as one `in_atom` response. Read [DETAILED_EXPANSION.md](DETAILED_EXPANSION.md).

## Categories

### `in_atom`

Use when the question directly explores the current objective or one of its misconceptions. Answer now, keep the same Active Atom, and update the current question/confusion.

### `blocking_prerequisite`

Use when the learner lacks a concept required to reason about the current Atom. Record the question, identify an existing prerequisite Atom, and backtrack. If no Atom exists, propose adding one before switching.

### `non_blocking`

Use when the question is useful but not needed for the current objective. Add it to the Parking Lot, acknowledge it briefly, and return to the saved next action.

If it names a distinct concept that could become a learner-chosen side branch, use [CONCEPT_ROUTING.md](CONCEPT_ROUTING.md) instead of treating it as a generic non-blocking question.

### `future_atom`

Use when a mapped successor owns the question. Link it to that Atom and explain why answering fully now would depend on the current Atom. Avoid introducing the successor's mechanism.

### `optional_extension`

Use when a distinct related concept supports the learner's goal but is unnecessary for the current mastery objective. Run `route-concept` so the learner sees that it is optional and can choose definition-only context, a confirmed side branch, parking, or dismissal.

### `out_of_scope`

Use when the question does not serve the declared course goal. Record it only if the learner wants it preserved. Ask before expanding scope.

## Border cases

- A definition mentioned inside an explanation is not automatically blocking; backtrack only if the learner cannot continue without it.
- A real-world application can be `in_atom` when it is the planned example, otherwise it is often `non_blocking`.
- A future-topic question can expose a current misconception. Route by the actual bottleneck, not surface vocabulary.
- If uncertain between blocking and non-blocking, ask one diagnostic question while keeping the current Atom.
- When the learner first needs to know whether an unfamiliar concept belongs before, now, later, or on a side branch, preview `route-concept` and show its relationship card before explaining more.
- A request for more words is not automatically an expansion; a request that crosses multiple Atom-sized objectives is.

## Persistence rules

Keep the original wording. Store `related_atom_id`, classification, rationale, priority, status, and timestamps. Resolve a question only after answering it or explicitly deciding it no longer matters. Never drop parked questions when advancing.
