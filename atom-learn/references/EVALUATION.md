# Evolution evaluation

## Contents

- Evaluation rule
- Supported metrics
- Criteria schema
- Interpretation
- Promotion and rollback

## Evaluation rule

Evaluate learning outcomes without weakening correctness. Never promote a proposal merely because an Atom completed faster.

Hard gates must remain zero:

- workspace validation errors;
- multiple Active Atoms;
- mastered Atoms without Evidence;
- runtime raw-message storage.

After hard gates pass, evaluate retention, mastery quality, misconception recurrence, and learning effort.

## Supported metrics

Atom metrics:

- `atom.average_score`
- `atom.attempts`
- `atom.evidence_count`
- `atom.mastery_failures`
- `atom.review_failures`
- `atom.dimension_spread`
- `atom.blocking_questions_as_current`
- `atom.parked_questions`

Course metrics:

- `course.mastered_ratio`
- `course.skipped_atoms`
- `course.skipped_ratio`
- `course.deferred_atoms`
- `course.open_questions`
- `course.parked_questions`
- `course.pending_reviews`
- `course.total_evidence`

System metrics:

- `system.workspace_validation_errors`
- `system.active_atom_count`
- `system.mastered_without_evidence`

## Criteria schema

```yaml
evaluation:
  success_criteria:
    - metric: atom.average_score
      atom_id: calculus.derivative.definition
      operator: gte
      value: 0.8
      min_observations: 2
    - metric: system.workspace_validation_errors
      operator: eq
      value: 0
```

Supported operators are `gte`, `lte`, `gt`, `lt`, and `eq`.

Return `insufficient` until every criterion has enough observations. Return `passed` only when every evaluated criterion passes. A failed result recommends review or rollback but never triggers destructive automatic rollback.

## Interpretation

Historical replay can verify state integrity and detect regressions, but it cannot prove a counterfactual teaching benefit. Prefer shadow observation and new delayed Evidence for learning claims.

Do not tune a proposal against the same fixture used to generate it. Use separate examples or later learner interactions to evaluate generalization.

## Promotion and rollback

Promote only after all success criteria pass and protected invariants remain intact. Store baseline metrics, current metrics, individual criterion results, and course revision in an experiment record.

Rollback immediately only when no learning mutation occurred after application. Otherwise produce a compensating proposal that preserves newer learning state.
