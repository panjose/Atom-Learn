# State and payload schema

## Contents

- Workspace layout
- Core records
- Import plan
- Mutation payloads
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

Treat the five Markdown files at workspace root as generated projections.

## Core records

Use lowercase dot-separated IDs matching `[a-z0-9][a-z0-9.-]*`. Never place `/` or `\\` in an ID.

Atom statuses are `locked`, `available`, `active`, `mastered`, `review_due`, and `archived`. Session phases are `orientation`, `teaching`, `questioning`, `checking`, `reviewing`, `paused`, `blocked`, and `transitioning`.

Use these question classifications: `in_atom`, `blocking_prerequisite`, `non_blocking`, `future_atom`, and `out_of_scope`.

Use Evidence scores from `0.0` through `1.0`. The CLI derives the result from required dimensions, `pass_threshold`, and `minimum_dimension_score`.

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

Import plans add or update sources and Atoms. They do not silently remove missing records. The CLI recalculates `locked` and `available`, preserves `mastered`, `active`, `review_due`, and `archived`, then rebuilds graph edges.

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

## Revisions and timestamps

Read `course.yaml.revision` before a mutation and pass `--expected-revision`. Every canonical state file and Atom carries the same revision; a mismatch detects an interrupted multi-file write. A stale command or inconsistent revision must stop rather than overwrite state. Store timestamps as timezone-aware ISO 8601 values. Let the CLI create IDs, revisions, and timestamps.
