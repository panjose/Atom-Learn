# Session adaptation schema

## Contents

- Runtime files
- Observation payload
- Dimensions and values
- Evidence and reason codes
- Profile states
- Commands

## Runtime files

Canonical state lives in `.atomlearn/adaptation/`:

- `state.yaml`: adaptation revision, privacy policy, thresholds, and session count;
- `profile.yaml`: derived active, provisional, contested, and retired preferences;
- `signals.ndjson`: enum-only preference signals and retirement tombstones;
- `ledger.ndjson`: mutation events containing IDs, not conversation content.

`PERSONALIZATION.md` is generated. Do not edit it to mutate adaptation state.

Adaptation revision is independent from course, intake, RAG, research, and evolution revisions. Mutations accept `--expected-adaptation-revision`.

## Observation payload

```yaml
session_id: session-2026-08-14-a
context: teaching
signals:
  - dimension: response.detail
    value: concise
    direction: prefer
    evidence: explicit
    reason_code: explicit_request
    confidence: 0.95
    turn_refs: [turn-18]
```

Top-level fields are exactly `session_id`, `context`, and `signals`. Signal fields are exactly `dimension`, `value`, `direction`, `evidence`, `reason_code`, `confidence`, and `turn_refs`. Unknown fields are rejected.

Constraints:

- session and turn references are opaque IDs using letters, numbers, dot, colon, underscore, or hyphen;
- one session ID may be observed only once;
- context is `general`, `orientation`, `teaching`, `review`, or `research`;
- one observation contains 1-20 signals;
- confidence is 0.5-1.0;
- one signal has at most ten turn references;
- raw text, message content, quotes, free-text rationale, and sensitive traits have no schema field.

## Dimensions and values

| Dimension | Values |
| --- | --- |
| `response.detail` | `concise`, `balanced`, `detailed` |
| `answer.structure` | `prose`, `checklist`, `step_by_step`, `mixed` |
| `language.mode` | `chinese`, `english`, `bilingual`, `match_user` |
| `explanation.order` | `intuition_first`, `example_first`, `formal_first`, `mixed` |
| `example.mode` | `practical`, `code`, `visual`, `analogy`, `theoretical`, `mixed` |
| `interaction.pacing` | `one_atom`, `short_batch`, `user_led` |
| `teaching.mode` | `direct`, `socratic`, `guided_discovery`, `mixed` |
| `feedback.style` | `direct`, `supportive`, `neutral` |
| `notation.level` | `plain`, `mixed`, `formal` |
| `challenge.level` | `gentle`, `standard`, `stretch` |
| `research.orientation` | `breadth_first`, `depth_first`, `evidence_first`, `application_first` |
| `source.priority` | `user_materials`, `primary_sources`, `textbooks`, `mixed` |

Direction is `prefer` or `avoid`.

## Evidence and reason codes

| Evidence | Reason codes |
| --- | --- |
| `explicit` | `explicit_request`, `user_confirmation`, `user_correction`, `user_rejection` |
| `behavioral` | `repeated_request`, `format_correction`, `accepted_format`, `abandoned_format` |
| `outcome` | `task_success`, `task_failure`, `mastery_improved`, `mastery_struggled` |

Do not label a behavioral inference `explicit`. Do not infer a durable preference merely because the learner accepted one response without correction.

## Profile states

- `active`: usable in guidance;
- `provisional`: insufficient distinct-session support;
- `contested`: competing inferred values are too close;
- `retired`: excluded from guidance by an auditable tombstone.

`source` is `explicit`, `inferred`, `unconfirmed`, or `retired`. Candidate records contain scores, session counts, and signal IDs, not message content.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py adapt status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py adapt profile <workspace>
python <SKILL_DIR>/scripts/atomlearn.py adapt guidance <workspace> --context teaching
python <SKILL_DIR>/scripts/atomlearn.py adapt observe-session <workspace> --input <session-signals.yaml>
python <SKILL_DIR>/scripts/atomlearn.py adapt retire <workspace> <dimension> --reason-code <reason>
python <SKILL_DIR>/scripts/atomlearn.py adapt validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py adapt render <workspace>
```
