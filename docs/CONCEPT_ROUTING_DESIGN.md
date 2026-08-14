# Relation-Aware Concept Routing Design

## Problem

An atomic tutor inevitably mentions adjacent concepts. When a learner asks about one, a generic assistant often responds with another long explanation, even though the learner first needs to know whether the concept belongs now, should have been learned earlier, is already scheduled later, or is merely optional. That destroys the one-Atom focus and makes the course map feel unreliable.

AtomLearn resolves this with an explicit relationship decision before content delivery. The decision is previewable, explainable, persisted, and connected to safe DAG operations.

To reduce confusion before a question occurs, the tutor may add one short cue to the first unavoidable mention of a likely unfamiliar adjacent term: “后续会讲,” “若不熟需先补,” or “可选拓展.” It avoids tagging ordinary vocabulary; asking about the term expands the cue into the full relationship card.

## Product principles

1. Navigation before explanation.
2. No surprise curriculum mutation.
3. One Active Atom even during remediation.
4. Required and optional work remain visibly different.
5. Every interruption has a deterministic return path.
6. Existing planned work is named instead of duplicated.
7. The smallest useful context is preferred over an unsolicited lecture.

## User experience

The assistant responds to an unfamiliar concept with a short card:

```text
关系：后续会讲
原因：它由“topic.next”负责，当前 Atom 只需要先建立核心定义。
对当前进度：不阻塞；现在展开会提前引入两个尚未具备的步骤。
建议：先放入待答区，完成当前 Atom 后按计划学习。
可选：按计划稍后学 / 只给一句背景
```

For a blocking prerequisite, the card instead offers “learn first” and “quick diagnostic.” For an optional extension it offers definition-only context, an optional branch, parking, or dismissal.

## Domain model

The canonical relations are:

- `inside_current`
- `required_prerequisite`
- `scheduled_successor`
- `optional_extension`
- `out_of_scope`

Routed Questions gain a compact `routing` record containing concept, relation, action, impact, and timestamp. Optional branch Atoms gain anchor and origin-Question metadata. The graph projects optional branches separately from prerequisite edges and detailed-expansion containment.

## State transition design

Preview performs normalization, destination checks, and cycle checks without writing state.

Non-structural actions create a Question and preserve the Active Atom. `brief_context` additionally returns a machine-readable definition-only response limit.

Required prerequisite actions create or reuse an Atom, add the prerequisite edge, record a blocking Question, and invoke the existing backtrack stack. Mastery followed by `resume` restores the interrupted Atom.

Optional branch creation adds an optional Atom anchored after the current Atom. It does not change the Active Atom and does not enter required completion accounting.

## Guardrails

- Structural actions require explicit confirmation and optimistic revision checks.
- A downstream Atom cannot be inserted as an upstream prerequisite.
- A locked prerequisite target must have its own prerequisites repaired first.
- Existing optional targets must already be marked optional.
- Optional branches never outrank required available Atoms in the ordinary queue.
- Split and merge reject branch participants.
- Expanded child sequence requirements are retained while allowing explicit extra prerequisite edges.

## Source grounding

New routing Atoms inherit source locators from the Active Atom by default. The tutor should retrieve evidence for the concept boundary and the claimed relationship before proposing a new Atom. If local coverage is insufficient, the existing RAG corrective-search workflow supplies bounded external evidence. Optional curiosity alone is not a reason for broad web expansion.

## Implementation

The `route-concept` CLI owns preview and application. Its deterministic action matrix prevents a model from inventing incompatible choices. Canonical validation covers relation metadata, branch anchors, origin Questions, graph projections, and DAG acyclicity.

Generated learning maps nest same-module optional branches under their anchors and label them. Knowledge-lineage structure and Markdown output expose optional branches as a separate layer. Course completion continues to consider only required, non-archived Atoms.

## Verification

Tests cover read-only preview, focus-preserving routes, confirmed prerequisite insertion and resume, cycle rejection, optional branch projection, recommendation order, map visibility, and optional-work-independent completion. The complete existing test suite remains the compatibility gate.
