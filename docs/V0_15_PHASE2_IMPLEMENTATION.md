# v0.15 Phase 2 Implementation Record

## Scope

Phase 2 implements Workstream B from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: task-form/evidence-dimension compatibility, immutable scorer profiles, Mastery Feasibility Preflight, multi-Evidence mastery, and conservative migration.

## Delivered contracts

- `task-form-compatibility.yaml` defines what single/multiple choice, numeric short answer, structured derivation, open explanation, critique, teach-back, concept map, novel application, and independently scored multi-part tasks can establish.
- Measurement item schema v2 requires task form, response mode, item family, novelty scope, supported dimensions, and scoring profile. Explicit incompatible declarations fail closed.
- Scorer registry schema v2 records identity class, immutable profile hash, artifact hashes, coverage, calibration/drift/review/abstention state, privacy, disabled state, and test-only status.
- Evidence v3 stores the task contract plus a hashed decision-relevant scorer snapshot. Validation does not consult a later registry to reinterpret historical Evidence.
- `eligible_dimensions` is the Atom/item/task/scorer intersection. Deterministic legacy choice items are conservatively restricted to recognition/discrimination.
- `measure feasibility` reports valid production paths, missing dimensions, scorer hashes, and diversity feasibility. `activate` rejects infeasible mastery claims.
- `assess` aggregates compatible qualified Evidence by dimension and records contributing task form, family, scorer/hash, and measurement window. Per-Atom policy can require multiple families/forms, delayed retention, and transfer.

## Compatibility and migration

- Evidence v1 still migrates idempotently to historical v2 with incomplete provenance and no new eligibility.
- Existing v2 remains readable and is validated under stored historical fields; scorer-v2 changes do not promote or demote it retroactively.
- Legacy measurement-bank items remain readable but use conservative inferred task contracts. New item integrations should emit schema v2.
- Older plans gain default `claim_mode: mastery` and a one-family/one-form evidence policy when imported. Existing persisted Atoms without those fields remain valid through defaults.

## Safety boundaries

- The bundled anchored scorer is test-only and never satisfies production feasibility.
- Unregistered, uncalibrated, disabled, non-independent, abstained, or disputed model scores cannot master an Atom.
- External/human reviewer identity remains asserted provenance; Core cannot authenticate a person or provider account.
- Engineering and calibration evidence still does not establish a causal learning benefit.

## Verification

Targeted regressions cover scorer/matrix versioning, choice-to-explanation rejection, conservative legacy deterministic grading, Evidence v3 profile snapshots, multi-item completion, production-open-scorer infeasibility, activation failure, model abstention/review exclusion, unqualified model exclusion, and legacy migration.

Final verification on Windows/Python 3.12:

- `python -m pytest -q`: **211 passed, 1 skipped** in 22:00; the skip is the existing optional `scale`/HNSW case.
- Skill Creator `quick_validate.py atom-learn`: passed.
- `python -m compileall -q atom-learn/scripts manager/atomlearn_manager release`: passed.
- wheel and sdist build: passed, with the task-form matrix and both new schemas included.
- README structure/code-block alignment, capability/core schemas, scorer profile hashes, release gate, and signed upgrade-path fixtures: passed.
