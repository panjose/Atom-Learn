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

Only assessed Evidence for the exposed Atom with `strategy_eligible: true` is accepted. Strategy scoring reads the persisted `required_dimension_scores`, never caller-added auxiliary dimensions. Legacy, unregistered, uncalibrated, and non-independent Evidence is rejected even if its numeric scores pass the mastery threshold. Review Evidence supplies delayed outcomes. Historical Evidence without an exposure is never backfilled. See [MEASUREMENT.md](MEASUREMENT.md).

## Monitoring and promotion

`strategy monitor` compares only eligible outcomes in strata containing both baseline and candidate arms. Context, Atom type, difficulty, prior diagnostic bucket, and episode type define a stratum. Every candidate preregisters its learning, process, UX, and guardrail metrics; delayed or transfer learning must be primary. The v2 floor is at least 10 comparable outcomes per arm, 20 distinct episodes, and five qualified delayed-retention outcomes per arm. A larger design may declare stricter floors.

The analysis uses a fixed seed, analysis version, confidence level, bootstrap count, and maximum per-arm window. It reports deterministic stratified bootstrap intervals. Promotion requires the lower 95% interval bound of every primary learning effect to exceed the preregistered minimum effect, while every adverse guardrail interval upper bound stays within tolerance. A sufficiently precise adverse learning or guardrail interval pauses early. Reaching the fixed window without a qualifying learning effect rejects the candidate. Wide intervals, small samples, missing delayed outcomes, process improvements, faster completion, lower prompt counts, overrides, and satisfaction can never promote on their own.

Run `strategy replay-shadow <id>` before live assignment to verify every immutable shadow assignment. `set-live` performs the same replay and stores its hash. Analysis is deterministic for unchanged records. Outcomes must match the exposure's Atom and episode, a preregistered measurement kind, an allowed A/B quality tier, and an allowed grader. `strategy migrate-v2 --confirmed` conservatively moves v1 experiments to `needs_review`, clears legacy overlays, and marks historical outcomes ineligible; migration never upgrades old evidence quality.

An active candidate enters Effective Policy as `user_strategy`, below all explicit preferences. A failing guardrail pauses the experiment without rewriting Evidence. `strategy pause <id>` immediately removes its overlay; `strategy pause` disables experiments and pauses all monitoring or active experiments. Use `strategy explain` for samples, metrics, limitations, precedence, and the experiment audit trail.

All mutations accept `--expected-strategy-revision`. Use `strategy validate` after manual recovery or a Core update. If an enum is no longer supported, monitoring moves the experiment to `needs_review` instead of applying it.

This operational comparison chooses a bounded presentation policy for one opted-in learner. It is not a universal causal claim. A product learning-gain claim requires the independent consent and minimized-data contract in [LEARNING_EFFECT_STUDY.md](LEARNING_EFFECT_STUDY.md).
