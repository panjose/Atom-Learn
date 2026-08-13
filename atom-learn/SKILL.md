---
name: atom-learn
description: Build and run persistent, source-grounded learning courses by turning textbooks, PDFs, notes, documentation, or multiple learning resources into a prerequisite DAG of small Knowledge Atoms. Use when a learner wants a controlled study path, one-concept-at-a-time tutoring, durable progress across sessions, question parking and prerequisite backtracking, mastery checks, spaced review, or recovery of an existing AtomLearn workspace.
---

# AtomLearn

Follow the Atom Principle:

> Never advance while the current atom remains unclear.

Maintain exactly one Active Atom. Permit unlimited questions, prerequisite review, and parked side questions without losing the current learning state. Advance only after recorded mastery evidence passes the Atom's rubric.

## Locate the runtime

Set `SKILL_DIR` to this Skill directory and invoke:

```text
python <SKILL_DIR>/scripts/atomlearn.py <command> ...
```

Treat `.atomlearn/` YAML as canonical state. Treat `LEARNING_MAP.md`, `CURRENT.md`, `PROGRESS.md`, `QUESTIONS.md`, and `SOURCES.md` as generated views. Do not edit generated views to mutate state.

## Choose a workflow

### Create a course

1. Choose the user's requested workspace. If none is given, create a clearly named `<course-id>-atomlearn` subdirectory and tell the user.
2. Read [references/PROTOCOL.md](references/PROTOCOL.md), [references/SCHEMA.md](references/SCHEMA.md), and [references/ATOMIZATION.md](references/ATOMIZATION.md).
3. Inspect supplied sources with the appropriate file or web tools. Keep private source material out of the Skill directory and repository.
4. Run `init` before building the map.
5. Create an import plan that follows the schema. Prefer 10-30 Atoms in the first batch; extend large courses incrementally.
6. Run `import-plan`, then `validate` and `render`.
7. Summarize the map, ambiguities, conflicts, and first available Atom. Do not start a long lecture during orientation.

```text
python <SKILL_DIR>/scripts/atomlearn.py init <workspace> --course-id <id> --title <title> --goal <goal>
python <SKILL_DIR>/scripts/atomlearn.py import-plan <workspace> --input <plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py render <workspace>
```

### Resume a course

1. Run `status --json`; do not rely on chat history.
2. Read only the Active Atom, referenced questions, and necessary source locations.
3. Restate the current Atom, learner confusion, and next action in one short orientation.
4. Continue the recorded phase. Do not reactivate or advance an Atom merely because a new session started.

### Teach one turn

1. Read `status --json` and note its `revision`.
2. Interpret the input as an answer, a question, a state command, or a scope change.
3. Route questions using [references/QUESTION_ROUTING.md](references/QUESTION_ROUTING.md). Record the question before taking a routing action.
4. Teach only the minimum needed for the Active Atom, using Why -> What -> How -> Example -> Intuition.
5. Persist current question, understood ideas, confusions, and `next_action` with `update-session`.
6. Run `validate` after a state-changing command. Use `--expected-revision` on mutations when supported.
7. Keep the user-facing reply focused; mention parked or backtracked questions explicitly.

Do not teach a future Atom to be conversationally helpful. Record it and return to the current objective.

### Check mastery and advance

1. Read [references/MASTERY.md](references/MASTERY.md) before designing or grading a check.
2. Ask for observable performance; never use "Do you understand?" as the only check.
3. Save the prompt, response summary, dimension scores, feedback, and evaluator rationale with `record-evidence`.
4. Run `assess`. Let the CLI derive `mastered`, `partial`, or `not_mastered` from the Atom rubric.
5. If not mastered, target the weakest dimension and keep the Atom active.
6. If mastered, render progress, use `suggest-next`, and activate a successor only when the learner asks to continue or the active learning request clearly authorizes continuation.

Never mark an Atom mastered without persisted Evidence.

### Handle prerequisite backtracking

1. Record the blocking question.
2. Run `backtrack --to <atom-id> --question-id <id>`.
3. Teach and assess the prerequisite as an Active Atom.
4. Run `resume` only after the remedial Atom is mastered and no Atom remains active.
5. Continue the saved parent question and next action.

### Review and restructure

- Run `refresh-reviews` at the beginning of a study session. Prefer a due review before a new Atom unless the learner asks otherwise.
- Use `restructure` only after reading [references/ATOMIZATION.md](references/ATOMIZATION.md). Generate a proposal first. Apply it only with explicit user confirmation and `--confirmed`.
- Preserve archived Atom IDs, aliases, questions, and Evidence. Never erase learning history during split or merge.

## State command rules

- Pass semantic payloads through YAML/JSON input files; do not construct complex shell strings from learner text.
- Use stable lowercase dot-separated Atom IDs such as `calculus.derivative.definition`.
- Run `validate` before and after manual recovery or structural changes.
- Stop and explain a validation error. Do not bypass a guard by editing generated Markdown.
- Avoid putting full copyrighted sources into state. Store source metadata, short notes, and stable locators.

## Reference routing

- Read [references/SCHEMA.md](references/SCHEMA.md) when creating plans, payloads, or troubleshooting validation.
- Read [references/PROTOCOL.md](references/PROTOCOL.md) for orientation, teaching, recovery, and response behavior.
- Read [references/ATOMIZATION.md](references/ATOMIZATION.md) when building or restructuring a map.
- Read [references/QUESTION_ROUTING.md](references/QUESTION_ROUTING.md) when a learner asks a side question or reveals a prerequisite gap.
- Read [references/MASTERY.md](references/MASTERY.md) when creating checks, grading Evidence, or scheduling remediation.

## Completion standard

Consider an interaction complete only after canonical state is saved, `validate` passes, generated views are refreshed, and the learner is told the current Atom and next action. Consider a course complete only when all non-optional, non-archived Atoms are mastered and no blocking question remains open.
