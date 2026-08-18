# Incremental Episode Checkpoints

## Purpose

Episode checkpoints make a meaningful Active Atom interaction resumable without treating chat-session termination as a reliable event. They are a workspace-local observability layer, not course truth, mastery Evidence, a strategy outcome, or telemetry.

Observability is default-off. `episode status` is read-only and does not initialize storage. Enable it only after the learner opts in:

```text
atomlearn episode status <workspace>
atomlearn episode enable <workspace> --expected-observability-revision 0
```

The first enable records the exact workspace revision where coverage starts. Historical strategy exposures, outcomes, or chat sessions are never backfilled. Status therefore reports a coverage boundary instead of implying continuous observation.

## Lifecycle

Use a stable opaque episode key for one Active Atom interaction. The key is hashed into a workspace-local episode ID and is not persisted.

```text
atomlearn episode begin <workspace> <atom-id> --episode-key <opaque-key> --request-key <request-key> --expected-observability-revision <revision> --expected-workspace-revision <revision>
atomlearn episode checkpoint <workspace> <episode-id> --event exposure_recorded --exposure-ref <exposure-id> --request-key <request-key> --expected-observability-revision <revision> --expected-workspace-revision <revision>
atomlearn episode checkpoint <workspace> <episode-id> --event strategy_applied --teaching-mode <mode> --request-key <request-key> --expected-observability-revision <revision> --expected-workspace-revision <revision>
atomlearn episode checkpoint <workspace> <episode-id> --event teaching_step --interaction-pattern <pattern> --request-key <request-key> --expected-observability-revision <revision> --expected-workspace-revision <revision>
atomlearn episode checkpoint <workspace> <episode-id> --event evidence_attempted --attempt-status <status> --request-key <request-key> --expected-observability-revision <revision> --expected-workspace-revision <revision>
```

`outcome_recorded` requires the opaque strategy-outcome ID and matching assessed, strategy-qualified Evidence. The Evidence Atom and its original opaque episode key must hash to the checkpoint episode. This checkpoint records that the harness observed a real strategy outcome; it does not create or replace that outcome.

Finish with one explicit mode:

- `strategy_outcome_recorded`: a validated outcome checkpoint exists;
- `assessment_only`: matching assessed Evidence exists but no strategy outcome is asserted;
- `no_outcome`: the interaction ended without an outcome.

Every finalize response declares `strategy_promotion_input: false`. Strategy promotion continues to consume only the independent, preregistered `strategy record-outcome` ledger. An incomplete or no-outcome episode therefore cannot become a strategy sample merely because an episode checkpoint exists.

## Sudden close, resume, and retry

Every mutation requires the current observability revision and, when it observes course state, the current workspace revision. `episode resume` succeeds only when:

- the episode is still `incomplete`;
- observability remains enabled;
- the recorded Atom is still the Active Atom;
- the workspace revision exactly matches the last checkpoint.

If course state changed without a corresponding checkpoint, resume fails closed. Inspect the episode, reconcile it through a new valid checkpoint when the same Atom remains active, finalize it without an outcome, or retire it. Never guess across a revision gap.

`begin`, `checkpoint`, `resume`, and `finalize` accept stable request keys. Retrying the same request and payload returns the prior record without incrementing the observability revision. Reusing a key with different fields fails. This makes harness tool retries idempotent while preserving stale-revision protection for genuinely different operations.

## Privacy and user control

The schema stores only opaque references, fixed enums, revisions, timestamps, and lifecycle status. It has no fields for raw messages, learner answers, quotations, prompts, free-text profiles, personal identifiers, or sensitive-trait inference.

```text
atomlearn episode inspect <workspace> <episode-id>
atomlearn episode retire <workspace> <episode-id> --reason privacy_request --expected-observability-revision <revision>
atomlearn episode disable <workspace> --expected-observability-revision <revision>
```

Retirement removes an episode from coverage summaries while preserving its local audit record. Disable stops all new observation and resume mutations without destroying existing records. The user can still inspect or retire preserved history.

## Coverage and validation

`episode status` reports observed episode count and exposure, teaching, evidence-attempt, outcome, resume, finalization, and incomplete-without-outcome coverage. These are harness integration diagnostics only. They do not show that a model followed the teaching protocol or that a learner benefited.

Run `episode validate` or the workspace-level `validate`. Validation checks the strict schema, one-incomplete-episode invariant, opaque workspace identity, monotonic workspace revisions, idempotency-key uniqueness, finalization consistency, and Evidence references.

Episode state is the declared `workspace_episodes` compatibility namespace. `migrate plan|validate --workspace <workspace>` and the trusted Manager state-copy catalog discover it at `.atomlearn/episodes/state.yaml`. There is no fabricated migration for pre-observability history; schema v1 starts only at explicit opt-in.
