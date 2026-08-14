# Bounded self-evolution

## Contents

- Principle
- Two evolution lanes
- When to evolve
- Workflow
- Proposal lifecycle
- Evolution types
- Application and rollback
- Skill-level evolution

## Principle

Treat self-evolution as evidence-grounded proposal generation, validation, and controlled promotion. Never treat it as permission to rewrite production behavior directly.

Keep two independent revisions:

- course revision protects learning state;
- evolution revision protects hypotheses, proposals, policy, and experiments.

Require both revisions to be current before applying a proposal. Route every course mutation through the normal Workspace commit and validation path.

## Two evolution lanes

Keep session adaptation separate from bounded course evolution:

- use `adapt` for allowlisted presentation preferences distilled from chat sessions;
- use `evolve` for teaching-policy experiments, mastery, reviews, dependencies, Atom structure, and Skill candidates.

Read [SESSION_ADAPTATION.md](SESSION_ADAPTATION.md) before extracting chat preference signals. Session adaptation has its own revision so frequent low-risk preference updates do not stale pending evolution proposals. It may automatically apply explicit preferences and corroborated cross-session inferences, but it cannot mutate course or evolution state.

Exam analysis is also revision-isolated. Its corpus emphasis may reorder a generated preparation queue, but it cannot directly change mastery rubrics, prerequisite edges, review policy, or Atom structure. Record learner attempts as ordinary Evidence; only subsequent outcome evidence may support a bounded evolution proposal.

## When to evolve

Run analysis after meaningful evidence accumulates, such as repeated mastery failure, delayed review failure, repeated blocking backtracks, or an explicit learner request. Do not analyze after every conversational turn.

Use:

```text
python <SKILL_DIR>/scripts/atomlearn.py evolve analyze <workspace> --propose
```

The analyzer stores derived metrics and ID references, not raw learner messages. It may read adaptation session and active-preference counts, but it does not copy chat signals into evolution state. It may generate:

- hypotheses based on deterministic signals;
- ready low- or medium-risk proposals when the change is structurally complete;
- incomplete structural proposals that require semantic Atom design.

Never invent observation IDs. A proposal without real Evidence, Question, Review, or Event references must state that it is a learner-requested design change.

## Workflow

1. Run `evolve status` and note both revisions.
2. Run `evolve analyze`; add `--propose` only when proposal generation is useful.
3. Inspect metrics, signals, and hypotheses.
4. Create or refine a proposal using the schema below.
5. Run `evolve preview` and explain the change, risk, stale status, and validation errors.
6. Obtain the required authority and run `evolve approve`.
7. Run `evolve apply` only when the proposal is ready and its base course revision is current.
8. Gather new learning Evidence without optimizing for the proposal's expected answer.
9. Run `evolve monitor` after the declared minimum observations.
10. Keep a promoted proposal or run `evolve rollback` when safe.

Default to `proposal_only`. Do not add a type to `auto_apply_types` merely for convenience.

## Proposal lifecycle

```text
proposed -> approved -> applied -> monitoring -> promoted
    |          |          |            |
    +----------+----------+------------+-> rejected / rolled_back / blocked
```

Use a proposal like:

```yaml
type: adjust_mastery
scope: learner
target_atom_ids: [calculus.derivative.definition]
observations: [ev-000014, ev-000017]
hypothesis: Delayed review failure indicates that transfer evidence is missing.
change:
  atom_id: calculus.derivative.definition
  required_dimensions: [explain, apply, discriminate, transfer]
evaluation:
  success_criteria:
    - metric: atom.average_score
      atom_id: calculus.derivative.definition
      operator: gte
      value: 0.8
      min_observations: 2
```

The engine derives risk from type. Do not let proposal input lower its own risk.

## Evolution types

### `teaching_strategy`

Change the learner-specific strategy policy without changing course revision. Store an Atom-specific strategy or bounded default. Require explicit approval in proposal-only mode.

### `adjust_review_intervals`

Change future review intervals within policy bounds. Do not rewrite completed Evidence or pretend that an earlier review happened on a new schedule.

### `adjust_mastery`

Change required dimensions or thresholds within policy bounds. If a previously mastered Atom needs new evidence, make it available for revalidation and retain all historical Evidence.

### `add_dependency` / `remove_dependency`

Change one prerequisite edge. Reject missing endpoints, duplicate edges, and cycles. When a new prerequisite invalidates previous mastery assumptions, require revalidation rather than deleting history.

### `split_atom` / `merge_atoms`

Embed a complete existing `restructure` proposal under `change.proposal`. Require learner confirmation. Preserve aliases, Evidence, questions, and archived Atom files.

### `patch_skill`

Record a high-risk candidate summary for repository review. Maintainer approval may acknowledge the candidate, but runtime application is always forbidden. Implement it only through a normal repository patch, tests, official Skill validation, and explicit release decision.

## Application and rollback

Create a checkpoint before application. Keep checkpoint data inside the course workspace and exclude raw learner messages. Store only the canonical structures and link fields needed to reverse the proposal.

Allow automatic rollback only when course revision still equals the proposal's applied revision. If learning continued, create a new compensating migration proposal instead of restoring an old snapshot over newer Evidence.

Never permanently delete Atoms introduced by a structural proposal. Archive them with a rollback reason.

## Skill-level evolution

For `patch_skill`:

1. Collect raw failure artifacts outside the runtime proposal when authorized.
2. Generate a candidate repository patch.
3. Add or update tests that reproduce the failure.
4. Run the complete regression suite and official Skill validator.
5. Compare behavior on independent fixtures.
6. Request maintainer review.
7. Promote through a new Git commit; never overwrite the stable Skill from a course workspace.
