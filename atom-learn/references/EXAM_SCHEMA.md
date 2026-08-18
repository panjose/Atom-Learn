# Exam analysis schema

## Contents

- Runtime files
- Import payload
- Automatic document processing
- Paper records
- Question records
- Difficulty rubric
- Knowledge-point mappings
- Derived analysis
- Commands

## Runtime files

Canonical state lives in `.atomlearn/exam/`:

- `state.yaml`: exam revision, title, target date, and timestamps;
- `bank.yaml`: normalized paper and question metadata;
- `events.ndjson`: append-only import audit events;
- `schedule.yaml`: independently revisioned canonical exam calendar, day outcomes, and replan audit events.

Generated workspace views are `EXAM_BLUEPRINT.md` and `EXAM_STUDY_PLAN.md`. Do not edit them to mutate state.

Exam revision is independent from course, intake, RAG, research, evolution, adaptation, and schedule revisions. Exam-corpus mutations accept `--expected-exam-revision`; schedule mutations accept `--expected-schedule-revision` and bind the exam/course revisions they observed.

## Import payload

```yaml
papers:
  - id: final-2025
    title: 2025 final examination
    year: 2025
    session: annual
    kind: official_past_exam
    total_points: 100
    source_id: final-2025-source
    locator: pages 1-8
questions:
  - id: final-2025.q1
    paper_id: final-2025
    number: "1"
    type: calculation
    points: 10
    stem_summary: Apply the derivative definition to a polynomial.
    source_locator: page 1, question 1
    family_id: derivative-from-definition
    cognitive_levels: [apply]
    tags: [representative]
    difficulty:
      basis: rubric
      conceptual_load: 2
      reasoning_depth: 3
      knowledge_integration: 2
      execution_load: 2
      time_pressure: 2
      confidence: 0.85
      official_level: null
    knowledge_points:
      - id: derivative.definition
        label: Derivative definition
        atom_id: calculus.derivative.definition
        weight: 1.0
        confidence: 0.9
        basis: direct
```

The top-level fields are exactly `papers` and `questions`. Imports are incremental. IDs already present in the bank are rejected; use new stable IDs for new papers and questions.

## Paper records

Paper fields are exactly:

- `id`: lowercase dot-separated stable ID;
- `title`;
- `year`: integer `1900`-`2200` or `null`;
- `session`: short label, possibly empty;
- `kind`: `official_past_exam`, `sample_exam`, `mock_exam`, `question_bank`, or `practice_set`;
- `total_points`: positive number or `null`;
- `source_id`: stable source/RAG ID;
- `locator`: location of the paper within that source.

Known question points may not exceed the paper's declared total.

## Question records

Question fields are exactly:

- `id`, `paper_id`, and display `number`;
- `type`;
- `points` or `null`;
- concise `stem_summary` and stable `source_locator`;
- optional `family_id`;
- non-empty `cognitive_levels`;
- up to 20 tags;
- one difficulty record;
- one or more knowledge-point mappings.

Question type is one of `single_choice`, `multiple_choice`, `true_false`, `short_answer`, `calculation`, `proof`, `essay`, `programming`, `case_analysis`, or `other`.

Cognitive levels are `remember`, `understand`, `apply`, `analyze`, `evaluate`, and `create`.

`answer_locator`, `marking_locator`, and `marking_link_status` (`linked`, `answer_only`, `marking_only`, or `missing`) associate each question with answer and marking artifacts without copying them. The schema has no full-stem, full-solution, or answer-key field. Keep those artifacts in the referenced private source layer.

## Automatic document processing

`exam process` accepts private question, answer, and marking text or paths through `assets/templates/exam-process.yaml`. It recognizes `Question 1`, `Q1`, `第 1 题`, and `1.` style boundaries, rejects ambiguous unnumbered text, produces stable question IDs and line locators, links matching answer/marking numbers, infers points and question/cognitive types, proposes Atom mappings, and derives the five-factor rubric. Only concise summaries and locators enter canonical state.

The result includes unmatched answer/marking numbers, missing associations, and a mapping review queue. `semantic_mapping` accepts `auto`, `required`, or `off`. `auto` uses semantic evidence only with a current learned/provider embedding profile and benchmark-approved reranker; otherwise it records a typed lexical fallback. `required` fails before mutation when that gate is unavailable. Each admitted semantic result retains its RAG/source revision, chunk, Document IR block, exact locator, runtime profile, and benchmark. Use `exam review-mappings` with `assets/templates/exam-mapping-review.yaml` to confirm, remap, or explicitly unmap every proposal.

## Difficulty rubric

