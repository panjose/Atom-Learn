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

Persist the complete protocol with `research set-protocol` before discovery. Its revision is independent from the paper graph revision and records languages, dates, literature types, target outcomes, and search limits in addition to the question and inclusion/exclusion criteria.

## Build the paper map

Prefer an initial map of 8-20 representative papers, then expand deliberately. Cover roles rather than selecting only highly cited or recent work:

1. surveys for vocabulary, branches, and debates;
2. seminal papers for original problem framing;
3. theory and representative method families;
4. benchmarks and datasets that define the evidence surface;
5. critiques and replications that test accepted claims;
6. applications and recent challengers after foundations are clear.

Use `prerequisite_paper_ids` for the reading order. Use `cites` for internal citation links. Put references outside the imported set in `external_citations`. Link `concept_atom_ids` when a paper assumes knowledge that the learner may need to repair. When lineage is initialized, trace those Atoms to expose the smallest prerequisite and conceptual repair route while preserving the Active Paper.

Import normalizes DOI forms and merges exact DOI/title duplicates before validating the paper graph. `research discover` can query Crossref/OpenAlex directly or emit a typed harness Web Search action. Submit action results through `research submit-discovery`; Core records the exact query, filter, provider, result IDs, protocol revision, and failure status. Run `research snowball` for bounded backward/forward citation expansion and `research refresh` for saved-query, metadata, correction, and retraction checks. Review reported conflicts; do not overwrite contradictory metadata silently.

Discovered records remain candidates. Use `research screen` against predeclared criteria. An unconfirmed model include/exclude suggestion becomes `needs_review`; confirmed exclusion requires one exact protocol criterion and a reason. PRISMA-style counts are audit totals for the bounded provider results, never an exhaustive-review claim. A retraction or integrity concern blocks activation until screening is explicitly reconsidered.

Treat a provisionally skipped concept as a disclosed assumption, returned under `provisional_knowledge_atom_ids`, rather than a proven competency. Deferred and otherwise unsatisfied concepts remain `knowledge_gap_atom_ids`. If paper comprehension exposes a skipped-concept gap, backtrack without losing the Active Paper.

When a full paper or user knowledge-base item has already been indexed by RAG, bind the imported paper to the active shared [Document IR](DOCUMENT_IR.md) with `research attach-source`. Research state records only the source revision, content hash, block count, and locator; it does not copy the paper body. Reingest a legacy source first if it predates Document IR.

If a required concept has a detailed expansion, complete its ordered children and parent integration check before treating the paper's Knowledge Atom dependency as mastered. Keep the Active Paper state separate from the one Active learning Atom.

Do not use citation count as evidence quality. Mark why each paper is in scope and what role it plays.

## Read one paper

Use three passes:

1. Triage: inspect title, abstract, figures, conclusion, venue, and metadata to confirm relevance.
2. Structure: identify the problem, assumptions, contribution, approach, evaluation, and claimed boundary.
3. Evidence: inspect experimental design or argument, baselines, data, metrics, uncertainty, threats, and whether the conclusion exceeds the evidence.

For each central claim, store a concise statement, evidence summary, and strength: `weak`, `mixed`, `moderate`, `strong`, or `unclear`. Also store population, setting, dataset, method, baseline, outcome, metric, assumption, effect/uncertainty, and a sentence/table/figure/equation/block evidence locator. Document IR block locators are checked against the attached source revision and block IDs. A paper cannot complete without claim-level locators. Record limitations even when the paper does not state them explicitly, but label inference as analysis rather than author-reported fact.

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

Treat `LITERATURE_MATRIX.md` as a comparison surface, not a ranking. Theme proposals require either compatible structured outcome/metric plus context facets or an explicit paper relation; token overlap alone cannot merge claims. Each theme retains claim locators and conditional population/dataset/metric/assumption differences. Themes remain `proposed` until `research review-synthesis` confirms, relabels, or rejects them. Single-source and contested themes remain explicit. Use `RESEARCH_GAPS.md` to inspect open questions, repeated limitations, contradictions, and missing replications.

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
python <SKILL_DIR>/scripts/atomlearn.py research set-protocol <workspace> --input <research-protocol.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research discover <workspace> --provider harness|crossref|openalex --query <query>
python <SKILL_DIR>/scripts/atomlearn.py research submit-discovery <workspace> --input <research-discovery-submission.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research screen <workspace> --input <research-screening.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research snowball <workspace> <paper-id> --direction backward|forward --stopping-rule <rule>
python <SKILL_DIR>/scripts/atomlearn.py research refresh <workspace> --provider harness|crossref|openalex
python <SKILL_DIR>/scripts/atomlearn.py research import <workspace> --input <research-plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research reconcile-metadata <workspace> --input <research-metadata.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research fetch-metadata <workspace> --provider crossref
python <SKILL_DIR>/scripts/atomlearn.py research attach-source <workspace> <paper-id> --source-id <source-id>
python <SKILL_DIR>/scripts/atomlearn.py research status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research next <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research activate <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research note <workspace> <paper-id> --input <note.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research complete <workspace> <paper-id>
python <SKILL_DIR>/scripts/atomlearn.py research synthesize <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research review-synthesis <workspace> --input <research-synthesis-review.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research validate <workspace>
```

Pass `--expected-research-revision` on every research mutation. Use `park` when a paper is relevant but not timely. Use `exclude --reason <reason> --criterion <predeclared-criterion>` when it fails declared scope criteria.
