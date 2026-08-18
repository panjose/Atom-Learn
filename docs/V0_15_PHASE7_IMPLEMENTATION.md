# v0.15 Phase 7 implementation record

## Scope

Phase 7 implements Workstream G from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: review-gated hybrid exam mapping, provenance-qualified difficulty, and a revisioned exam calendar that can react to learning and planning changes. It does not predict future exam content, infer learner ability from item difficulty, or weaken prerequisite and mastery rules to make a calendar fit.

## Hybrid mapping and review boundary

Automatic processing still combines the question stem, linked answer, and marking rubric through deterministic lexical evidence. The exam process contract now accepts `semantic_mapping: auto|required|off`:

- `auto` admits semantic evidence only when workspace RAG has a learned or provider embedding profile and a current benchmark-approved reranker; otherwise it records a typed lexical fallback;
- `required` checks that same gate before any exam mutation and fails closed when it is unavailable;
- `off` makes the deterministic lexical-only decision explicit.

Approved retrieval is constrained to Atom source references. Each semantic contribution retains the RAG revision, source ID and revision, chunk ID, Document IR block ID, exact locator, runtime profile, reranker profile, and benchmark ID. Hybrid candidates expose lexical and semantic score components, supporting evidence, opposing candidates, and the source revision used. Retrieval scores are candidate-ranking evidence, not confidence or truth.

Every automatic mapping is `pending` regardless of score or separation. Only `exam review-mappings` can confirm, correct, explicitly unmap, or reject it. Pending and rejected candidates never count as exam-to-course coverage. Legacy mappings remain readable and are normalized conservatively.

## Difficulty provenance

The three difficulty concepts remain independent:

- `structural_complexity` is the five-factor heuristic and any calibrated estimate derived from it;
- `official_difficulty` is usable only after `exam record-official` records a reviewed level, source, exact locator, and reviewer ID;
- `empirical_difficulty` is usable only with at least 30 attempts, a named population, a complete observation window, source, and exact locator.

Old `official_level` fields without provenance are preserved for compatibility but downgraded to structural evidence. Calibration uses only qualified official anchors and leaves both anchors and original structural factors intact. Empirical records that miss a threshold remain visible with explicit qualification reasons; they do not silently become the effective difficulty.

The two new strict payload contracts are `exam-official-difficulty.schema.json` and the extended `exam-empirical-difficulty.schema.json`, with matching YAML templates.

## Revisioned canonical schedule

`exam daily-plan` remains a read-only preview. `exam replan` requires the payload target to equal the canonical exam target (or directs the user through `exam set-target`) and creates `.atomlearn/exam/schedule.yaml`, governed by `exam-schedule-state.schema.json`, with an independent schedule revision, exact exam/course revision binding, capacity-contract hash, task and day assignments, carried completion, day outcomes, invalidation reasons, and a `replanned` or `infeasible` audit event.

`exam record-day` accepts the strict `exam-day-outcome.schema.json` contract. A completed day must name all planned tasks, a partial day names the completed subset, and a missed day names none. Exact revision checks prevent a late client from changing a newer plan.

`exam plan-status` is read-only. It derives `due` and `overdue` outputs and classifies the schedule as `uninitialized`, `current`, `stale`, or `infeasible`. Every schedule mutation also refreshes the canonical schedule, due/overdue state, day assignments, and unscheduled gap in `EXAM_STUDY_PLAN.md`, so the non-adapter workflow remains complete. It marks the plan stale after:

- new mastery/retention Evidence or review state;
- a missed, partial, availability-changed, or unrecorded past day;
- a target date changed through `exam set-target`, a changed corpus, mapping review, difficulty review, or other exam revision;
- a revoked skip, inserted prerequisite/backtrack, graph change, or course-plan revision.

Completion carries into a new plan only for time and capacity changes. Evidence, course-structure, mapping, difficulty, or corpus changes regenerate the queue without assuming that an earlier completion still discharges the new requirement. An infeasible plan persists its unmet tasks and minute gap without lowering mastery. External reminder integrations can subscribe to Core output; no notification service is required for the CLI/Markdown workflow.

`workspace_exam_schedule` is now a declared Core state namespace and appears in both Core and trusted Manager migration/state-copy catalogs. Legacy workspaces have no invented schedule: `plan-status` stays read-only until the first explicit replan.

## Compatibility and safety

- Exam, course, RAG, and schedule revisions remain independent and are cross-bound only by recorded snapshots.
- Existing structured imports and lexical processing stay available without semantic dependencies.
- Provider/learned vectors and rerankers remain optional; no model is downloaded and no private source is sent externally by this phase.
- Official-difficulty reviewer IDs are strict audit provenance, but Core cannot authenticate an asserted human identity.
- Canonical question state still stores concise summaries and locators rather than full stems, solutions, or marking text.
- Item-family review and held-out transfer-risk boundaries are unchanged.
- All writes use existing workspace locking and atomic replacement; status and preview operations do not initialize state.

## Verification

Automated coverage includes:

- lexical fallback and required-semantic fail-closed behavior;
- admitted hybrid candidates retaining RAG/source/Document IR provenance;
- all automatic mappings staying review-only;
- official-anchor provenance and empirical population/window thresholds;
- missed-day detection and exact-revision replanning;
- infeasible-plan persistence without mastery relaxation;
- mapping-review, difficulty, corpus, course, and new-Evidence invalidation;
- schedule schema, migration namespace, manager state-copy, CLI/help, package, and bilingual documentation contracts.

Final local release validation:

- fast contract suite: 69 passed;
- full integration suite: 166 passed and one optional HNSW test skipped because the `scale` dependency set is not installed;
- current exam regression suite: 20 passed, followed by 3 date-object/missed-day/infeasible/Evidence schedule regressions after the final YAML normalization fix;
- all 48 JSON Schemas are valid Draft 2020-12 schemas;
- both `atom-learn` and Manager bridge Skills pass the Skill Creator validator, and the release capability gate passes with 435 lines in `SKILL.md`;
- wheel and sdist builds succeed and contain the schedule runtime, all three new schemas, and both new templates;
- a clean system-site virtual environment installs the final wheel, imports a question corpus, accepts the unquoted-date daily-plan template, persists schedule revision 1, reports `freshness: current`, and renders the canonical Markdown schedule.

These are engineering checks only; they do not establish learning benefit or future-exam prediction accuracy.
