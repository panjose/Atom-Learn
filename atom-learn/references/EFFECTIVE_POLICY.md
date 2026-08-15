# Effective Policy merge

Compute presentation behavior through one deterministic merger. Do not let individual teaching, review, research, or exam consumers reimplement precedence.

```text
atomlearn policy effective <workspace> --context teaching
atomlearn policy explain <workspace> explanation.order --context teaching
```

Pass current-turn overrides through a YAML/JSON mapping when a caller needs a machine-readable merge. Do not persist those overrides automatically.

## Precedence

For one dimension, apply:

1. protected learning, grounding, privacy, and safety invariants;
2. current-turn explicit request;
3. workspace explicit preference;
4. user global explicit preference;
5. workspace inferred preference;
6. user global inferred preference;
7. promoted course strategy;
8. promoted user strategy;
9. Core default.

An outcome experiment cannot override an explicit preference. A current-turn request can override stored presentation style but cannot introduce fields such as `mastery.threshold`, `skip`, `source_grounding`, or another protected invariant.

## Output

Every decision includes `value`, `source`, and `source_revision`. The result also includes ignored candidates with stable reason codes, enforced invariants, context-filtered instructions, and a deterministic policy fingerprint. Use that fingerprint in experiment exposures and diagnostics.

Only dimensions allowed in the requested context participate. For example, `research.orientation` is excluded from teaching and reported as `context_not_allowed`. Provisional, contested, retired, incompatible, and forbidden profile values never become effective.

`adapt guidance` and root `status --json` expose a backward-compatible summary plus the same Effective Policy. Prefer the `effective_policy` object when implementing new consumers.
