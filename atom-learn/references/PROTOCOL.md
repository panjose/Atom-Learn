# Learning protocol

## Contents

- Orientation
- Teaching loop
- Session recovery
- Backtracking
- Advancement
- Flexible progression
- Failure recovery

## Orientation

Start by establishing the learner's target, current level, time constraints, preferred depth, and authoritative sources. Inspect sources before producing a map. Separate source order from learning order.

Build the map in bounded batches. For a large field, map the core path and immediate dependencies first, then expand nearby modules when the learner approaches them. State uncertainty about dependency choices or source conflicts.

Finish orientation by reporting the course goal and scope, source inventory and conflicts, module and Atom counts, important prerequisite assumptions, first available Atom, and anything requiring learner confirmation. Do not turn orientation into the first lecture.

When prior knowledge appears substantial, offer short diagnostics for likely-known Atoms. Do not make the learner sit through introductory teaching merely to reach the check.

## Teaching loop

Use one Active Atom and cycle through:

1. **Why** — establish the problem the Atom solves.
2. **What** — state the concept or mechanism precisely.
3. **How** — derive or apply it in small steps.
4. **Example** — use one representative example.
5. **Intuition** — connect it to prior knowledge without replacing precision.
6. **Questioning** — let the learner probe the current Atom.
7. **Checking** — request observable performance.
8. **Remediation or advancement** — target weak dimensions or persist mastery.

Prefer one teachable move per response. Do not optimize for covering a chapter. Persist after any turn that changes what the learner understands, what remains confusing, the current question, or the next action.

## Session recovery

At the start of a new session:

1. Run `refresh-reviews` and `status --json`.
2. Read the Active Atom and open blocking question, if any.
3. Read only source locations needed for the next action.
4. Summarize the state in two or three sentences.
5. Continue from `next_action`; do not restart teaching unless requested.

If no Atom is active, present due reviews first, then available candidates from `suggest-next`.

## Backtracking

Backtrack only when a missing prerequisite blocks the current objective. Record the question and rationale before switching. Preserve the parent Atom, phase, question, and next action on the stack.

Treat the remedial prerequisite as the only Active Atom. Require a proportionate check. After mastery, resume the parent and explicitly reconnect the prerequisite to the original confusion.

Do not backtrack for an interesting but nonessential question; park it.

## Advancement

Advance when all conditions hold:

- the mastery check covers the required dimensions;
- saved Evidence meets the threshold and minimum dimension score;
- no blocking question for the Atom remains open;
- state validation passes;
- the learner asks to continue, or the original request clearly authorizes continuous guided study.

When multiple Atoms are available, prefer lower difficulty, then map order. Explain a non-obvious choice.

## Flexible progression

Treat a request to skip as a state command, not as proof of understanding. Read [FLEXIBLE_PROGRESSION.md](FLEXIBLE_PROGRESSION.md). Prefer a short diagnostic. If the learner explicitly confirms a provisional skip, allow traversal while labeling the Atom `skipped` and preserving zero mastery claim. Use `deferred` for “not now”; it must not satisfy prerequisites.

If a downstream gap exposes a bad assumption, backtrack to the skipped Atom and let the CLI revoke the skip. Keep the tone neutral: reversible assumptions are part of the protocol, not learner failure.

## Failure recovery

If `validate` fails, stop state mutation and report the exact inconsistency. Repair canonical YAML only after inspecting all references. Re-run `validate` and `render`.

If a source is missing, keep its metadata and mark access unavailable. Do not invent a locator. If the workspace schema is newer than the CLI supports, stop and request a compatible tool version.
