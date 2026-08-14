# Intake state schema

## Contents

- Runtime files
- Common fields
- Sources mode
- Outline mode
- Topic mode
- Commands

## Runtime files

Canonical intake state lives in `.atomlearn/intake.yaml`. Intake mutations are appended to `.atomlearn/intake-events.ndjson`. `INTAKE.md` is a generated view.

Intake revision is independent from course, research, and evolution revisions. Pass `--expected-intake-revision` on updates and completion.

## Common fields

```yaml
mode: topic
request_summary: I want to learn derivatives.
goal: Understand what derivatives mean and how to use them.
desired_outcome: orientation
target_depth: overview
prior_knowledge: [basic algebra]
constraints: [Prefer visual intuition before formal proofs]
ambiguities: [The intended application domain is not specified]
assumptions: [Begin with single-variable calculus]
```

`desired_outcome` is `orientation`, `working_knowledge`, `exam`, `project`, or `research`. `target_depth` is `overview`, `working`, `advanced`, or `expert`.

The CLI can detect the mode when exactly one of `source_materials`, `outline_items`, or `topic_terms` is non-empty. Set `mode` explicitly for mixed inputs.

## Sources mode

```yaml
mode: sources
request_summary: Learn from the supplied calculus textbook.
goal: Understand derivatives from first principles.
desired_outcome: working_knowledge
target_depth: working
source_materials:
  - id: calculus-text
    title: Calculus, 9th edition
    type: pdf
    location: C:/materials/calculus.pdf
    version: 9th edition
```

Source types are `pdf`, `book`, `notes`, `documentation`, `website`, `database`, `outline`, `exam`, and `other`. Store metadata and stable locations, not copied full text.

## Outline mode

```yaml
mode: outline
request_summary: Use my syllabus as the coverage structure.
goal: Prepare for the final assessment.
desired_outcome: exam
target_depth: advanced
outline_source_id: user-outline
outline_items:
  - id: outline.limits
    title: Limits
    parent_id: null
    notes: Intuition and formal definition
  - id: outline.derivatives
    title: Derivatives
    parent_id: outline.limits
    notes: Definition and computation
```

Outline parent IDs must exist and the hierarchy must remain acyclic. Register `outline_source_id` in the course import plan and use outline item IDs as Atom locators. Outline intake remains `discovering` until a RAG coverage report for the current intake revision explicitly supports every outline ID.

## Topic mode

Initial capture:

```yaml
mode: topic
request_summary: I want to learn causal inference.
goal: Understand the foundations and evaluate common methods.
desired_outcome: working_knowledge
target_depth: working
topic_terms: [causal inference]
ambiguities: [The application domain is unspecified]
assumptions: [Start with observational data]
discovery_sources: []
```

After source discovery, update with:

```yaml
discovery_sources:
  - id: causal-overview
    title: Authoritative causal inference overview
    type: book
    location: stable-source-locator
    version: current-edition
```

Topic intake is not ready for plan completion while `discovery_sources` is empty or while its current RAG coverage gate is not `pass`. Run `rag requirements` only after the final discovery-source intake update so the report binds to the current intake revision.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py intake init <workspace> --input <intake.yaml>
python <SKILL_DIR>/scripts/atomlearn.py intake status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py intake guidance <workspace>
python <SKILL_DIR>/scripts/atomlearn.py intake update <workspace> --input <update.yaml>
python <SKILL_DIR>/scripts/atomlearn.py intake complete <workspace>
python <SKILL_DIR>/scripts/atomlearn.py intake validate <workspace>
```
