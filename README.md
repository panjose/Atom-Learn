# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn is a progressive AI learning Skill built around knowledge atomization. It reorganizes textbooks or knowledge bases into a prerequisite-aware knowledge graph, then teaches, answers questions, verifies mastery, and schedules reviews around one Active Atom at a time.

> Never advance while the current atom remains unclear.  
> Do not move to the next Knowledge Atom until the current one is genuinely understood.

## Implemented Capabilities

- Generate a Knowledge Atom DAG from textbooks, PDFs, notes, or multiple sources
- Enforce exactly one Active Atom and guard all prerequisites
- Route in-Atom questions, blocking prerequisites, future questions, and Parking Lot items
- Evaluate mastery through explain/apply/discriminate/transfer/teach-back Evidence
- Restore state across sessions with revision conflict protection and event auditing
- Schedule reviews at 1/3/7/30-day intervals, with course-level overrides
- Split or merge Atoms with user confirmation while preserving stable ID aliases
- Analyze learning evidence and propose bounded, approval-gated course evolution
- Generate five learning views plus an evolution view from canonical YAML state

## Installation

AtomLearn requires Python 3.10+ and PyYAML:

```powershell
python -m pip install PyYAML
```

Copy or link the repository's `atom-learn` directory into your personal Codex Skills directory, for example:

```text
~/.codex/skills/atom-learn/
```

During development, you can also ask Codex to use the repository's `atom-learn/SKILL.md` directly.

## Quick Verification

```powershell
python atom-learn/scripts/atomlearn.py init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py validate courses/calculus
python atom-learn/scripts/atomlearn.py status courses/calculus --json
```

See [SKILL.md](atom-learn/SKILL.md) for the complete command workflow and teaching behavior, and [SCHEMA.md](atom-learn/references/SCHEMA.md) for structured input formats. Runtime course state is stored in the learner's selected course workspace, not in the Skill installation directory.

## Self-Evolution

AtomLearn can derive metrics from persisted Evidence, reviews, and prerequisite backtracking, then create testable proposals for teaching strategy, review intervals, mastery rubrics, dependency edges, or Atom structure. Evolution is `proposal_only` by default: every change must be previewed, approved by the required authority, validated, checkpointed, and monitored.

```powershell
python atom-learn/scripts/atomlearn.py evolve status courses/calculus
python atom-learn/scripts/atomlearn.py evolve analyze courses/calculus --propose
python atom-learn/scripts/atomlearn.py evolve preview courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve approve courses/calculus evo-000001 --authority learner --actor "learner"
python atom-learn/scripts/atomlearn.py evolve apply courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve monitor courses/calculus evo-000001
```

The engine keeps course and evolution revisions separate, stores no raw learner messages in evolution metrics, and refuses runtime `patch_skill` application. Automatic rollback is allowed only before later learning mutations; otherwise AtomLearn requires a compensating proposal that preserves newer Evidence. See [Bounded Self-Evolution](atom-learn/references/EVOLUTION.md) for the operating workflow.

## Design Documentation

- [Product and Technical Design](docs/PRODUCT_DESIGN.md)
- [Detailed Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Self-Evolution Design](docs/SELF_EVOLUTION_DESIGN.md)

## Development Validation

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/evolution.py
```

The repository includes small calculus and operating-systems course plans as test fixtures. Automated tests use isolated workspaces under `.test-workspaces/` and do not modify the example files.
