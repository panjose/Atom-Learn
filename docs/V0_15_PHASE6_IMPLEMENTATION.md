# v0.15 Phase 6 implementation record

## Scope

Phase 6 implements Workstream F from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: incremental, privacy-bounded episode observability and a versioned harness/model behavior evaluation protocol. It does not claim that a harness is already verified or that AtomLearn improves learning outcomes.

## Incremental episode checkpoint

The new `episode` subsystem is default-off and workspace-local. Once explicitly enabled, the harness can checkpoint activation, strategy exposure, applied teaching mode, teaching steps, Evidence attempts, strategy outcomes, review events, resume, and finalization at the moment each transition occurs. The canonical state is one atomically replaced schema-valid file, so a missing session-end hook cannot erase earlier checkpoints.

Each operation binds the independent observability revision and the observed workspace revision. A stable opaque request key makes exact retries replay the existing checkpoint; using the same key with different fields is rejected. Resume requires the same incomplete episode, the same Active Atom, and an exact last-checkpoint workspace revision. A state change after the checkpoint therefore fails closed instead of reconstructing chat context by guesswork.

Coverage begins at the first opt-in revision. Old strategy records are never backfilled. Status reports exposure, teaching, Evidence-attempt, outcome, resume, finalization, and incomplete-without-outcome coverage, and always labels these values as harness observability—not mastery, strategy outcomes, model compliance, or learning benefit.

The strict schema has no raw-message, answer, quotation, prompt, free-text profile, personal-identifier, or sensitive-trait field. Users can inspect or retire a record and can disable all new observation. Retirement preserves the local audit record but excludes it from coverage.

`workspace_episodes` is declared in the Core compatibility manifest and in both Core and trusted Manager state-copy catalogs. Migration planning and validation discover the state, while legacy sessions remain absent rather than receiving invented exposure or outcome records.

## Strategy boundary

Episode outcomes are pointers to already assessed strategy-qualified Evidence and an asserted strategy outcome ID. The episode subsystem never writes `strategy-outcomes.ndjson` and every finalization response returns `strategy_promotion_input: false`. Strategy monitoring continues to count only independently recorded, preregistered outcomes. Incomplete and no-outcome episodes therefore cannot inflate promotion samples.

## Harness/model behavior protocol

The bundled behavior protocol contains 18 cases: nine protocol categories in both English and Chinese. It covers single-Atom focus, child expansion and parent integration, related-concept routing, flexible progression and resume, exam answer holdback, research claim locators, stale revisions, retry/idempotency, and grading abstention.

The run schema records exact model/harness/prompt versions, temperature, seed, language, timestamps, trace hashes, and structured rubric ratings. It rejects raw model output fields. Deterministic engineering runs require one deterministic annotation and can never exceed `engineering_smoke_only`. Model compatibility runs require two distinct human annotations for every case, mandatory adjudication on disagreement, full bilingual coverage, and all protocol thresholds.

The generated report separates protocol adherence, Atoms added per turn, future-knowledge leakage, state correctness, citation support, resume success, abstention quality, and exact reviewer agreement. `pass` applies only to the exact recorded configuration. Every report forbids learning-effect claims, and no synthetic fixture changes the release capability ledger from `harness_behavior: not_evaluated`.

## Verification

Automated coverage includes:

- read-only default-off status and explicit coverage-start boundary;
- sudden close followed by exact resume;
- idempotent repeated begin, checkpoint, and resume calls;
- idempotent replay of an already committed begin or finalization even after the workspace advances;
- fail-closed resume after an uncheckpointed workspace revision;
- no-outcome finalization remaining outside strategy promotion;
- strict enum-only state, lifecycle validation, and workspace-level validation;
- bilingual protocol completeness and schema validation;
- deterministic smoke claim containment;
- dual-human compatible reports, reviewer composition, disagreement adjudication, and threshold gating;
- rejection of raw-output fields and learning-effect claim escalation.

Final local release validation:

- fast suite: 69 passed;
- full integration suite: 162 passed, one optional HNSW test skipped because the `scale` dependency set is not installed;
- final Episode regression suite: 3 passed, including post-commit workspace-advance retries;
- both `atom-learn` and Manager bridge Skill packages pass the Skill Creator validator;
- release Skill and capability-ledger validation passes with 430 lines in `SKILL.md`;
- wheel and sdist builds succeed and contain both new runtime modules, the behavior protocol, and all four new schemas;
- a clean virtual environment can install the wheel, validate the 18-case bilingual protocol, keep default-off status read-only, enable observability, and validate its newly initialized state.

These results establish engineering integrity for this phase. They do not replace a real dual-human, model-compatibility evaluation and do not establish learning benefit.
