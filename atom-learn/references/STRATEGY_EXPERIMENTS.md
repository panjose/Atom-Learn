# Strategy experiments

Use strategy experiments only to test low-risk teaching presentation choices. They are separate from the user preference profile, workspace adaptation, course evolution, and all mastery or source-grounding state.

## Safety boundary

- Experiments are independently opt-in with `strategy enable-experiments`; enabling a global profile is not experiment consent.
- Only `explanation.order`, `example.mode`, `teaching.mode`, `feedback.style`, `check.style`, and `review.presentation` may vary.
- Experiments never change the Atom DAG, mastery rubrics, Evidence, skips, RAG, research scope, privacy, or safety rules.
- Current-turn, workspace, and global explicit preferences always win. An overridden episode is recorded but excluded from comparison.
- Candidates must cite existing opaque Evidence, Review, or Event IDs. Do not copy learner responses or source content into strategy state.

## Lifecycle

```text
candidate -> monitoring (shadow) -> monitoring (live) -> active
                         |                    |
                         +--------------------+-> paused / needs_review
```

Create the candidate after meaningful evidence exists. `start` always enters shadow mode. Record at least one shadow exposure, inspect its deterministic assignment, and only then run `set-live`. Shadow records describe what assignment would have occurred but never change teaching and never accept outcomes.

At the beginning of every matching Active Atom episode, run:

```text
atomlearn strategy exposure <workspace> <atom-id> --context teaching --episode-type new_learning --episode-key <opaque-key>
```

Reuse the same opaque episode key on retry. The returned exposure and instruction are immutable; do not switch arms during the episode. If the result says `assigned: false`, use Effective Policy normally. If status is `shadow` or `overridden`, use `chosen_value`, not `assigned_value`.

After the learner completes an actual check, record and assess ordinary Evidence first. Then bind it exactly once:

```text
atomlearn strategy record-outcome <workspace> <exposure-id> --evidence-id <evidence-id>
```

Only assessed Evidence for the exposed Atom is accepted. Review Evidence supplies delayed outcomes. Historical Evidence without an exposure is never backfilled.

## Monitoring and promotion

`strategy monitor` compares only strata containing both baseline and candidate outcomes. Context, Atom type, difficulty, prior diagnostic bucket, and episode type define a stratum. Promotion requires all preregistered data minimums, at least five distinct comparable Atoms, at least two delayed outcomes, complete metrics, zero hard-gate failures, no material quality or guardrail degradation, and improvement in a quality metric. Fewer attempts alone can never promote a candidate.

An active candidate enters Effective Policy as `user_strategy`, below all explicit preferences. A failing guardrail pauses the experiment without rewriting Evidence. `strategy pause <id>` immediately removes its overlay; `strategy pause` disables experiments and pauses all monitoring or active experiments. Use `strategy explain` for samples, metrics, limitations, precedence, and the experiment audit trail.

All mutations accept `--expected-strategy-revision`. Use `strategy validate` after manual recovery or a Core update. If an enum is no longer supported, monitoring moves the experiment to `needs_review` instead of applying it.
