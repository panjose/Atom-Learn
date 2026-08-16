# Evidence v2 and learning measurement

## Purpose

Evidence v2 separates an observed learner response from the authority used to score it. A numeric score is not trusted merely because a harness or model supplied it. Core records the measurement kind, item and episode identity, grader profile, rubric and calibration versions, independence claim, answer hash, required-dimension projection, quality tier, and eligibility decisions.

Read this reference before creating mastery, retention, transfer, or teaching-strategy Evidence. Continue to use [MASTERY.md](MASTERY.md) for Atom-specific rubric design.

## Quality and eligibility

The bundled scorer registry is `assets/scorer-registry.yaml` and is validated by `assets/schemas/scorer-registry.schema.json`.

| Tier | Typical source | May master | May enter a strategy outcome |
| --- | --- | --- | --- |
| A | Core deterministic exact-choice or numeric/unit scorer | Yes | Yes |
| B | Registered calibrated anchored scorer, independent dual review, or declared human adjudication | If its registry profile and independence/calibration gates pass | If the same gates pass |
| C | Unregistered, uncalibrated, or non-independent external scorer | No | No |
| legacy | Historical score without reconstructable provenance | Historical state only | No |

An old-shaped payload submitted now is stored as explicit unqualified legacy Evidence and cannot independently master an Atom. Migration may preserve an already-mastered historical result so the workspace remains recoverable, but it never makes that record eligible for a new strategy experiment.

`assess` reads only `required_dimension_scores`, which Core derives from the Active Atom's current mastery dimensions. Extra presentation or fluency scores cannot inflate mastery or a strategy outcome.

## Measurement kinds

- `immediate_mastery`: a new check close to teaching time;
- `delayed_retention`: a held-out check after a declared delay, persisted with Evidence `kind: review`;
- `near_transfer`: a held-out variant that changes a limited part of the context;
- `far_transfer`: a held-out task requiring the concept in a meaningfully different context.

Transfer and delayed-retention items must be `held_out` and `context_isolated`. Do not expose their answer, equivalent worked solution, or hidden rubric during the same teaching episode. If the harness cannot maintain this boundary, record the result as ineligible feedback rather than qualified Evidence.

## Deterministic Evidence

Use `atomlearn measure grade --input <file>` to preview a deterministic grade, or place the same `item` and `response` under `grading_input` when calling `record-evidence`. Core recomputes the scores, takes the item ID and answer hash from that result, and rejects a conflicting claimed item ID. The raw response is not copied to canonical Evidence.

The exact-choice scorer normalizes surrounding whitespace and optionally case. The numeric/unit scorer checks a finite number against the declared absolute or relative tolerance and requires the canonical unit or an explicit alias. It does not prove that a derivation is sound, so use an appropriate open-response rubric when reasoning steps are themselves required.

All new Evidence also requires a stable, opaque `episode_id`. Deterministic Evidence must declare `assessment.independent: true`; Core itself performs the scorer and ignores caller-supplied scores.

## External, dual, and human Evidence

External scoring must provide:

- a non-empty `measurement_item_id` and `episode_id`;
- an assessment method, registered `grader_id`, and registered `rubric_version`;
- the exact registered `calibration_set_version` when that method requires calibration;
- `independent: true` when claiming qualified Evidence;
- a local SHA-256 hash of the raw response, never a fabricated answer hash;
- bounded scores for every required Atom dimension.

The registry contains fixture profiles for reproducible tests; their presence is not a claim that an arbitrary model is calibrated. Adding a production scorer requires a reviewed registry change, a versioned held-out calibration set, and a reproducible report. Repeating the same model call does not create a dual-blind evaluator.

## Item banks

Validate a bank with:

```text
atomlearn measure validate-bank --input measurement-bank.yaml
```

Each item declares its Atom family, measurement kind, required dimensions, prompt, scorer and rubric, answer specification, holdout family and visibility, retention delay, language, domain, and difficulty. Keep calibration examples separate from learning-effect test items. Item-family separation is required to reduce memorization leakage between teaching, calibration, immediate checks, retention, and transfer.

## Open-response calibration

Run:

```text
atomlearn measure calibrate --input calibration-set.yaml --output calibration-report.json
```

The report is deterministic for identical input and includes dataset hash, sample/scored/abstain counts, a distinct human-review-required rate, MAE, signed bias, tolerance agreement, per-dimension metrics, multilingual/domain/difficulty/length strata, pass/fail confusion, thresholds, and qualification reasons. An optional prior report summary produces signed metric deltas, a maximum absolute drift, and a qualification-blocking drift threshold without importing raw calibration answers. Output creation is exclusive: an existing report is never overwritten.

A calibration report measures agreement with reference scores on a declared distribution. It does not establish that a strategy improves learning and it does not authorize a different model, prompt, rubric, or calibration-set version.

The bundled `assets/benchmarks/calibration-open-v1.yaml` is a deliberately small engineering fixture for the anchored scorer contract. Its committed `.report.json` must reproduce value-for-value through the CLI. It proves report determinism and gate behavior only; its four artificial examples are not production model validation.

## Three benchmark layers

`assets/learning-benchmark-protocol.yaml` keeps claims separated:

1. engineering tests show that schemas, state transitions, graders, privacy boundaries, and recovery behave correctly;
2. calibration tests show performance against known answers or human annotations on declared strata;
3. only an independently consented learning-effect study with a control condition, delayed measurements, transfer items, missing-data reporting, and uncertainty intervals may support a learning-gain claim.

The minimum learning-effect measures are immediate mastery, 7-day and 30-day retention, near transfer, and far transfer. Research data is separately opt-in, minimized, withdrawable, and keeps raw answers local by default. Engineering or calibration success alone must never be described as proof that learners learn better.

## Migration and audit

Preview workspace status first, then explicitly migrate unmigrated historical Evidence:

```text
atomlearn status <workspace> --json
atomlearn migrate-evidence <workspace> --confirmed --expected-revision <revision>
atomlearn validate <workspace>
```

Migration adds legacy provenance, a required-dimension projection, and strategy exclusion without changing historical scores or results. It is idempotent. Preserve the event log and never rewrite a legacy record as calibrated Evidence after the fact.
