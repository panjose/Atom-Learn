# Course intake workflows

## Contents

- Choose an intake mode
- Full sources or knowledge bases
- User-provided outlines
- Topic names or keywords
- Mixed inputs
- Readiness and completion

## Choose an intake mode

Classify the primary input before building the Knowledge Atom map:

- `sources`: the user supplies one or more textbooks, PDFs, notes, documentation sets, websites, or knowledge bases with substantive content;
- `outline`: the user supplies a syllabus, chapter list, curriculum, or structured topic outline without complete explanatory content;
- `topic`: the user supplies only a field name, keyword, concept, skill, or desired subject.

Use the most information-rich primary mode. A textbook with a table of contents remains `sources`. A short syllabus with a few references remains `outline`. Mixed inputs may retain secondary fields while using one primary mode.

Create the base workspace, write an intake payload, and run:

```text
python <SKILL_DIR>/scripts/atomlearn.py intake init <workspace> --input <intake.yaml>
python <SKILL_DIR>/scripts/atomlearn.py intake guidance <workspace>
```

Then initialize retrieval. Read [RAG.md](RAG.md) before indexing material or using Web Search.

Record concise assumptions and ambiguities instead of silently resolving them. Ask only questions whose answers materially change the path; proceed with explicit assumptions for non-blocking uncertainty.

## Full sources or knowledge bases

1. Inventory every supplied source, version, and stable location.
2. Inspect structure with the appropriate PDF, document, filesystem, browser, or connector tools.
3. Build a cross-source concept registry before defining Atoms.
4. Merge duplicate explanations; preserve material disagreements and version differences.
5. Derive prerequisite edges from conceptual dependency, not chapter order.
6. Attach every Atom to source IDs and precise locators.
7. Flag prerequisites that the supplied materials assume but do not explain.

Do not copy complete copyrighted material into state. Store metadata, locators, short notes, and the minimum excerpts allowed by the active tool and source policy.

## User-provided outlines

Treat the outline as a coverage contract, not a finished learning graph.

1. Give every outline item a stable ID and preserve its hierarchy.
2. Register the outline as a source, normally `user-outline`.
3. Split headings that combine several independently testable ideas.
4. Merge repeated headings that express the same learning objective.
5. Infer prerequisite edges across outline sections.
6. Add missing bridge Atoms and label them as inferred.
7. Use outline item IDs as source locators so coverage remains auditable.

If explanatory sources are absent, the outline is not ready to plan. Index the outline, generate one retrieval requirement per outline ID, use corrective harness Web Search for missing explanations, and pass the explicit RAG coverage gate.

## Topic names or keywords

Do not require the user to invent a syllabus.

1. Normalize aliases and disambiguate the term's most likely meaning.
2. Infer a practical initial goal from the request and record the assumption.
3. Ask at most one or two high-value questions about outcome or boundary when needed.
4. Discover at least one authoritative overview and one primary or technical source when appropriate.
5. Open the results, ingest bounded evidence with full provenance, and record discovered source metadata with `intake update`.
6. Generate and evaluate topic and goal-level RAG requirements. Require two distinct sources for the overall goal.
7. Build a provisional 10-30 Atom map covering vocabulary, foundations, mechanisms, representative applications, and common misconceptions.
8. Label uncertain scope, prerequisites, and source gaps.
9. Show the orientation map and refine it from learner feedback and diagnostic Evidence.

If current recommendations, versions, standards, or research coverage matter, verify them with current primary sources. Never present a topic-only map as exhaustive.

## Mixed inputs

Use the strongest input as the primary mode and preserve the others:

- textbook plus outline: use `sources`; use outline items as coverage checks;
- outline plus keywords: use `outline`; use keywords to clarify emphasis;
- several sources plus a target project: use `sources`; set `desired_outcome: project` and record constraints;
- textbooks plus past exams: use `sources`, set `desired_outcome: exam`, build the prerequisite graph from explanatory material, and use the separate exam workflow for question statistics and targeted preparation;
- past exams as the only material: use `sources` with source type `exam`, discover the explanatory prerequisites needed for a sound course, then map questions through [EXAM_PREPARATION.md](EXAM_PREPARATION.md);
- research field plus papers: use research mode for the paper graph and topic intake for prerequisite Knowledge Atoms.

## Readiness and completion

`sources` becomes `ready_to_plan` after valid intake capture. `outline` remains `discovering` until every outline anchor passes RAG coverage. `topic` remains `discovering` until authoritative discovery sources are recorded and topic/goal coverage passes. Coverage must match the current intake revision; an intake update makes the previous report stale.

After creating and importing the course plan, run:

```text
python <SKILL_DIR>/scripts/atomlearn.py intake complete <workspace> --expected-intake-revision <revision>
python <SKILL_DIR>/scripts/atomlearn.py validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py render <workspace>
```

Completion requires:

- at least one imported Knowledge Atom;
- a passed current RAG coverage report for outline and topic intake;
- the intake source IDs represented in course sources;
- source locators on every non-archived Atom;
- a valid course and intake state.
