# State and payload schema

## Contents

- Workspace layout
- Core records
- Course intake
- Import plan
- Mutation payloads
- Flexible progression state
- Revisions and timestamps

## Workspace layout

Treat files under `.atomlearn/` as canonical:

```text
.atomlearn/
├── course.yaml
├── graph.yaml
├── atoms/<atom-id>.yaml
├── state/current.yaml
├── questions.yaml
├── evidence.yaml
├── reviews.yaml
└── events.ndjson
```

Treat the five core Markdown files at workspace root as generated projections. Optional intake, RAG, research, evolution, adaptation, exam, and lineage subsystems add their own canonical files under `.atomlearn/` and generated views at the workspace root.

Session adaptation uses `.atomlearn/adaptation/{state.yaml,profile.yaml,signals.ndjson,ledger.ndjson}` and generates `PERSONALIZATION.md`. It has an independent adaptation revision; see [ADAPTATION_SCHEMA.md](ADAPTATION_SCHEMA.md).

Exam preparation uses `.atomlearn/exam/{state.yaml,bank.yaml,events.ndjson}` and generates `EXAM_BLUEPRINT.md` plus `EXAM_STUDY_PLAN.md`. It has an independent exam revision; see [EXAM_SCHEMA.md](EXAM_SCHEMA.md).

Knowledge lineage uses `.atomlearn/lineage/{state.yaml,map.yaml,events.ndjson}` and generates `KNOWLEDGE_LINEAGE.md`. It derives structural views from the course DAG and stores only semantic annotations, relations, and curated threads under an independent lineage revision; see [LINEAGE_SCHEMA.md](LINEAGE_SCHEMA.md).

## Core records

Use lowercase dot-separated IDs matching `[a-z0-9][a-z0-9.-]*`. Never place `/` or `\\` in an ID.

Atom statuses are `locked`, `available`, `active`, `mastered`, `review_due`, `skipped`, `deferred`, and `archived`. `skipped` satisfies traversal without claiming mastery; `deferred` does neither. Session phases are `orientation`, `teaching`, `questioning`, `checking`, `reviewing`, `paused`, `blocked`, and `transitioning`.

Course statuses are `orientation`, `active`, `completed`, `completed_with_skips`, and `paused`. `completed` requires mastery-like status for every required Atom; `completed_with_skips` discloses that traversal finished with at least one required provisional assumption.

Use these question classifications: `in_atom`, `blocking_prerequisite`, `non_blocking`, `future_atom`, and `out_of_scope`.

Use Evidence scores from `0.0` through `1.0`. The CLI derives the result from required dimensions, `pass_threshold`, and `minimum_dimension_score`.

## Course intake

Course intake accepts three primary modes: complete `sources`, a user-provided `outline`, or topic-only `topic` terms. It uses an independent revision in `.atomlearn/intake.yaml` and generates `INTAKE.md`.

Read [INTAKE_SCHEMA.md](INTAKE_SCHEMA.md) for payloads and [COURSE_INTAKE.md](COURSE_INTAKE.md) for mode-specific workflows. Complete intake only after the imported plan represents the intake source IDs and every non-archived Atom has a source locator.

## Import plan

Create a YAML file like:

```yaml
course:
  title: Calculus foundations
  goal: Understand derivatives from first principles
  learner:
    prior_knowledge: [functions]
sources:
  - id: calculus-text
    title: Calculus, 9th edition
    type: pdf
    location: C:/materials/calculus.pdf
    version: 9th edition
atoms:
  - id: calculus.limit.intuition
    title: 极限的直觉
    module: Limits
    objective: 能用自己的话解释趋近而不必等于
    prerequisites: []
    difficulty: 1
    estimated_minutes: 20
    sources:
      - source_id: calculus-text
        locator: Chapter 2, Section 2.1, pp. 35-41
    misconceptions: [趋近等同于取到]
    mastery:
      required_dimensions: [explain, discriminate]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
  - id: calculus.derivative.definition
    title: 导数的形式化定义
    module: Derivatives
    objective: 能解释并使用差商极限定义导数
    prerequisites: [calculus.limit.intuition]
    difficulty: 2
    estimated_minutes: 25
    sources:
      - source_id: calculus-text
        locator: Chapter 3, Section 3.1, pp. 72-76
    misconceptions: [可以直接令 delta_x 等于 0]
    mastery:
      required_dimensions: [explain, apply, discriminate]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
```

Import plans add or update sources and Atoms. They do not silently remove missing records. The CLI recalculates `locked` and `available`, preserves `mastered`, `active`, `review_due`, `skipped`, `deferred`, and `archived`, then rebuilds graph edges and existing flexibility metadata.

## Flexible progression state

An Atom may carry `flexibility: null` or an exact record:

