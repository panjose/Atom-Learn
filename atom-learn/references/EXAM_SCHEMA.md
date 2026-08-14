# Exam analysis schema

## Contents

- Runtime files
- Import payload
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
- `events.ndjson`: append-only import audit events.

Generated workspace views are `EXAM_BLUEPRINT.md` and `EXAM_STUDY_PLAN.md`. Do not edit them to mutate state.

Exam revision is independent from course, intake, RAG, research, evolution, and adaptation revisions. Import mutations accept `--expected-exam-revision`.

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

The schema has no full-stem, full-solution, or answer-key field. Keep those artifacts in the referenced private source layer.

## Difficulty rubric

`basis` is `official`, `rubric`, or `estimated`. Each factor is a number from `1` through `5`; confidence is `0.5`-`1.0`. `official_level` is a number from `1` through `5` or `null`, and is required when basis is `official`.

The runtime derives:

```text
estimated = 0.24 conceptual_load
          + 0.28 reasoning_depth
          + 0.20 knowledge_integration
          + 0.18 execution_load
          + 0.10 time_pressure
```

It stores `estimated_level`, `effective_level`, and a band: `foundation`, `standard`, `intermediate`, `advanced`, or `challenge`.

## Knowledge-point mappings

Mapping fields are exactly `id`, `label`, `atom_id`, `weight`, `confidence`, and `basis`.

- `atom_id` references an existing course Atom or is `null` for a coverage gap;
- weights are positive and total `1.0` within one question;
- confidence is `0.5`-`1.0`;
- basis is `direct`, `solution_step`, `prerequisite`, or `inferred`;
- one knowledge-point ID must keep the same label and Atom mapping across the corpus.

Archived Atom aliases are resolved during analysis. An unresolved archived mapping becomes a coverage gap rather than being silently discarded.

## Derived analysis

`exam analyze` returns:

- corpus size, years, points, and Atom mapping coverage;
- question-type, cognitive-level, and difficulty distributions;
- source/RAG traceability;
- per-knowledge-point and per-Atom frequency, score, paper coverage, difficulty, and confidence;
- unmapped coverage gaps and limitations.

`exam plan` returns a revision-bound priority queue derived from exam emphasis, learner gap, difficulty, prerequisites, and representative questions. The queue is generated, not canonical.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py exam init <workspace> --title <title> [--target-date YYYY-MM-DD]
python <SKILL_DIR>/scripts/atomlearn.py exam import <workspace> --input <exam-import.yaml> [--expected-exam-revision N]
python <SKILL_DIR>/scripts/atomlearn.py exam status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam analyze <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam plan <workspace> [--mode learning|review|mixed] [--limit N]
python <SKILL_DIR>/scripts/atomlearn.py exam validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py exam render <workspace>
```
