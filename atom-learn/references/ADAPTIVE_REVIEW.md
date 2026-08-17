# Per-Atom adaptive review

## Product boundary

AtomLearn schedules concepts, explanations, applications, and transfer checks—not isolated flashcards. The adaptive adapter therefore consumes normalized Knowledge Atom review events and does not translate answer speed or a card-style button directly into a learning claim.

The default remains `fixed`: successful reviews use the course's `review_intervals_days`, normally 1, 3, 7, and 30 days. `adaptive-shadow` computes per-Atom memory and an alternative date while leaving the real queue fixed. `adaptive-active` changes future dates only after the bundled benchmark passes and the learner explicitly sets `active_opt_in: true`. Changing policy never rewrites existing pending reviews or historical due dates.

## Qualified review events

Only assessed Evidence with all of these properties updates memory:

- `kind: review` and `measurement_kind: delayed_retention`;
- an A/B quality, mastery-eligible scorer;
- `review_observation.retrieval_mode: active_recall`;
- `review_observation.delayed: true`;
- required-dimension scores produced through the normal Active Atom Evidence path.

Every new review is still normalized into an audit event and linked back from Evidence through `review_event_id`. Recognition, passive rereading, legacy/unqualified scoring, and observations with missing context retain their ineligibility reasons but cannot change stability, difficulty, retrievability, or the suggested date. The normalized event records correctness, the minimum required-dimension score, hint count, delayed status, a response-time bucket, and scorer quality. Validation re-derives these fields and qualification reasons from the linked Evidence. Response time is auxiliary only: it is not an input to the memory update and can never independently extend an interval.

Use the observation template as a block inside a normal review Evidence payload:

```yaml
review_observation:
  retrieval_mode: active_recall
  hint_count: 0
  delayed: true
  response_time_seconds: 90
```

## Memory state and adapter

Qualified events maintain one state per Atom:

```yaml
scheduler: adaptive
stability_days: 6.2
retrievability: 1.0
difficulty: 5.4
desired_retention: 0.90
last_qualified_review_at: '2026-08-16T00:00:00+00:00'
model_version: atomlearn-memory-v1
qualified_event_count: 3
suggested_interval_days: 6
suggested_due_at: '2026-08-22T00:00:00+00:00'
```

`atomlearn-memory-v1` is a deterministic DSR/FSRS-like adapter spike. It models stability, difficulty, and retrievability, uses a forgetting curve whose stability is the point at which retrievability reaches 90%, increases stability after qualified recall, and shortens it after failure. It does not claim to implement a particular FSRS release or to have learned personalized parameters from insufficient history. The verified defaults remain explicit and fixed remains available at all times.

The adapter boundary follows the open FSRS project's published D/S/R concepts and its warning that optimization needs review history; AtomLearn adds stricter Atom-specific event qualification rather than copying flashcard ratings. See the [open scheduler overview](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler), [algorithm description](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm), and [official tutorial](https://github.com/open-spaced-repetition/fsrs4anki/blob/main/docs/tutorial.md).

## Configure and operate

Start in shadow mode:

```powershell
atomlearn review benchmark courses/calculus --expected-revision 7
atomlearn review configure courses/calculus --input atom-learn/assets/templates/review-policy.yaml --expected-revision 8
atomlearn review status courses/calculus
```

The policy schema is `assets/schemas/review-policy.schema.json`. Desired retention is bounded to 0.70–0.97. An `exam` objective requires a target date and clamps adaptive recommendations to its final-review window; a `long_term` objective has no target date.

To enable active scheduling, first inspect a current passing benchmark result and then submit a policy containing both `mode: adaptive-active` and `active_opt_in: true`. A stale or changed benchmark profile fails closed. Active scheduling applies only to reviews created afterward.

The benchmark is an engineering gate over versioned deterministic fixtures: Brier score, successful interval monotonicity, failure shortening, bounded states, and response-time invariance. It is not evidence of better retention. `review pilot` replays qualified history against fixed and shadow dates, reports data sufficiency and limitations, and always returns `promotion_allowed: false`. Any learning-effect claim requires the separate consented study workflow.

## Unified daily queue

Build a read-only daily queue with:

```powershell
atomlearn review queue courses/calculus --date 2026-08-16 --minutes 60 --cognitive-load 10
```

The queue considers failure remediation, due and overdue reviews, blocking prerequisites, eligible new Atoms, and initialized exam practice. It fits tasks within time and cognitive-load capacity. When capacity is insufficient it returns an explicit backlog and `behind_schedule: true`; it never marks Evidence, mastery, or a review complete and never deletes overdue history. Blocking prerequisites and failure remediation outrank new material.

## Safety and interpretation

- Keep `fixed` as the default and fallback.
- Never infer an active-retrieval event from reading time, chat duration, satisfaction, or self-report.
- Do not rewrite pending reviews when policy changes.
- Do not call a passing engineering benchmark a learning-effect result.
- Do not call a one-workspace observational replay causal evidence.
- Use `study` with explicit consent and delayed/transfer outcomes before making a bounded comparative learning claim.