```yaml
flexibility:
  mode: provisional
  reason_code: already_mastered
  note: Prior coursework covers this objective.
  diagnostic_offered: true
  confirmed: true
  created_at: "2026-08-14T10:00:00+00:00"
  revoked_at: null
```

`mode` is `provisional` or `defer`. Reason codes are `already_mastered`, `too_easy`, `not_relevant`, `time_constraint`, `different_goal`, and `other`. A non-null `revoked_at` retains the latest decision record while returning the Atom to normal availability calculation. See [FLEXIBLE_PROGRESSION.md](FLEXIBLE_PROGRESSION.md).

## Mutation payloads

### Update session

```yaml
phase: questioning
current_question: 为什么不能直接令 delta_x 等于 0？
add_understands: [average-rate-of-change]
add_confusions: [approach-vs-equality]
remove_confusions: []
next_action: Contrast a nonzero shrinking interval with division by zero.
```

### Record question

```yaml
text: 那线程是什么？
classification: future_atom
related_atom_id: os.thread.definition
rationale: The current Atom is process definition; threads are a mapped successor.
priority: normal
```

### Record Evidence

```yaml
atom_id: calculus.derivative.definition
kind: mastery_check
prompt: Explain why delta_x approaches zero but is not set to zero.
response_summary: The learner connected the nonzero difference quotient to its limit.
scores:
  explain: 0.9
  apply: 0.8
  discriminate: 0.9
feedback: Correct; make the domain restriction explicit next time.
rationale: The response handled the central misconception and applied the definition.
```

### Split proposal

```yaml
action: split
source_atom_id: calculus.derivative.definition
downstream_replacement_id: calculus.derivative.definition.compute
new_atoms:
  - id: calculus.derivative.definition.meaning
    title: 差商极限的含义
    module: Derivatives
    objective: 解释差商极限中趋近零的意义
    prerequisites: [calculus.limit.intuition]
    mastery:
      required_dimensions: [explain, discriminate]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
  - id: calculus.derivative.definition.compute
    title: 用定义计算导数
    module: Derivatives
    objective: 使用定义计算简单函数的导数
    prerequisites: [calculus.derivative.definition.meaning]
    mastery:
      required_dimensions: [apply]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
```

### Merge proposal

```yaml
action: merge
source_atom_ids: [topic.atom-a, topic.atom-b]
merged_atom:
  id: topic.combined-atom
  title: Combined mechanism
  module: Topic
  objective: Explain and apply the combined mechanism
  prerequisites: []
  mastery:
    required_dimensions: [explain, apply]
    pass_threshold: 0.8
    minimum_dimension_score: 0.6
```

## Evolution state and proposal

Evolution uses a separate revision domain under `.atomlearn/evolution/`. Its canonical files are `policy.yaml`, `state.yaml`, `metrics.yaml`, `hypotheses.yaml`, proposal and experiment directories, checkpoints, and an append-only ledger. `EVOLUTION.md` is a generated view.

Create a manual proposal with this shape:

```yaml
scope: learner
origin: manual
type: adjust_mastery
target_atom_ids: [calculus.derivative.definition]
observations: [ev-000014, rv-000003]
hypothesis: Delayed review failure indicates that transfer evidence is missing.
change:
  atom_id: calculus.derivative.definition
  required_dimensions: [explain, apply, discriminate, transfer]
evaluation:
  success_criteria:
    - metric: atom.average_score
      atom_id: calculus.derivative.definition
      operator: gte
      value: 0.8
      min_observations: 2
```

Supported types are `teaching_strategy`, `adjust_review_intervals`, `adjust_mastery`, `add_dependency`, `remove_dependency`, `split_atom`, `merge_atoms`, and `patch_skill`. The engine assigns risk from the type; input cannot lower it. Split and merge changes embed the corresponding restructure proposal under `change.proposal`.

Read [EVOLUTION.md](EVOLUTION.md) for the lifecycle and [EVOLUTION_POLICY.md](EVOLUTION_POLICY.md) for authority, bounds, privacy, and rollback rules.

## Research paper layer

Research reading uses an independent revision and paper graph under `.atomlearn/research/`. Read [RESEARCH_SCHEMA.md](RESEARCH_SCHEMA.md) for research import plans, paper roles and statuses, critical notes, relations, and generated views. Read [RESEARCH_READING.md](RESEARCH_READING.md) for the operational workflow and research-gap safeguards.

## Revisions and timestamps

Read `course.yaml.revision` before a mutation and pass `--expected-revision`. Every canonical state file and Atom carries the same revision; a mismatch detects an interrupted multi-file write. A stale command or inconsistent revision must stop rather than overwrite state. Store timestamps as timezone-aware ISO 8601 values. Let the CLI create IDs, revisions, and timestamps.
