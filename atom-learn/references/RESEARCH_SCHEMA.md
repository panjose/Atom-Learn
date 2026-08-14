# Research state schema

## Contents

- Runtime layout
- Research import plan
- Paper roles and statuses
- Critical note
- Relations and identifiers

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

`priority` ranges from 1 (highest) to 5. Internal `cites` and prerequisite IDs must exist in the imported paper set. Use `external_citations` for stable references that are not yet imported.

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

Claim strength is `weak`, `mixed`, `moderate`, `strong`, or `unclear`. A paper cannot become `read` without a problem, at least one contribution, an approach, at least one evidence-linked claim, at least one limitation, and field positioning.

## Relations and identifiers

Use lowercase dot-separated stable IDs such as `paper.author.keyword.2025`. Claim IDs are generated as `<paper-id>.claim.<number>` when omitted.

Relations are `supports`, `extends`, `contradicts`, `replicates`, and `compares`. Relation targets must be imported papers and cannot point to the same paper. Paper prerequisites must remain acyclic.