`basis` is `official`, `rubric`, or `estimated`. Each factor is a number from `1` through `5`; confidence is `0.5`-`1.0`. Legacy `official_level` remains readable, but only `exam record-official` can create a qualified official anchor because it requires the level, source, exact source locator, and reviewer ID.

The runtime derives `structural_complexity`:

```text
estimated = 0.24 conceptual_load
          + 0.28 reasoning_depth
          + 0.20 knowledge_integration
          + 0.18 execution_load
          + 0.10 time_pressure
```

It stores the raw/calibrated structural level and a band: `foundation`, `standard`, `intermediate`, `advanced`, or `challenge`. Qualified `official_difficulty` is immutable source-located examiner/publisher evidence. Optional `empirical_difficulty` contains `attempt_count`, `correct_rate`, population, observation-window start/end, optional `median_seconds`, `discrimination`, `irt_b`, and mandatory `source`/`source_locator`. At least 30 attempts plus complete provenance are required before an empirical level can become effective. `effective_basis` declares `empirical`, `official`, or `structural_complexity`; the last is never described as reliable observed difficulty.

## Knowledge-point mappings

Required mapping fields are `id`, `label`, `atom_id`, `weight`, `confidence`, and `basis`. Canonical mappings also retain `review_status`, `candidate_atom_ids`, `candidate_scores`, `mapping_method`, `semantic_gate`, and source-located semantic evidence when admitted.

- `atom_id` references an existing course Atom or is `null` for a coverage gap;
- weights are positive and total `1.0` within one question;
- confidence is `0.5`-`1.0`;
- basis is `direct`, `solution_step`, `prerequisite`, or `inferred`;
- one knowledge-point ID must keep the same label and Atom mapping across the corpus.
- automatic mappings are always `pending`; only explicit review can confirm or correct them;

Archived Atom aliases are resolved during analysis. An unresolved archived mapping becomes a coverage gap rather than being silently discarded.

`exam calibrate` requires at least one official-level anchor. It learns the bounded mean offset between official and five-factor estimated levels, reports MAE before/after calibration, and applies the offset to non-official questions while retaining every original factor, estimate, and official value.

## Derived analysis

`exam analyze` returns:

- corpus size, years, points, and Atom mapping coverage;
- question-type, cognitive-level, and difficulty distributions;
- source/RAG traceability;
- per-knowledge-point and per-Atom frequency, score, paper coverage, difficulty, and confidence;
- unmapped coverage gaps and limitations.

`exam propose-families` creates canonical but provisional family records from normalized stems, knowledge candidates, and solution structure. `exam review-families` controls confirmed `family_id` assignment and optionally records held-out transfer evidence. `memorization_risk` remains `unknown` without minimum seen and held-out samples.

`exam plan` returns a revision-bound priority queue derived from exam emphasis, learner gap, difficulty, prerequisites, and representative questions. `exam daily-plan` consumes the capacity contract in `assets/templates/exam-daily-plan.yaml` as a read-only preview. `exam replan` requires its target date to equal the canonical exam target and persists `.atomlearn/exam/schedule.yaml` under `exam-schedule-state.schema.json`, with its own revision, exact exam/course revision binding, capacity hash, task/day assignment, carried completion, invalidation reasons, and `replanned` or `infeasible` event. Day outcomes conform to `exam-day-outcome.schema.json`. `exam plan-status` derives freshness plus `due` and `overdue` events without mutating state.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py exam init <workspace> --title <title> [--target-date YYYY-MM-DD]
python <SKILL_DIR>/scripts/atomlearn.py exam import <workspace> --input <exam-import.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam process <workspace> --input <exam-process.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam process-source <workspace> --source-id <source-id> --paper-id <paper-id> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam review-mappings <workspace> --input <exam-mapping-review.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam record-official <workspace> --input <exam-official-difficulty.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam set-target <workspace> --target-date YYYY-MM-DD [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam calibrate <workspace> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam record-empirical <workspace> --input <exam-empirical-difficulty.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam propose-families <workspace> [--threshold 0.62] [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam review-families <workspace> --input <exam-family-review.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam analyze <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam plan <workspace> [--mode learning|review|mixed] [--limit N]
python <SKILL_DIR>/scripts/atomlearn.py exam daily-plan <workspace> --input <exam-daily-plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py exam replan <workspace> --input <exam-daily-plan.yaml> --reason <reason> --expected-schedule-revision N
python <SKILL_DIR>/scripts/atomlearn.py exam plan-status <workspace> [--as-of YYYY-MM-DD]
python <SKILL_DIR>/scripts/atomlearn.py exam record-day <workspace> --input <exam-day-outcome.yaml> --expected-schedule-revision N
python <SKILL_DIR>/scripts/atomlearn.py exam validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam render <workspace>
```
