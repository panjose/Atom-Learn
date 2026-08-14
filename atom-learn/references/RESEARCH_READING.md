# Research reading workflow

## Contents

- Principle
- Scope a field
- Build the paper map
- Read one paper
- Synthesize across papers
- Identify research gaps
- Commands

## Principle

Organize research reading around a question and an evidence graph, not a pile of summaries. Use Knowledge Atoms for prerequisite concepts and Papers for claims, methods or arguments, evidence, limitations, and relations to the field.

Maintain at most one Active Paper. Do not mark a paper read merely because its abstract or introduction was summarized. Completion requires a critical note with the problem, contribution, approach, evidence-linked claims, limitations, and field positioning.

## Scope a field

Before searching, record:

- the research question;
- the field and relevant neighboring areas;
- time, population, task, language, or venue scope;
- inclusion and exclusion criteria;
- the intended outcome, such as orientation, method selection, replication, or novelty assessment.

If the user asks for current coverage or a novelty claim, search current primary literature and verify bibliographic metadata. Separate observed metadata from inferred relevance. Never present an incomplete search as an exhaustive review.

## Build the paper map

Prefer an initial map of 8-20 representative papers, then expand deliberately. Cover roles rather than selecting only highly cited or recent work:

1. surveys for vocabulary, branches, and debates;
2. seminal papers for original problem framing;
3. theory and representative method families;
4. benchmarks and datasets that define the evidence surface;
5. critiques and replications that test accepted claims;
6. applications and recent challengers after foundations are clear.

Use `prerequisite_paper_ids` for the reading order. Use `cites` for internal citation links. Put references outside the imported set in `external_citations`. Link `concept_atom_ids` when a paper assumes knowledge that the learner may need to repair. When lineage is initialized, trace those Atoms to expose the smallest prerequisite and conceptual repair route while preserving the Active Paper.

Import normalizes DOI forms and merges exact DOI/title duplicates before validating the paper graph. Run `research reconcile-metadata` with a provider snapshot, or `research fetch-metadata --provider crossref|openalex`, to verify title/DOI/year/authors and acquire outgoing citation relations. Review reported conflicts; do not overwrite contradictory metadata silently.

Treat a provisionally skipped concept as a disclosed assumption, returned under `provisional_knowledge_atom_ids`, rather than a proven competency. Deferred and otherwise unsatisfied concepts remain `knowledge_gap_atom_ids`. If paper comprehension exposes a skipped-concept gap, backtrack without losing the Active Paper.

If a required concept has a detailed expansion, complete its ordered children and parent integration check before treating the paper's Knowledge Atom dependency as mastered. Keep the Active Paper state separate from the one Active learning Atom.

Do not use citation count as evidence quality. Mark why each paper is in scope and what role it plays.

## Read one paper

Use three passes:

1. Triage: inspect title, abstract, figures, conclusion, venue, and metadata to confirm relevance.
2. Structure: identify the problem, assumptions, contribution, approach, evaluation, and claimed boundary.
3. Evidence: inspect experimental design or argument, baselines, data, metrics, uncertainty, threats, and whether the conclusion exceeds the evidence.

For each central claim, store a concise statement, evidence summary, and strength: `weak`, `mixed`, `moderate`, `strong`, or `unclear`. Record limitations even when the paper does not state them explicitly, but label inference as analysis rather than author-reported fact.

Relate a completed paper to imported papers with `supports`, `extends`, `contradicts`, `replicates`, or `compares`. Keep the relation note specific about the claim, setting, or evidence difference.

Do not store complete paper text in the workspace. Store metadata, stable locators, and concise analytical notes.

## Synthesize across papers

Run synthesis after a coherent group is critically complete. The runtime forms source-preserving claim themes and compares papers on:

- question and assumptions;
- method or argument;
- data and evaluation;
- central claim and evidence strength;
- limitations and external validity;
- support, extension, contradiction, or replication relations.

Treat `LITERATURE_MATRIX.md` as a comparison surface, not a ranking. Each synthesized theme retains its claim IDs, evidence summaries, strengths, paper relations, and limitations; single-source and contested themes remain explicit. Use `RESEARCH_GAPS.md` to inspect open questions, repeated limitations, contradictions, and missing replications.

## Identify research gaps

A gap is a candidate hypothesis until checked against current literature. Give it stronger status only when:

- it follows from multiple evidence-linked observations;
- nearby work and terminology have been searched;
- the gap is not merely an unstated detail in one paper;
- the proposed contribution is distinguishable from a different dataset, metric, or implementation;
- contradictory and negative evidence are included.

State search limits, uncertainty, and the date of current-literature verification.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py research init <workspace> --field <field> --question <question> --scope <scope>
python <SKILL_DIR>/scripts/atomlearn.py research import <workspace> --input <research-plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research reconcile-metadata <workspace> --input <research-metadata.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research fetch-metadata <workspace> --provider crossref
python <SKILL_DIR>/scripts/atomlearn.py research status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research next <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research activate <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research note <workspace> <paper-id> --input <note.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research complete <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research synthesize <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research validate <workspace>
```

Pass `--expected-research-revision` on every research mutation. Use `park` when a paper is relevant but not timely. Use `exclude --reason` when it fails declared scope criteria.
