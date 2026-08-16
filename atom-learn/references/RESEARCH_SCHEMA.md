# Research state schema

## Contents

- Runtime layout
- Research import plan
- Paper roles and statuses
- Critical note
- Relations and identifiers
- Metadata reconciliation and citation acquisition
- Evidence synthesis

## Runtime layout

```text
<workspace>/
├── RESEARCH_MAP.md
├── CURRENT_PAPER.md
├── LITERATURE_MATRIX.md
├── RESEARCH_GAPS.md
└── .atomlearn/research/
    ├── state.yaml
    ├── events.ndjson
    └── papers/
        └── <paper-id>.yaml
```

The YAML and NDJSON files are canonical. The four root Markdown files are generated projections. Research revision is independent from course and evolution revisions.

`state.yaml` also stores a separate `protocol_revision`, the structured discovery protocol, append-only discovery/screening logs, and the latest refresh receipt. A discovery action records provider, query, filters, seed/direction/depth, stopping rule, result IDs, completion, and failure status.

## Research import plan

```yaml
research:
  field: Reliable autonomous research agents
  research_question: Which design choices make tool use reliable and auditable?
  scope: Architectures, evaluations, and replication evidence.
  inclusion_criteria: [Reports inspectable evidence]
  exclusion_criteria: [Product announcement only]
papers:
  - id: paper.field.survey
    title: A survey of the field
    authors: [A. Researcher]
    year: 2025
    venue: Example Venue
    doi: ""
    url: https://example.org/paper
    locator: local-or-stable-source-locator
    role: survey
    priority: 1
    status: queued
    prerequisite_paper_ids: []
    cites: []
    external_citations: []
    concept_atom_ids: []
    tags: [survey, reliability]
```

`priority` ranges from 1 (highest) to 5. Internal `cites` and prerequisite IDs must exist in the imported paper set. Use `external_citations` for stable references that are not yet imported. DOI values are canonicalized to lowercase bare identifiers. Import automatically merges exact DOI or normalized-title duplicates, records `paper_aliases`, and rewrites their internal citation/prerequisite references to the canonical paper.

## Paper roles and statuses

Roles are `survey`, `seminal`, `theory`, `method`, `benchmark`, `dataset`, `application`, `critique`, and `replication`.

Statuses are:

- `discovered`: recorded but not yet committed to the reading queue;
- `queued`: eligible when paper prerequisites are complete;
- `active`: the single paper currently under critical reading;
- `read`: critically complete but not yet integrated by synthesis;
- `synthesized`: represented in cross-paper synthesis;
- `parked`: intentionally deferred;
- `excluded`: outside declared scope, with a reason.

## Critical note

```yaml
problem: What limitation or question does the paper address?
contributions:
  - The specific contribution, not the paper section list.
approach: Method, theoretical argument, or study design.
datasets: [Dataset or evidence source]
claims:
  - id: paper.field.survey.claim.001
    statement: A bounded statement of the central claim.
    evidence_summary: Evidence that directly bears on the claim.
    strength: moderate
    effect: The bounded observed effect.
    uncertainty: Confidence interval, variance, or stated uncertainty boundary.
    facets:
      population: [Target population]
      setting: [Evaluation setting]
      dataset: [Evidence source]
      method: [Method family]
      baseline: [Comparator]
      outcome: [Outcome]
      metric: [Metric]
      assumption: [Condition]
    evidence_locator:
      locator: Results, table 2, row 4
      kind: table
      extraction_method: document_ir
      confidence: 0.95
      source_id: paper-source
      source_revision: 1
      block_ids: [block-0123456789abcdef01234567]
limitations:
  - A boundary, threat, missing comparison, or external-validity limit.
open_questions:
  - A testable unresolved question.
field_positioning: How this paper differs from or changes nearby work.
relations:
  - paper_id: paper.method.alpha
    type: contradicts
    note: The result changes under a broader evaluation distribution.
```

Claim strength is `weak`, `mixed`, `moderate`, `strong`, or `unclear`. A paper cannot become `read` without a problem, at least one contribution, an approach, at least one evidence-linked claim with a sentence/table/figure/equation/block locator, at least one limitation, and field positioning. Document IR block IDs and source revision are verified when present.

## Discovery and screening

Set a protocol with `research set-protocol`, then use `research discover`. Harness discovery returns a typed action whose result must match `research-discovery-submission.schema.json`; Crossref/OpenAlex discovery passes through the same submission/import path. Candidates begin with screening status `candidate`. Only confirmed inclusion makes them readable. A model-only include/exclude decision becomes `needs_review`, and confirmed exclusion must cite a predeclared criterion.

`research snowball` creates backward/forward actions with seed, depth, and stopping rule. `research refresh` checks saved queries and included-paper integrity metadata. The provider result set remains bounded; PRISMA-style counts do not assert exhaustive retrieval.

## Relations and identifiers

Use lowercase dot-separated stable IDs such as `paper.author.keyword.2025`. Claim IDs are generated as `<paper-id>.claim.<number>` when omitted.

Relations are `supports`, `extends`, `contradicts`, `replicates`, and `compares`. Relation targets must be imported papers and cannot point to the same paper. Paper prerequisites must remain acyclic.

## Metadata reconciliation and citation acquisition

Use `research reconcile-metadata` with `assets/templates/research-metadata.yaml` for provider snapshots returned by a connector or harness. A record matches by paper ID, DOI, or normalized title. The runtime compares title, DOI, year, and author overlap; fills only missing metadata when all checks pass; preserves conflicts for review; resolves references to internal `cites`; and retains unresolved DOI/provider identifiers under `external_citations`.

For direct provider acquisition, `research fetch-metadata --provider crossref|openalex` fetches every paper that has a DOI and passes the normalized result through the same reconciliation path. Crossref DOI references and OpenAlex referenced-work IDs become citation relations or auditable external references. Per-paper provider failures are returned without hiding successful records.

Each paper stores:

```yaml
metadata_verification:
  status: verified # unverified, verified, or conflict
  provider: crossref
  provider_id: 10.1234/example
  retrieved_at: 2026-08-14T10:00:00+08:00
  checks:
    title: true
    doi: true
    year: true
    authors: true
```

## Evidence synthesis

`research synthesize` groups claims only from structured facet compatibility or explicit paper relations, preserves every claim locator and conditional context, and records a bounded `latest_synthesis`. Every theme exposes merge evidence, source paper and claim IDs, evidence strengths, relation types, limitations, an `assessment`, and an evidence grade. A one-paper cluster is `single_source`; a contradiction is `contested`, never averaged away. Themes start as `proposed`; `research review-synthesis` is required to confirm, relabel, or reject them.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py research import <workspace> --input <research-plan.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research set-protocol <workspace> --input <research-protocol.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research discover <workspace> --provider harness|crossref|openalex --query <query>
python <SKILL_DIR>/scripts/atomlearn.py research submit-discovery <workspace> --input <research-discovery-submission.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research screen <workspace> --input <research-screening.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research snowball <workspace> <paper-id> --direction backward|forward --stopping-rule <rule>
python <SKILL_DIR>/scripts/atomlearn.py research refresh <workspace> --provider harness|crossref|openalex
python <SKILL_DIR>/scripts/atomlearn.py research reconcile-metadata <workspace> --input <research-metadata.yaml>
python <SKILL_DIR>/scripts/atomlearn.py research fetch-metadata <workspace> --provider crossref
python <SKILL_DIR>/scripts/atomlearn.py research synthesize <workspace>
python <SKILL_DIR>/scripts/atomlearn.py research review-synthesis <workspace> --input <research-synthesis-review.yaml>
```
