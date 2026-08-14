# Evolution policy

## Contents

- Default mode
- Risk and authority
- Bounds
- Protected invariants
- Privacy

## Default mode

Keep `mode: proposal_only` until real usage demonstrates that a specific low-risk type is consistently safe. Proposal-only mode requires explicit approval for every application, including teaching-strategy changes.

`bounded_auto` is reserved for a future release. The current engine rejects non-empty `auto_apply_types` and still requires explicit preview, approval, and application. Add automatic application only after independent evaluation demonstrates that a narrowly scoped low-risk type is safe.

## Risk and authority

| Type | Risk | Minimum authority | Runtime application |
| --- | --- | --- | --- |
| Teaching strategy | low | learner | allowed after approval |
| Review intervals | low | learner | allowed within bounds |
| Mastery rubric | medium | learner | allowed with revalidation |
| Dependency edge | medium | learner | allowed after DAG validation |
| Atom split/merge | medium | learner | allowed through restructure guards |
| Skill patch | high | maintainer | forbidden |

Session presentation preferences are governed separately by the adaptation policy. They may activate without an evolution proposal only when the dimension and value are allowlisted, context-scoped, reversible, and unable to change course invariants. Explicit current-turn instructions always take precedence.

Risk is derived from proposal type and cannot be supplied or lowered by the proposer.

## Bounds

Enforce policy bounds before approval and immediately before application:

- review intervals: positive, unique, increasing, and within configured minimum/maximum days;
- mastery thresholds: within configured range;
- minimum dimension score: within configured range;
- analysis signals: meet `minimum_observations` unless explicitly authored by the learner.

Treat a bound change as a policy change requiring maintainer review outside the course runtime.

## Protected invariants

Never evolve away these invariants:

- exactly one Active Atom;
- prerequisites before activation;
- mastery requires persisted Evidence;
- sources and locators are preserved;
- history is archived rather than erased;
- runtime Skill patching is forbidden.

Reject a proposal if normal Workspace validation fails after applying it in memory.

## Privacy

Keep `store_raw_messages: false` and `cross_workspace_aggregation: false` by default.

Keep session adaptation's `infer_sensitive_traits: false`. Do not infer identity, health, politics, religion, personality, intelligence, disability, or other sensitive traits from chat behavior. Store enum-only presentation signals with opaque turn references; never copy the message or a free-text summary.

Metrics may store:

- counts and averages;
- Evidence, Question, Review, and Event IDs;
- Atom IDs and titles already present in the workspace;
- derived signals and proposal rationale.

Do not duplicate question text, full learner answers, source passages, secrets, or personal identifiers into evolution metrics or checkpoints. Cross-workspace learning requires a separate opt-in aggregation design.
