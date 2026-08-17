# Unified start wizard

## Purpose

`start` reduces first-use input to one learner request. It is a resumable orchestrator over the existing canonical course, intake, and RAG engines; it does not create a second source of truth.

Use either:

```text
python <SKILL_DIR>/scripts/atomlearn.py start <workspace> --topic "named topic"
python <SKILL_DIR>/scripts/atomlearn.py start <workspace> --input <start.yaml>
python <SKILL_DIR>/scripts/atomlearn.py start <workspace> --json
```

The shortest form infers a stable course ID, title, goal, topic intake, initial coverage requirements, and disclosed default assumptions. The structured form accepts complete sources, a compact outline, a topic, or mixed inputs with an explicit primary `mode`. The default console is a short English/Chinese status; `--json` exposes the typed protocol used by the harness.

The learner makes one request and does not edit intermediate YAML. The harness translates that request into the initial payload, executes the returned actions, and submits typed results. Read [WORKFLOW_ACTIONS.md](WORKFLOW_ACTIONS.md) for that loop and its trust boundary.

## State machine

1. `initialized` / `clarification_required`: create course, intake, RAG, and Document IR state; ask only high-impact scope questions.
2. `coverage_judgment_required`: retrieve candidates for every Goal Contract anchor and ask the harness to judge only those candidates.
3. `web_search_required` or `corpus_gap_reported`: correct unsupported anchors only when Corpus Policy permits external expansion; otherwise expose the gap and ask the learner to narrow the goal, add sources, or explicitly change policy.
4. `course_plan_required`: return a plan contract listing allowed source IDs, the Goal Contract, Corpus Policy, and grounding rules.
5. `phase_confirmation_required`: validate the proposed plan in a preview workspace and ask for confirmation before importing it.
6. `first_atom_confirmation_required`: complete intake and show the first eligible Atom without activating it.
7. `complete`: activate the learner-confirmed first Atom and hand off to atomic teaching.

Run the same command again to resume. With `--json` and no new payload, Core returns the exact current action without changing the revision. `.atomlearn/start.yaml` stores stage, revisions, source IDs, and the current typed action. Full private source text remains in RAG and Document IR, not in wizard state. `START.md` is generated and non-canonical.

## One payload contract

The authoritative machine-readable contract is [start.schema.json](../assets/schemas/start.schema.json). Print it with:

```text
python <SKILL_DIR>/scripts/atomlearn.py start unused --print-schema
```

Initial fields include:

- `topic` or `topic_terms`: one name or a list;
- `outline`: title strings or objects with stable IDs and optional parents;
- `sources`: path, inline text, or structured passages with source metadata;
- optional `course_id`, `title`, `goal`, outcome, depth, prior knowledge, constraints, assumptions, explicit `mandatory_anchors`, `corpus_policy`, and OCR settings.

Resume fields include:

- `web_evidence`: provenance-complete bounded passages produced by harness Web Search;
- `verdicts`: direct-support judgments over the current requirement-specific candidates, used both before and after external correction;
- `course_plan`: the normal AtomLearn plan mapping.
- `confirmed` and `activate_first`: direct CLI confirmation flags; typed harness operation submits the equivalent action result.

The schema deliberately permits a resume-only payload. Runtime stateful validation requires initial content when the workspace does not exist and rejects plan import until universal Goal Contract coverage passes.

The public action and submission contracts are [workflow-action.schema.json](../assets/schemas/workflow-action.schema.json) and [workflow-submission.schema.json](../assets/schemas/workflow-submission.schema.json). Prefer `--submission <file> --json` for harness operation. An action is bound to its ID, wizard revision, and idempotency key; stale or cross-action submissions are rejected.

## Source and outline normalization

Relative source paths resolve against the start payload file. Source IDs and outline IDs are generated deterministically when omitted. Whenever an outline is present, including mixed input, the wizard creates and indexes a stable inline outline source so every coverage item and later Atom can retain a locator. Accepted Web evidence is registered as discovery-source metadata for every primary mode before coverage is reevaluated.

## Failure behavior

- Invalid JSON/YAML fails before workspace creation.
- JSON Schema errors name the exact failing field.
- Existing non-wizard workspaces are never silently adopted.
- A failed step leaves canonical subsystem state intact and the wizard remains resumable.
- `closed_corpus` never emits a Web Search action and rejects direct Web evidence ingestion.
- A proposed plan is not imported until phase confirmation, and its first Atom is not activated until a second explicit confirmation.
- Replaying the current action is read-only; replaying an old submission after progress fails as stale.
- A workspace with an imported plan refuses a second wizard plan; later changes use `import-plan` with revision protection.
