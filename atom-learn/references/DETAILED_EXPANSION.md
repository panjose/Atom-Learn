# Atomic detailed expansion

## Contents

- Purpose
- Trigger decision
- Expansion contract
- Source grounding
- Payload and command
- State transition
- Teaching loop
- Nested expansion
- Mastery and integration
- Flexibility and recovery
- Subsystem behavior
- Failure boundaries

## Purpose

A request such as “explain this in detail,” “break this down,” or “go deeper on this Atom” is not permission to place several concepts in one long response. Treat it as evidence that the current Atom is broader than the learner's workable cognitive unit.

Convert the request into a persistent expansion tree. Teach exactly one child Atom at a time, require Atom-specific Evidence for every child, and return to the parent only for a final integration check.

## Trigger decision

Use detailed expansion when all are true:

1. the learner explicitly asks for more depth, steps, components, or a careful derivation;
2. satisfying that request requires two or more independently teachable ideas;
3. each idea can have its own observable objective and check;
4. the requested scope still belongs to the current parent objective.

Do not expand when one concise clarification, example, analogy, or missing definition is sufficient. Answer that move inside the current Atom and keep the same Active Atom.

Do not confuse expansion with:

- `backtrack`: repair an external prerequisite that blocks the parent;
- `restructure split`: replace and archive a badly modeled Atom;
- `response.detail=detailed`: alter presentation style without changing the graph;
- `future_atom`: park a successor topic rather than teaching it early.

The learner's explicit detailed-explanation request supplies confirmation for a well-formed expansion plan. Do not ask them to approve the same structural action again unless scope or ordering is genuinely ambiguous.

## Expansion contract

Create between 2 and 12 ordered child Atoms. Prefer 2–5 for one request. Every child must:

- have one observable learning objective;
- be teachable and checkable independently;
- inherit the parent's source grounding and optionality;
- remain inside the parent's objective boundary;
- avoid previewing later children in its teaching response.

The CLI converts list order into a strict chain:

```text
original prerequisites -> child 1 -> child 2 -> ... -> child N -> parent integration
```

The containment relation is stored separately as `parent_atom_id` and `graph.expansions`. Prerequisite edges still control activation.

## Source grounding

Retrieve the parent source locators and only the evidence required to define child boundaries. If the supplied material does not support the requested depth, run the normal RAG corrective-search loop before expanding.

Inherit parent source references by default. Supply narrower child locators when available. Never manufacture detail from model memory when the course requires source grounding, and never copy long source passages into an Atom record.

## Payload and command

Create a YAML plan in teaching order:

Start from `assets/templates/expand-plan.yaml` when useful.

```yaml
reason_code: learner_requested_detail
note: The learner asked to derive the definition step by step.
child_atoms:
  - id: calculus.derivative.definition.change-ratio
    title: Change ratio
    objective: Explain what the difference quotient measures
    estimated_minutes: 12
    sources:
      - source_id: calculus-text
        locator: Chapter 3, Section 3.1, pp. 72-73
    misconceptions: [A difference quotient is already an instantaneous rate]
    mastery:
      required_dimensions: [explain, discriminate]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
  - id: calculus.derivative.definition.limit-step
    title: Limit step
    objective: Explain why the interval approaches zero without becoming zero
    estimated_minutes: 15
    mastery:
      required_dimensions: [explain, apply]
      pass_threshold: 0.8
      minimum_dimension_score: 0.6
```

Do not provide `prerequisites` for children. The CLI derives them from list order.

```text
python <SKILL_DIR>/scripts/atomlearn.py expand <workspace> <parent-id> --plan <expand.yaml>
python <SKILL_DIR>/scripts/atomlearn.py expand <workspace> <parent-id> --plan <expand.yaml> --confirmed --expected-revision <revision>
```

Without `--confirmed`, the command is a read-only preview. A direct learner request for detailed explanation authorizes the harness to rerun the reviewed plan with `--confirmed`.

## State transition

Expansion accepts an `available` or `active` parent and refuses archived, mastered, review-due, skipped, deferred, or already-expanded parents. It also refuses expansion while another Atom is active or while the parent has unassessed Evidence.

On application, the CLI:

1. stores the parent's original prerequisites as `base_prerequisite_ids`;
2. creates ordered child Atoms with `parent_atom_id`;
3. replaces the parent's prerequisites with the final child;
4. records the containment edge in `graph.expansions`;
5. moves the parent to `locked`;
6. activates child 1;
7. records an expansion frame for deterministic progression.

All changes occur under the normal course revision guard and one commit event.

## Teaching loop

For the Active child:

1. state its objective and connection to the parent in one sentence;
2. teach only the minimum Why → What → How → Example → Intuition moves needed for that child;
3. do not summarize or preview later children;
4. answer in-child questions normally;
5. persist the current confusion and next action;
6. run a proportionate mastery check;
7. let `assess` activate the next child automatically after mastery.

The learner-facing response should identify the child position, such as “1 of 3,” without dumping the remaining content. Titles may be listed once as an orientation map, but explanations must remain sequential.

## Nested expansion

If a child itself requires several independent ideas, expand that Active child. The expansion stack becomes a containment path, for example:

```text
parent
└── child 1
    ├── grandchild 1
    └── grandchild 2
```

Complete the innermost grandchildren, run the child integration check, then continue with the next outer child. Maintain exactly one Active Atom at every depth.

## Mastery and integration

Every child requires mastered Evidence. A diagnostic test-out may shorten teaching, but a provisional skip cannot satisfy an expanded child.

After the final child is mastered, the CLI activates the parent in `integrating` phase. Design a new parent-level check that requires the learner to connect or apply the child ideas together. Do not reuse a child prompt as the integration check.

Only mastered integration Evidence sets `expansion.completed_at`. Until then, the parent and downstream Atoms remain incomplete.

## Flexibility and recovery

The learner may defer an Active child. Deferral clears the current expansion focus and does not unlock the next child. Run `unskip`, then `activate` on the restored child to reconstruct the expansion context.

If a child exposes an external missing prerequisite, use normal question recording and `backtrack`. The expansion frame retains its backtrack depth so completing the remedial Atom returns through `resume` instead of jumping ahead in the branch.

Do not use provisional skip on an expanded parent before integration. Do not mark expansion complete from response length, self-report, or having merely displayed every child title.

## Subsystem behavior

- Learning maps render the containment tree while the prerequisite DAG enforces order.
- Lineage structure reports detailed expansions separately from semantic relations.
- Exam mappings to the parent inherit the new prerequisite closure, so preparation reaches its children first.
- Research Knowledge Atom gaps likewise follow the expanded prerequisite chain before returning to an Active Paper.
- Importing an updated course plan preserves existing expansion metadata and computed prerequisites.
- Session adaptation may change explanation style inside a child, but cannot collapse several children into one answer.

## Failure boundaries

Fail closed when child IDs collide, the plan contains fewer than two or more than twelve children, explicit child prerequisites conflict with ordered expansion, a parent already has an expansion, or state validation fails.

Do not silently flatten an expansion, archive its parent, merge participating Atoms, or erase Evidence. Use a future explicit expansion-revision workflow if the child structure itself must change after learning begins.
