# Evidence v3 and learning measurement

## Purpose

Evidence v3 binds every score to three independent contracts: the Atom's required dimension, the task form's supported dimension, and an immutable scorer profile. A score can enter mastery only when it belongs to their intersection. This prevents a correct selected option from being reused as proof of explanation, derivation, critique, or transfer.

Read this reference before creating mastery, retention, transfer, or strategy Evidence. Read [MASTERY.md](MASTERY.md) for Atom rubric and diversity policy design.

## Compatibility contract

The versioned matrix is `assets/task-form-compatibility.yaml`:

| Task form | Eligible dimensions |
| --- | --- |
| single/multiple choice | recognize, discriminate |
| numeric short answer | compute; apply only in a changed context |
| structured derivation | derive, compute |
| open explanation | explain, connect |
| critique | critique, evaluate |
| teach-back / concept map | explain, connect |
| novel application | apply, near/far transfer under novelty and holdout rules |
| multi-part | only dimensions with separately attributable rubric sections |

Every v2 measurement item declares `task_form`, `response_mode`, `item_family`, `novelty_scope`, `supported_dimensions`, and `scoring_profile_id`. Core computes:

```text
eligible_dimensions = Atom.required_dimensions
                    ∩ item.supported_dimensions
                    ∩ task_form.supported_dimensions
                    ∩ scorer.supported_dimensions
```

Anything outside this intersection is rejected for an explicit v2 item and cannot enter `required_dimension_scores`. Legacy v1 items are interpreted conservatively: incompatible dimensions are discarded, never promoted. In particular, legacy choice correctness can establish `discriminate` but not `explain`.

## Immutable scorer profiles

`assets/scorer-registry.yaml` is schema v2. A profile records provider class, method, implementation, supported languages/domains/forms/dimensions, prompt/rubric/parser/calibration hashes, calibrated metrics, abstention and review policies, validity, drift, disabled/test-only state, privacy class, and eligibility limits. Its `profile_hash` must match its canonical content.

Evidence v3 freezes the decision-relevant profile fields and the registry profile hash into `scorer_profile_snapshot`; `scorer_profile_hash` authenticates that snapshot. Validation uses the frozen snapshot, not today's registry. A later scorer release therefore cannot reinterpret historical Evidence. Human and model provenance remain distinct through `provider_class`.

The bundled anchored-model fixture is `test_only` and can test calibration machinery but cannot establish production feasibility or mastery. An unregistered/uncalibrated model can provide feedback only. Disagreement, abstention, low confidence, disabled profiles, and missing calibration remain ineligible until the declared review path resolves them.

## Mastery Feasibility Preflight

Run before activating a course or after changing its rubric/scorer configuration:

```text
atomlearn measure feasibility <workspace>
```

The report lists each Atom's required dimensions, production-eligible task/scorer paths, missing dimensions, scorer hashes, and evidence-diversity feasibility. `activate` fails closed for an infeasible mastery Atom. Repair it by adding a valid task/scorer, narrowing the claim, or setting `mastery.claim_mode: reading|exploration`; never create a mastery course whose required Evidence cannot exist.

Courses may restrict production profiles with `course.settings.scorer_profile_ids`. Absence of a production open-response path makes `explain` infeasible even if a test fixture exists.

## Multi-Evidence mastery

One item no longer has to cover every Atom dimension. `assess` aggregates only assessed, mastery-eligible Evidence per required dimension. The final report retains the contributing Evidence ID, task form, item family, scorer identity/hash, and measurement window. Mastery also requires the Atom thresholds and its `evidence_policy`:

- `minimum_item_families` and `minimum_task_forms` prevent one copied score from filling high-risk claims;
- `delayed_check_required` requires delayed-retention Evidence;
- `transfer_check_required` requires a near- or far-transfer window;
- far transfer requires a held-out cross-domain task.

Until every required dimension and policy condition passes, the Atom stays Active. Missing, disputed, low-confidence, or ineligible Evidence does not disappear; it remains auditable as partial/not-mastered feedback.

## Measurement kinds and holdouts

- `immediate_mastery`: a new check close to teaching time;
- `delayed_retention`: a held-out check after a declared delay, with Evidence `kind: review`;
- `near_transfer`: a held-out variant in a changed context;
- `far_transfer`: a held-out cross-domain application.

Retention and transfer items must be `held_out` and `context_isolated`. Never expose the answer, equivalent worked solution, or hidden rubric in the same episode.

## Deterministic and external scoring

Use `atomlearn measure grade --input <file>` to preview exact-choice or numeric/unit scoring, or provide the same item and response under `grading_input` to `record-evidence`. Core recomputes the result, hashes but does not persist the raw response, and rejects conflicting item identity or task contracts.

External, dual, and human Evidence requires stable item/episode IDs, a registered profile and rubric, required calibration, independent provenance, a local SHA-256 answer hash, an explicit task contract, and bounded scores. Model-assessed Evidence must also declare `abstain`, `review_required`, and `confidence`; abstention, review requests, or confidence below the frozen threshold cannot master the Atom. Backward-compatible human/dual payloads without task fields are conservatively treated as a multi-part legacy contract; new integrations should always declare the fields.

## Item banks, calibration, and claim boundaries

```text
atomlearn measure task-forms
atomlearn measure registry
atomlearn measure validate-bank --input measurement-bank.yaml
atomlearn measure calibrate --input calibration-set.yaml --output calibration-report.json
atomlearn measure validate-protocol
```

Calibration reports remain deterministic and stratified, but calibration is not a learning-effect result. Only an independently consented, controlled study with delayed and transfer outcomes may support a learning-gain claim.

## Migration

Evidence v1 migrates idempotently to historical v2 with explicit incomplete provenance and no new mastery/strategy eligibility. Existing v2 stays v2 and is validated under its frozen historical rules; it is never silently upgraded or reinterpreted by scorer-v2.

```text
atomlearn migrate-evidence <workspace> --confirmed --expected-revision <revision>
atomlearn validate <workspace>
```
