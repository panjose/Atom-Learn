# Unified start wizard

## Purpose

`start` reduces first-use input to one learner request. It is a resumable orchestrator over the existing canonical course, intake, and RAG engines; it does not create a second source of truth.

Use either:

```text
python <SKILL_DIR>/scripts/atomlearn.py start <workspace> --topic "named topic"
python <SKILL_DIR>/scripts/atomlearn.py start <workspace> --input <start.yaml>
```

The shortest form infers a stable course ID, title, goal, topic intake, and initial coverage requirements. The structured form accepts complete sources, a compact outline, a topic, or mixed inputs with an explicit primary `mode`.

## State machine

1. `initialized`: create the course workspace, intake state, local RAG index, and source chunks.
2. `web_search_required`: return one harness task per weak, missing, or unverified sparse-input requirement.
3. `course_plan_required`: return a plan contract listing the allowed source IDs and grounding rules.
4. `complete`: import the plan, finish intake traceability checks, and hand off to `suggest-next`.

Run the same command again to resume. `.atomlearn/start.yaml` stores only stage, revisions, source IDs, and the last task summary. Full private source text remains in the RAG index, not in wizard state. `START.md` is generated and non-canonical.

## One payload contract

The authoritative machine-readable contract is [start.schema.json](../assets/schemas/start.schema.json). Print it with:

```text
python <SKILL_DIR>/scripts/atomlearn.py start unused --print-schema
```

Initial fields include:

- `topic` or `topic_terms`: one name or a list;
- `outline`: title strings or objects with stable IDs and optional parents;
- `sources`: path, inline text, or structured passages with source metadata;
- optional `course_id`, `title`, `goal`, outcome, depth, prior knowledge, constraints, assumptions, and OCR settings.

Resume fields include:

- `web_evidence`: provenance-complete bounded passages produced by harness Web Search;
- `verdicts`: direct-support judgments over the refreshed requirement candidates;
- `course_plan`: the normal AtomLearn plan mapping.

The schema deliberately permits a resume-only payload. Runtime stateful validation requires initial content when the workspace does not exist and rejects plan import until sparse-input coverage passes.

## Source and outline normalization

Relative source paths resolve against the start payload file. Source IDs and outline IDs are generated deterministically when omitted. For outline input, the wizard creates and indexes a stable inline outline source so every coverage item and later Atom can retain a locator. For topic mode, accepted Web evidence is also registered as discovery-source metadata before coverage is reevaluated.

## Failure behavior

- Invalid JSON/YAML fails before workspace creation.
- JSON Schema errors name the exact failing field.
- Existing non-wizard workspaces are never silently adopted.
- A failed step leaves canonical subsystem state intact and the wizard remains resumable.
- A workspace with an imported plan refuses a second wizard plan; later changes use `import-plan` with revision protection.
