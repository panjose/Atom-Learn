# Knowledge atomization and graph rules

## Contents

- Atom test
- Splitting and merging
- Detailed expansion
- Dependency rules
- Multi-source synthesis
- Dynamic restructuring

## Atom test

Accept a candidate as one Knowledge Atom only when all are true:

1. It has one observable learning objective.
2. It can be explained, questioned, practiced, and checked in one continuous teaching interaction.
3. It has an independent mastery check.
4. Its prerequisites can be listed without referring to an entire broad subject.
5. Its title names a concept, mechanism, distinction, or skill rather than a chapter container.

Reject broad labels such as “Derivatives” or “Operating Systems.” Reject fragments such as a standalone symbol unless understanding that symbol is itself a meaningful bottleneck. Aim initially for 15–40 focused minutes per Atom. Treat this as a heuristic, not a hard limit.

## Splitting and merging

Propose a split when an Atom has multiple independent objectives, requires separate checks, repeatedly fails in different subskills, or exceeds the learner's workable cognitive load.

Propose a merge when adjacent Atoms cannot be taught or checked independently, are always mastered together, or create needless state transitions for this learner.

Preserve stable history: archive old IDs rather than deleting them, add aliases, retain old Evidence, migrate open questions deliberately, replace downstream prerequisites with the declared replacement, and validate the full DAG before applying.

## Detailed expansion

Treat an explicit “explain this Atom in detail” request differently from a corrective split. Keep the parent as the eventual integration objective, create ordered child Atoms beneath it, and teach only one child at a time. Use `expand`, not `restructure split`; read [DETAILED_EXPANSION.md](DETAILED_EXPANSION.md).

Every child needs an independent objective and check. If only one clarification is needed, do not expand. If the original Atom is invalid as a learning unit for everyone rather than merely too broad for the requested depth, use a confirmed split instead.

## Dependency rules

Create an edge `A → B` only when mastering A materially reduces the reasoning required for B or B cannot be checked fairly without A. Do not encode mere textbook order as dependency.

Keep the graph acyclic. Use modules for navigation, not as prerequisite nodes. Prefer the smallest sufficient prerequisite set; redundant transitive edges make explanations and path selection noisy.

## Multi-source synthesis

Build one knowledge structure across sources. Map each Atom to every useful locator. Preserve source-specific terminology as aliases and record disagreements instead of blending them silently.

Distinguish source-grounded statements with locators, pedagogical synthesis derived from several sources, and AI-supplied bridge material marked `synthesized`. Do not copy large source passages into Atom metadata.

## Dynamic restructuring

Use actual learning evidence, not response length alone. Consider a split after repeated dimension-specific failure or when the learner can explain one part but not another. Consider a merge when checks show the learner treats two small Atoms as one coherent operation.

Generate a split or merge proposal, explain why it improves this learner's path, and obtain explicit confirmation before running `restructure --confirmed`. A direct request for detailed explanation authorizes a bounded expansion plan, but never authorizes a multi-concept lecture.
