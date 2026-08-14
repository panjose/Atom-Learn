# Flexible progression and skip policy

## Contents

- Purpose
- Decision ladder
- Diagnostic mode
- Defer mode
- Provisional skip mode
- Restore and backtrack
- Course policies
- Exam and research behavior
- State and audit rules
- Interaction examples

## Purpose

Learners should not be forced through material they already know or do not need today. At the same time, self-reported familiarity is not the same as demonstrated mastery. AtomLearn therefore separates three actions:

- test out through a short diagnostic;
- defer the Atom without changing prerequisites;
- provisionally skip it as an explicit, reversible assumption.

The learner controls pace. The system controls labels, traceability, and the distinction between Evidence and assumption.

## Decision ladder

When the learner says “这个太简单了”, “我已经学过”, “跳过这里”, or an equivalent request:

1. distinguish “讲快一点” from an actual request to leave the Atom;
2. acknowledge the request without starting another explanation;
3. use a compressed current-turn explanation when that is all the learner wants;
4. otherwise offer a short diagnostic by default;
5. explain defer and provisional skip in one sentence each when useful;
6. follow the learner's explicit choice;
7. record the normalized reason rather than a raw chat quote;
8. validate and show the changed route.

Do not repeatedly pressure a learner to take a diagnostic after they explicitly choose a provisional skip. One clear disclosure and explicit confirmation are enough unless the course uses `strict_mastery`.

## Diagnostic mode

Run:

```text
python <SKILL_DIR>/scripts/atomlearn.py skip <workspace> <atom-id> --mode diagnostic --expected-revision <revision>
```

Diagnostic mode is read-only. It returns:

- the Atom objective and current status;
- every required mastery dimension;
- pass and per-dimension thresholds;
- central misconceptions;
- whether the Atom can currently be activated for assessment;
- a next-step recommendation.

Create one or two short prompts that cover all required dimensions. Reuse the normal Evidence path:

```text
python <SKILL_DIR>/scripts/atomlearn.py activate <workspace> <atom-id>
python <SKILL_DIR>/scripts/atomlearn.py record-evidence <workspace> --input <diagnostic-evidence.yaml>
python <SKILL_DIR>/scripts/atomlearn.py assess <workspace> <atom-id> --evidence-id <evidence-id>
```

If the Atom is locked, do not activate it illegally. Either repair prerequisites or let the learner explicitly choose a provisional skip. A passed diagnostic creates normal mastered Evidence and schedules review. A failed diagnostic returns to targeted remediation rather than replaying the whole lesson.

## Defer mode

Use defer when the learner means “not now”:

```text
python <SKILL_DIR>/scripts/atomlearn.py skip <workspace> <atom-id> --mode defer --reason-code time_constraint
```

The Atom becomes `deferred`. This action:

- removes it from `suggest-next`;
- releases it if it was the Active Atom;
- does not satisfy a prerequisite;
- does not unlock successors;
- does not claim mastery;
- remains visible in status and `PROGRESS.md`.

Deferring a remedial prerequisite prevents resuming its saved parent until the Atom is restored or another valid repair path is chosen.

## Provisional skip mode

Use a provisional skip only after offering a diagnostic and disclosing that the action is not Evidence:

```text
python <SKILL_DIR>/scripts/atomlearn.py skip <workspace> <atom-id> --mode provisional --reason-code already_mastered --confirmed
```

The Atom becomes `skipped`. This action:

- satisfies prerequisite traversal;
- may unlock successors;
- releases it if it was the Active Atom;
- records an explicit reason, confirmation, and timestamp;
- remains reversible;
- never sets confidence or creates Evidence;
- never schedules spaced review;
- never appears in the mastered count.

Use these normalized reason codes:

- `already_mastered`;
- `too_easy`;
- `not_relevant`;
- `time_constraint`;
- `different_goal`;
- `other`.

