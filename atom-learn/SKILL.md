---
name: atom-learn
description: Build, run, and safely evolve persistent source-grounded learning courses and research-reading programs. Accept complete textbooks or knowledge bases, a user-provided outline or syllabus, or only a field keyword, concept, skill, or topic name; turn the input into a prerequisite DAG of Knowledge Atoms; map research fields into guided paper graphs; and track learning evidence. Use for course creation from sparse or rich inputs, one-concept-at-a-time study, durable progress, mastery checks, spaced review, adaptive teaching, critical paper reading, literature synthesis, field orientation, or recovery of an AtomLearn workspace.
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

Treat `.atomlearn/` YAML as canonical state. Treat root Markdown views, including learning, evolution, and research views, as generated. Do not edit generated views to mutate state.

## Choose a workflow

### Start from any input

1. Create the base workspace with `init`.
2. Read [references/COURSE_INTAKE.md](references/COURSE_INTAKE.md) and [references/INTAKE_SCHEMA.md](references/INTAKE_SCHEMA.md).
3. Classify the primary input as `sources`, `outline`, or `topic`. Use the most information-rich mode and retain secondary inputs.
4. Create an intake payload from the matching template and run `intake init` followed by `intake guidance`.
5. For full sources, inspect and inventory the content. For an outline, preserve coverage IDs but redesign Atom boundaries and dependencies. For a topic name, disambiguate it, make explicit assumptions, and discover authoritative sources without requiring the learner to create a syllabus.
6. Ask only questions that materially change the path. Continue with recorded assumptions when uncertainty is non-blocking.
7. Build and import a source-grounded plan, then run `intake complete`, `validate`, and `render`.

```text
python <SKILL_DIR>/scripts/atomlearn.py init <workspace> --course-id <id> --title <title> --goal <goal>
python <SKILL_DIR>/scripts/atomlearn.py intake init <workspace> --input <intake.yaml>
python <SKILL_DIR>/scripts/atomlearn.py intake guidance <workspace>
python <SKILL_DIR>/scripts/atomlearn.py import-plan <workspace> --input <plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py intake complete <workspace>
```

Never ask a topic-only user to supply a complete outline. Never treat a source table of contents or user outline as the final prerequisite graph. Keep every non-archived Atom traceable to a source locator.

### Create a course

1. Choose the user's requested workspace. If none is given, create a clearly named `<course-id>-atomlearn` subdirectory and tell the user.
2. Read [references/PROTOCOL.md](references/PROTOCOL.md), [references/SCHEMA.md](references/SCHEMA.md), and [references/ATOMIZATION.md](references/ATOMIZATION.md).
3. Complete the applicable intake workflow. Keep private source material out of the Skill directory and repository.
4. Create an import plan that follows the schema. Prefer 10-30 Atoms in the first batch; extend large courses incrementally.
5. Run `import-plan`, `intake complete` when intake state exists, then `validate` and `render`.
6. Summarize the map, assumptions, ambiguities, conflicts, source gaps, and first available Atom. Do not start a long lecture during orientation.

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

### Map and read a research field

1. Create the base workspace with `init`. Build Knowledge Atoms when the field has concepts or methods the learner may need to repair.
2. Read [references/RESEARCH_READING.md](references/RESEARCH_READING.md) and [references/RESEARCH_SCHEMA.md](references/RESEARCH_SCHEMA.md).
3. Define a research question, scope, inclusion criteria, exclusion criteria, and intended outcome before collecting papers.
4. Build an initial map of representative roles: survey, seminal, theory or method families, benchmarks or datasets, critiques or replications, and applications. Verify bibliographic metadata; do not equate citation count with evidence quality.
5. Run `research init`, create an import plan, then run `research import`, `research validate`, and `research next`.
6. Keep one Active Paper. If `research next` reports Knowledge Atom gaps, repair them through the learning workflow without losing the paper position.
7. Read in triage, structure, and evidence passes. Save a critical note with `research note`; mark it complete only after the critical-reading guard passes.
8. Run `research synthesize` after a coherent group is complete. Report agreements, contradictions, replications, recurring limitations, open questions, and search limits.

```text
python <SKILL_DIR>/scripts/atomlearn.py research init <workspace> --field <field> --question <question> --scope <scope>
python <SKILL_DIR>/scripts/atomlearn.py research import <workspace> --input <research-plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research next <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research activate <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research note <workspace> <paper-id> --input <note.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research complete <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research synthesize <workspace>
```

Do not call an observed open question a novel contribution without a current literature search. Do not mark a paper read from an abstract-only summary. Do not store complete paper text in canonical state.

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

### Evolve from evidence

1. Read [references/EVOLUTION.md](references/EVOLUTION.md), [references/EVOLUTION_POLICY.md](references/EVOLUTION_POLICY.md), and [references/EVALUATION.md](references/EVALUATION.md).
2. Run `evolve status` and note both course and evolution revisions.
3. Run `evolve analyze --propose` only after meaningful Evidence, review failure, repeated backtracking, or an explicit learner request.
4. Preview every proposal. Explain its observations, hypothesis, risk, expected effect, and validation result.
5. Obtain the policy-required authority before approval and application.
6. Monitor with new Evidence. Promote only when all criteria pass.
7. Roll back only when no learning mutation occurred after application; otherwise create a compensating proposal.

Keep evolution in `proposal_only` mode by default. Never apply `patch_skill` from a course workspace.

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
- Read [references/EVOLUTION.md](references/EVOLUTION.md) for the end-to-end evolution workflow.
- Read [references/EVOLUTION_POLICY.md](references/EVOLUTION_POLICY.md) before approval, application, or rollback.
- Read [references/EVALUATION.md](references/EVALUATION.md) when defining success criteria or monitoring a proposal.
- Read [references/RESEARCH_READING.md](references/RESEARCH_READING.md) when mapping a field, choosing a reading order, reading papers, or identifying evidence-linked gaps.
- Read [references/RESEARCH_SCHEMA.md](references/RESEARCH_SCHEMA.md) when creating paper import plans or critical notes, or troubleshooting research state.
- Read [references/COURSE_INTAKE.md](references/COURSE_INTAKE.md) when the user supplies full sources, an outline, mixed materials, or only a topic name.
- Read [references/INTAKE_SCHEMA.md](references/INTAKE_SCHEMA.md) when creating or updating an intake payload, or troubleshooting intake state.

## Completion standard

Consider an interaction complete only after canonical state is saved, `validate` passes, generated views are refreshed, and the learner is told the current Atom or Paper and next action. When intake state exists, complete it only after source traceability passes. Consider a course complete only when all non-optional, non-archived Atoms are mastered and no blocking question remains open. Consider a research synthesis complete only when included papers have critical notes, cross-paper relations are represented, open questions and contradictions are explicit, and search limits are stated.
