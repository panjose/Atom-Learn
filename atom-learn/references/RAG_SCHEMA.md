# RAG payload and state schema

## Contents

- Runtime files
- Local ingestion
- Web evidence ingestion
- Search
- Provider embeddings
- Coverage
- Commands

## Runtime files

Canonical runtime data lives in `.atomlearn/rag/`:

- `state.yaml`: schema, RAG revision, and chunking configuration;
- `sources.yaml`: source metadata and immutable revision history;
- `index.sqlite3`: active and historical chunks plus the FTS5 index;
- `events.ndjson`: append-only mutation audit;
- `query-events.ndjson`: query ID, time, RAG revision, and returned chunk IDs;
- `latest-coverage.yaml`: latest explicit coverage decision.

`RETRIEVAL.md` is generated. Do not edit it to mutate state.

RAG revision is independent from course and intake revisions. Mutating commands accept `--expected-rag-revision` for optimistic concurrency protection.

## Local ingestion

```yaml
sources:
  - id: stable-source-id
    title: Human-readable title
    authority: textbook
    version: optional edition or version
    path: C:/absolute/or/relative/source.pdf
```

A local source must provide exactly one usable content mechanism:

- `path`: TXT, Markdown, RST, HTML, JSON, YAML, CSV, PDF, or DOCX;
- `text`: inline Markdown-like text;
- `passages`: structured bounded passages.

Structured passages use:

```yaml
passages:
  - locator: chapter 2, theorem 4
    section: Convergence
    text: Bounded source content.
```

`authority` is one of `primary`, `official`, `peer_reviewed`, `textbook`, `user`, `secondary`, or `unknown`.

## Web evidence ingestion

```yaml
sources:
  - id: stable-web-source-id
    title: Page or paper title
    url: https://example.org/page
    retrieved_at: 2026-08-14T10:00:00+08:00
    query: focused search query that found this evidence
    authority: official
    version: optional page, standard, or software version
    passages:
      - locator: section 3
        section: Exact section title
        text: Bounded evidence passage or faithful note.
```

The URL must use HTTP(S), contain a host, and contain no userinfo. `retrieved_at` must be timezone-aware and not in the future. `query` and at least one passage are mandatory.

## Search

```yaml
query: Main question
alternate_queries: [alias, translated terminology]
top_k: 8
candidate_k: 50
source_ids: [optional-source-filter]
query_embedding: [0.1, -0.2] # optional
embedding_model: approved-provider/model@version # required with query_embedding
```

Constraints:

- query: 1-2000 characters;
- alternate queries: at most 10;
- `top_k`: 1-50;
- `candidate_k`: `top_k` through 200;
- source filter: at most 100 IDs;
- embedding: 1-8192 finite, nonzero numeric values.

The result includes `search_id`, query variants, retrieval metadata, candidate text, provenance, RRF score, component ranks, and a reranking contract. The RRF score is not a confidence probability.

## Provider embeddings

```yaml
model: approved-provider/model@version
embeddings:
  - chunk_id: source-id.r1.c00001
    vector: [0.1, -0.2]
```

`model` is mandatory. All vectors in one batch must have the same dimension and chunk IDs must be active. The first batch establishes the workspace embedding profile; later batches and query embeddings must match its model identifier and dimension. Attach embeddings only after ingestion.

## Coverage

```yaml
context: intake
intake_revision: 2
requirements:
  - id: outline.optimization
    query: constrained optimization and Lagrange multipliers
    alternate_queries: [equality constrained optimization]
    minimum_sources: 1
    authoritative: false
verdicts:
  - requirement_id: outline.optimization
    status: supported
    evidence_chunk_ids: [math-text.r1.c00012]
    rationale: The selected section directly covers the requested method.
```

Verdict status is `supported`, `weak`, or `missing`. `supported` requires at least one active evidence chunk, the requested number of distinct sources, and an authoritative evidence source when `authoritative: true`.

`context` is `intake`, `research`, or `custom`. Canonical state uses `intake_revision` or `research_revision`, which must match the current selected context. Coverage must contain every generated anchor and cannot weaken its minimum-source or authority rule. `custom` is accepted only when neither intake nor research state exists. The gate passes only when every requirement is explicitly `supported`.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py rag init <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest <workspace> --input <sources.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest-web <workspace> --input <web-evidence.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag attach-embeddings <workspace> --input <embeddings.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag search <workspace> --input <query.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag requirements <workspace> [--context intake|research]
python <SKILL_DIR>/scripts/atomlearn.py rag coverage <workspace> --input <coverage.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag render <workspace>
```
