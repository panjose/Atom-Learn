# RAG payload and state schema

## Contents

- Runtime files
- Local ingestion
- Web evidence ingestion
- Search
- Corrective Web Search orchestration
- Provider embeddings
- Coverage
- Evaluation
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
    ocr: auto # auto, required, or off; PDF paths only
    ocr_language: eng # Tesseract language code
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

HTML headings, lists, and tables, DOCX headings and tables, and PDF pages, formulas, and detected tables receive distinct locators. PDF OCR first reads a form-feed-separated `.pdf.ocr.txt` or `.ocr.txt` sidecar, then tries the optional PyMuPDF/Tesseract integration. `ocr: required` fails when any image-only page remains unrecovered.

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

The result includes `search_id`, query variants, retrieval metadata, candidate text, provenance, RRF score, component ranks, deterministic reranker score and components, and the harness evidence contract. Every chunk has a default `atomlearn/multilingual-hash-v1` local embedding, so BM25, default-dense, and deterministic reranking work without an API key. Neither the RRF nor reranker score is a confidence probability.

## Corrective Web Search orchestration

```yaml
coverage:
  context: intake
  intake_revision: 2
  requirements: []
  verdicts: []
web_evidence: # optional on a correction round
  sources: []
```

`rag correct` runs the coverage gate and returns `web_search_tasks` for weak, missing, or unverified requirements. The harness executes those tasks with native Web Search, opens authoritative pages, and reruns the command with bounded `web_evidence` and verdicts. Evidence is ingested before candidates are refreshed. A task disappears only after the matching requirement passes.

## Optional provider embeddings

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

Every `evidence_chunk_id` must also belong to the freshly retrieved candidate set for that exact requirement. Being active elsewhere in the corpus is insufficient.

`context` is `intake`, `research`, or `custom`. Canonical state uses `intake_revision` or `research_revision`, which must match the current selected context. Coverage must contain every generated anchor and cannot weaken its minimum-source or authority rule. `custom` is accepted only when neither intake nor research state exists. The gate passes only when every requirement is explicitly `supported`.

## Evaluation

```yaml
k: 10
queries:
  - id: calculus-chain-rule
    query: chain rule for composite functions
    relevant_chunk_ids: [calculus.r1.c00007]
claims:
  - id: answer-claim-1
    cited_chunk_ids: [calculus.r1.c00007]
    supported_chunk_ids: [calculus.r1.c00007]
    abstained: false
thresholds:
  recall_at_k: 0.9
  mrr: 0.8
  ndcg_at_k: 0.8
  citation_correctness: 0.95
  unsupported_claim_rate: 0.05
```

`rag evaluate` reports mean recall@k, MRR, nDCG@k, citation correctness, and unsupported-claim rate. Retrieval labels and support labels are active chunk IDs. With no thresholds the result is `quality_gate: report_only`. To request a deterministic `pass`/`fail` gate, provide all five threshold values from 0 through 1; partial sets are rejected.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py rag init <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest <workspace> --input <sources.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest-web <workspace> --input <web-evidence.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag attach-embeddings <workspace> --input <embeddings.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag search <workspace> --input <query.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag requirements <workspace> [--context intake|research]
python <SKILL_DIR>/scripts/atomlearn.py rag coverage <workspace> --input <coverage.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag correct <workspace> --input <rag-correction.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag evaluate <workspace> --input <rag-evaluation.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag render <workspace>
```
