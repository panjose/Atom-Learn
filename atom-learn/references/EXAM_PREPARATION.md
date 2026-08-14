# Exam-question analysis and targeted preparation

## Contents

- Principle
- Accept question sources
- Build the structured corpus
- Determine difficulty
- Identify common knowledge points
- Generate a targeted plan
- Run learning and review
- Guardrails

## Principle

Use supplied exams as evidence about the sampled assessment corpus, not as an oracle for the next exam.

AtomLearn combines four distinct inputs:

- source-located past papers, mock exams, sample exams, or question banks;
- mappings from each question to stable knowledge-point IDs and optional Knowledge Atoms;
- a transparent difficulty rubric;
- current learner status, Evidence, reviews, and prerequisites.

Keep exam revision independent from course, RAG, and adaptation revisions. Importing another paper must not rewrite learning history, and normal learning must not alter the question corpus.

## Accept question sources

Accept PDF, DOCX, text, Markdown, image-derived text, or a user-created structured question list. For document inputs:

1. initialize RAG and ingest the source in the learner workspace;
2. retain a stable `source_id` and per-question locator;
3. use structure-aware extraction and OCR supplied by the harness when the document is scanned;
4. retrieve the relevant marking scheme, syllabus, or solution source when available;
5. use corrective Web Search only for missing official context, then ingest bounded evidence with provenance.

Do not copy full question text into exam canonical state. Keep the original in the user source/RAG layer and store only a concise `stem_summary`, question number, and locator.

If exam questions are the only course input, first treat them as source intake, identify prerequisite concepts, and build the Knowledge Atom graph. Then import the exam mappings. Avoid building a course that teaches only memorized answers or repeats the sampled papers verbatim.

## Build the structured corpus

Read [EXAM_SCHEMA.md](EXAM_SCHEMA.md). For extracted question/answer/marking documents, start with the automatic processor; for an already structured corpus, use the manual import:

```text
python <SKILL_DIR>/scripts/atomlearn.py exam init <workspace> --title <title> [--target-date YYYY-MM-DD]
python <SKILL_DIR>/scripts/atomlearn.py exam process <workspace> --input <exam-process.yaml> --expected-exam-revision <revision>
python <SKILL_DIR>/scripts/atomlearn.py exam review-mappings <workspace> --input <exam-mapping-review.yaml> --expected-exam-revision <revision>
python <SKILL_DIR>/scripts/atomlearn.py exam import <workspace> --input <exam-import.yaml> --expected-exam-revision <revision>
```

For every question, record:

- the paper, number, points, type, cognitive levels, and source locator;
- a concise stem summary and optional stable family ID for related variants;
- five difficulty factors and their confidence/basis;
- one or more knowledge-point mappings whose weights total `1.0`;
- an existing Atom ID when the course graph covers the point, or `null` when it does not.

The processor automatically splits stable question numbers, associates matching answers and marking sections by number, derives locators, and proposes mappings/difficulty. Inspect its diagnostics and review queue. Use the same knowledge-point ID, label, and Atom mapping throughout the corpus. Do not force a weak mapping to obtain 100% coverage. Preserve it as an explicit coverage gap.

## Determine difficulty

Rate every factor from `1` through `5`:

- conceptual load;
- reasoning depth;
- knowledge integration;
- execution load;
- time pressure.

The runtime computes a weighted estimate and a band from `foundation` through `challenge`. Record whether the basis is an official rating, an explicit rubric, or a weaker estimate. If official levels exist, retain them and run `exam calibrate`: the learned offset adjusts non-official estimates, reports before/after MAE, and leaves the official anchors unchanged.

Difficulty describes the task under the stated conditions. It is not a claim about the learner's intelligence. Lower confidence when the marking scheme, time allowance, prerequisite assumptions, or expected solution path is missing.

## Identify common knowledge points

Run:

```text
python <SKILL_DIR>/scripts/atomlearn.py exam analyze <workspace>
```

The deterministic analysis combines:

- cross-paper coverage;
- weighted occurrence share;
- assigned-score share when points are available;
- mapping confidence and number of distinct papers;
- question type, cognitive level, and difficulty distributions.

Report `core`, `frequent`, `recurring`, or `limited` only for the supplied corpus. Always show paper count, question count, years, score share, difficulty, and confidence beside the tier. State sample limitations and never convert the emphasis score into a probability of appearing on a future exam.

## Generate a targeted plan

Run:

```text
python <SKILL_DIR>/scripts/atomlearn.py exam plan <workspace> --mode mixed --limit 10
```

Choose `learning`, `review`, or `mixed`. The queue combines corpus emphasis, current learner gap, and question difficulty. It then applies prerequisite order and returns one of these actions:

- `repair_prerequisites`;
- `verify_skip`;
- `learn`;
- `remediate`;
- `review`.

Every queue entry includes its score components, Evidence status, prerequisite IDs, mapped knowledge points, and representative question IDs. Unmapped points remain coverage gaps and cannot silently influence a targeted Atom plan.

If an exam-mapped Atom has a detailed expansion, its computed prerequisite closure reaches the ordered child Atoms before the parent integration check. Do not bypass that branch by presenting one exam-focused summary of every child.

`verify_skip` marks an exam-relevant Atom that the learner provisionally skipped without mastery Evidence. Mixed and review modes may surface it for a short diagnostic; learning mode honors the skip. Deferred Atoms remain outside the direct queue, while a deferred prerequisite can still block another target. Never silently convert either decision into mastery.

When a target date is configured, the plan reports days remaining and warns on an expired or seven-day horizon. Time pressure may change iteration length, but it must not remove prerequisite or mastery guards.

Read `adapt guidance --context exam` and apply presentation/challenge preferences without changing corpus statistics or mastery thresholds.

## Run learning and review

1. Start with the top queue entry, but repair its prerequisites first. When lineage is initialized, run `lineage trace <workspace> <atom-id>` to expose the target's full prerequisite and conceptual context.
2. Teach the Atom from source-grounded principles, not from one memorized answer pattern.
3. Select a representative question at an appropriate difficulty; withhold the solution while the learner attempts it.
4. Record the attempt as normal `diagnostic`, `mastery_check`, or `review` Evidence for the Active Atom.
5. Assess through the Atom rubric, then rerun `exam plan`; learner gap and next action will update from canonical Evidence.
6. Mix transfer variants and contrast cases so repeated past-paper forms do not create false mastery.

Do not mark an Atom mastered merely because the learner recognized a past question. Do not schedule all high-frequency points ahead of a blocking prerequisite.

## Guardrails

- Keep full question sources and marking schemes in the source/RAG layer.
- Separate official difficulty from rubric estimates.
- Preserve low-confidence and unmapped results instead of inventing certainty.
- Treat score weights and frequency as corpus descriptors, not forecasts.
- Do not infer learner ability from task difficulty.
- Do not expose solutions before a diagnostic attempt unless the learner explicitly asks for worked examples.
- Run `exam validate` and root `validate` after every import.
- Regenerate `EXAM_BLUEPRINT.md` and `EXAM_STUDY_PLAN.md` after corpus or learning-state changes.
