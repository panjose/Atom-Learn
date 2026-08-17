# v0.14 Phase 7 implementation: per-Atom review and daily queue

## Outcome

Phase 7 implements Workstream H without changing AtomLearn's default learning contract. Fixed 1/3/7/30 scheduling remains enabled by default. A qualified active-retrieval event can now maintain a per-Atom D/S/R memory state; shadow mode exposes an alternative date without changing the real queue; active mode requires both a current passing benchmark and explicit learner opt-in.

This phase does not claim that the adapter improves learning. The bundled benchmark is an engineering gate, and workspace pilot reports prohibit automatic promotion and causal interpretation.

## Delivered controls

1. Review Evidence may carry a typed `review_observation` with retrieval mode, hint count, delayed flag, and response time.
2. Every newly assessed review becomes an Evidence-linked auditable normalized event, but only delayed, A/B, mastery-eligible active recall updates memory; validation re-derives qualification and normalized fields from Evidence.
3. Per-Atom state records stability, retrievability, difficulty, desired retention, last qualified review, model version, event count, and suggestion.
4. `fixed`, `adaptive-shadow`, and gated `adaptive-active` modes preserve existing pending dates and history.
5. Exam objectives clamp adaptive suggestions around the target and final-review window; long-term objectives use desired retention.
6. The versioned `memory-core-v1` benchmark checks prediction calibration and critical invariants, including that response time alone has no effect.
7. The daily queue combines failures, reviews, prerequisites, new Atoms, and exam practice under declared time and cognitive-load capacity.
8. Behind-schedule output retains every overdue item and reports the backlog instead of fabricating completion.
9. The pilot compares fixed and shadow recommendations on qualified workspace history, declares insufficient samples, and always blocks automatic promotion.

## FSRS relationship

The open FSRS documentation represents memory with difficulty, stability, and retrievability and defines stability around 90% retrievability. Its tutorial also distinguishes verified defaults from parameters optimized on sufficient review history. AtomLearn adopts those useful model boundaries but uses an explicitly named DSR/FSRS-like adapter because a Knowledge Atom event contains rubric dimensions, hints, transfer context, and scorer provenance rather than a four-button flashcard rating. Sources: [scheduler overview](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler), [algorithm](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm), [tutorial](https://github.com/open-spaced-repetition/fsrs4anki/blob/main/docs/tutorial.md), and the project's [scheduler benchmark](https://github.com/open-spaced-repetition/srs-benchmark).

## Main artifacts

- `atom-learn/scripts/review_scheduler.py`
- `atom-learn/scripts/core_paths.py`
- `atom-learn/__init__.py`
- `atom-learn/assets/benchmarks/memory-core-v1.yaml`
- `atom-learn/assets/schemas/review-policy.schema.json`
- `atom-learn/assets/templates/review-policy.yaml`
- `atom-learn/assets/templates/review-evidence-observation.yaml`
- `atom-learn/references/ADAPTIVE_REVIEW.md`
- `manager/atomlearn_manager/builder.py`
- `tests/test_review_scheduler.py`

## Verification

The phase includes pure adapter invariants and end-to-end CLI coverage for the benchmark/opt-in gate, shadow scheduling, qualified active recall, passive review exclusion, capacity backlog preservation, read-only queue behavior, and non-promoting pilot reports.

Local release verification on 2026-08-17:

- fast contract/property/documentation suite: `59 passed`, `146 deselected` in 80.51 seconds;
- complete integration suite: `145 passed`, `1 skipped`, `59 deselected` in 1076.28 seconds;
- the single skip is the expected optional USearch HNSW path when the local `scale` dependency is absent; the release workflow retains its dedicated Ubuntu/Python 3.12 scale job;
- Skill release validation: passed for Core `0.14.0`, with 15 implemented, 1 experimental, and 0 planned capabilities;
- targeted Phase 7 adapter/CLI tests: `5 passed`;
- installed-runtime capability smoke now runs `review benchmark` and verifies both a current result and the fixed default;
- a clean `atom_learn-0.14.0-py3-none-any.whl` was installed into an isolated venv, where `version`, workspace initialization, and `review benchmark` passed using only the packaged asset tree;
- the stable release builder now rejects any runtime Core wheel that omits the review scheduler, asset resolver, memory benchmark, policy schema, or Core manifest when `review` is required by the smoke matrix.