Use `--note` only for a short operational summary. Do not paste a raw conversation, sensitive information, or a long learner explanation into it.

## Restore and backtrack

Restore either decision with:

```text
python <SKILL_DIR>/scripts/atomlearn.py unskip <workspace> <atom-id> --expected-revision <revision>
```

The prior flexibility record receives `revoked_at`. The Atom returns to `available` or `locked` according to its current prerequisites. Restoration fails if the Atom is a direct prerequisite of the current Active Atom, because that would make the live session invalid. Leave the downstream Atom first, then restore.

When downstream performance exposes a gap in a skipped Atom:

1. record a `blocking_prerequisite` question;
2. run `backtrack --to <skipped-atom>`;
3. let backtracking revoke the active skip record;
4. teach and assess the reopened Atom normally;
5. resume the saved parent after the prerequisite is satisfied.

If the skipped Atom itself has unsatisfied prerequisites, repair the deepest missing prerequisite first.

## Course policies

Configure `course.settings.skip_policy` in a course plan:

```yaml
course:
  settings:
    skip_policy: diagnostic_first
```

Allowed values are:

- `diagnostic_first`: default; recommend a diagnostic and require explicit confirmation for provisional skip;
- `learner_choice`: preserve the same disclosure and audit record while letting the learner select the route immediately;
- `strict_mastery`: allow diagnostic and defer, but reject provisional prerequisite bypass.

Use `strict_mastery` for certification, regulated training, safety-critical procedures, or any course where path completion must imply demonstrated competence.

## Exam and research behavior

Exam preparation treats a skipped Atom as an assumption. Mixed and review queues can return `verify_skip` for exam-mapped skipped Atoms. Learning mode honors the skip. Deferred Atoms remain outside the direct queue, but if one blocks another high-priority target it remains an unmet prerequisite. Exam warnings distinguish both cases.

Research reading lets a skipped concept satisfy navigation into a paper but returns it under `provisional_knowledge_atom_ids`. If comprehension breaks, restore or backtrack without losing the Active Paper. Deferred and other unsatisfied concepts remain `knowledge_gap_atom_ids`.

Knowledge-lineage learning overlays expose both `skipped_atom_ids` and `deferred_atom_ids`. Use `trace` before restoring a skipped prerequisite when the missing context is unclear.

## State and audit rules

Each active decision is stored on the Atom as `flexibility` with exactly:

- `mode`: `provisional` or `defer`;
- `reason_code`;
- bounded `note`;
- `diagnostic_offered`;
- `confirmed`;
- `created_at`;
- `revoked_at`.

Core events record `atom.provisionally_skipped`, `atom.deferred`, or `atom.flexibility_revoked`. The course revision protects every mutation. Re-importing a plan preserves the Atom's current flexibility record.

Course status becomes `completed_with_skips` when every required Atom is mastered or provisionally skipped and at least one required Atom is skipped. This is path completion with assumptions. Only `completed` means every required Atom is in a mastery-like state.

Validation fails when a skipped/deferred status lacks a matching active record, when a provisional record lacks confirmation, when timestamps or reason codes are malformed, or when course completion labels hide provisional skips.

## Interaction examples

Learner: “This is easy; skip it.”

Recommended response: “可以。默认我先给你一个覆盖 explain/apply 的快速诊断；通过后会记录为真正掌握。你也可以明确选择 provisional skip，它会解锁后续，但只记录为假设，不算掌握。”

Learner: “No test, I know this already.”

Recommended action: state the assumption once, obtain confirmation, run provisional skip, then show the next available Atom.

Learner: “I don't need this today.”

Recommended action: defer it. Do not unlock dependent Atoms.

Learner later fails a downstream question that depends on the skipped Atom.

Recommended action: acknowledge that the provisional assumption did its job by remaining reversible, record the blocking question, backtrack, and assess the reopened Atom. Do not frame the learner's earlier choice as a mistake.
