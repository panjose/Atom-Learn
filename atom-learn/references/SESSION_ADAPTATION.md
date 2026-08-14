# Session-based learner adaptation

## Contents

- Principle
- Start and resume a session
- Extract safe preference signals
- Activation and conflict rules
- Apply guidance
- Correct or retire a preference
- Relationship to bounded evolution
- Privacy and safety

## Principle

Adapt presentation to the learner without evolving away learning integrity.

Use two independent lanes:

- session adaptation changes low-risk presentation choices such as detail, pacing, example type, language, feedback style, and research orientation;
- bounded evolution changes mastery, review schedules, dependencies, Atom structure, or Skill behavior through proposals and approval.

Keep adaptation revision independent from course and evolution revisions. A frequent preference update must not stale a pending structural proposal.

## Start and resume a session

Read canonical course state and the context-specific adaptation guidance:

```text
python <SKILL_DIR>/scripts/atomlearn.py status <workspace> --json
python <SKILL_DIR>/scripts/atomlearn.py adapt guidance <workspace> --context teaching
```

Use `orientation`, `teaching`, `review`, `research`, `exam`, or `general` context. Root `status --json` automatically includes orientation, teaching, or review guidance after adaptation has been initialized; exam preparation should request `exam` guidance explicitly.

Apply precedence in this order:

1. the learner's explicit request in the current turn;
2. an active explicit stored preference;
3. an active cross-session inferred preference;
4. the default AtomLearn protocol.

Never force a stored preference when the current task clearly needs another format. A learner who usually prefers concise answers may still request a detailed proof in the current turn.

## Extract safe preference signals

Observe once near the end of a meaningful session, or immediately after an explicit preference or correction that should persist. Do not write every conversational turn.

Classify signals as:

- `explicit`: the learner directly requests, confirms, corrects, or rejects a presentation choice;
- `behavioral`: repeated requests or format corrections suggest a preference but the learner did not state it as a durable rule;
- `outcome`: a presentation choice repeatedly correlates with task success, failure, mastery improvement, or struggle.

Pass only allowlisted dimensions, enum values, confidence, reason codes, and opaque turn references. Never pass message text, quotes, summaries, demographic labels, health information, political or religious identity, or personality diagnoses.

```text
python <SKILL_DIR>/scripts/atomlearn.py adapt observe-session <workspace> --input <session-signals.yaml> --expected-adaptation-revision <revision>
```

Use a stable opaque `session_id` once. If the harness retries, read adaptation status first instead of recording the same session again.

## Activation and conflict rules

- Activate an explicit `prefer` signal immediately.
- Let a newer explicit preference replace an older explicit value in the same dimension.
- Let an explicit `avoid` clear the same active value and suppress older inferred support until a later explicit `prefer` re-enables it.
- Keep behavioral and outcome signals provisional until at least two distinct sessions support the same value and confidence meets policy.
- Mark close competing inferred candidates `contested`; do not apply either one.
- Keep provisional and contested preferences out of active guidance.
- Allow a later explicit preference to reactivate a retired dimension.

Do not ask for confirmation after every inferred signal. Ask only when a contested or high-impact presentation choice materially affects the next activity.

## Apply guidance

Guidance converts active profile values into concrete instructions. Apply only dimensions valid for the current context. For example, `research.orientation` applies to research mapping, while challenge and feedback preferences can apply to exam preparation without changing difficulty statistics or mastery thresholds.

A pacing preference may cause the harness to offer shorter explanations or a diagnostic earlier. It must never create a provisional skip automatically. Only an explicit learner choice through the flexible-progression workflow may do that, and the result remains separate from mastery.

Active session preferences may change:

- response detail and structure;
- response language;
- intuition/example/formal explanation order;
- practical, code, visual, analogy, theoretical, or mixed examples;
- one-Atom, short-batch, or user-led pacing;
- direct, Socratic, guided-discovery, or mixed instruction;
- direct, supportive, or neutral feedback;
- plain, mixed, or formal notation;
- challenge level;
- breadth, depth, evidence, or application research orientation;
- user-material, primary-source, textbook, or mixed source priority.

They must never change:

- the one-Active-Atom invariant;
- prerequisite guards;
- mastery evidence requirements;
- source grounding and RAG coverage;
- safety or privacy rules;
- research inclusion/exclusion criteria without an explicit scope change.

## Correct or retire a preference

Record a learner correction as newer `explicit` evidence with `reason_code: user_correction`. Use retirement when the learner rejects persistence, requests privacy removal from active use, or says the preference is no longer relevant:

```text
python <SKILL_DIR>/scripts/atomlearn.py adapt retire <workspace> response.detail --reason-code privacy_request --expected-adaptation-revision <revision>
```

Retirement is an auditable tombstone. It removes the dimension from guidance without erasing history. No raw conversation exists in the adaptation ledger.

## Relationship to bounded evolution

Session adaptation is the low-risk presentation layer. It can activate explicit preferences immediately and corroborated inferred preferences automatically because its value space is allowlisted and reversible.

Run `evolve analyze` separately for outcome-level changes. Evolution metrics include only adaptation session counts and active preference counts. Do not convert a presentation preference directly into a mastery, dependency, structural, or Skill patch.

If an adapted style appears to improve or harm mastery, store ordinary learner Evidence and let the bounded evolution analyzer propose a monitored change. Preserve separate course, evolution, and adaptation revisions.

## Privacy and safety

- Keep `store_raw_messages: false`.
- Keep `cross_workspace_aggregation: false` unless a future explicit opt-in design is implemented.
- Keep `infer_sensitive_traits: false`.
- Reject unknown payload fields to prevent accidental raw-chat storage.
- Store only workspace-local preference signals.
- Use opaque session and turn references that contain no message text.
- Treat profile values as presentation preferences, not facts about identity, ability, or personality.
- Show the learner the generated `PERSONALIZATION.md` view when they ask what the system has learned.
