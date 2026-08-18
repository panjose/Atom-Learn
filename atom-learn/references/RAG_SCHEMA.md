# RAG payload and state schema

## Contents

- Runtime files
- Local ingestion
- Web evidence ingestion
- Search
- Corrective Web Search orchestration
- Provider embeddings
- Local learned embeddings and HNSW
- Cross-encoder gate
- Coverage
- Evaluation
- Commands

## Runtime files

Canonical runtime data lives in `.atomlearn/rag/`:

- `state.yaml`: schema, RAG revision, and chunking configuration;
- `sources.yaml`: source metadata and immutable revision history;
- `document-ir/<source-id>.r<revision>.json`: immutable structured blocks for each newly ingested source revision;
- `index.sqlite3`: active and historical chunks plus the FTS5 index;
- `events.ndjson`: append-only mutation audit;
- `query-events.ndjson`: query ID, time, RAG revision, and returned chunk IDs;
- `latest-coverage.yaml`: latest explicit coverage decision.
- `vector-index/<kind>/gNNNNNN/`: immutable verified HNSW generations;
- `vector-index/active.yaml`: active generation pointers;
- `benchmarks/`: local named-gate and reranker reports.

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

HTML headings, lists, and tables, DOCX headings and tables, and PDF pages, formulas, and detected tables become typed Document IR blocks with distinct locators. Tables have child cells, OCR pages use `ocr_text`, and every retrieval chunk stores its owning `document_ir_block_ids`. PDF OCR first reads a form-feed-separated `.pdf.ocr.txt` or `.ocr.txt` sidecar, then tries the optional PyMuPDF/Tesseract integration. `ocr: required` fails when any image-only page remains unrecovered. See [DOCUMENT_IR.md](DOCUMENT_IR.md) and its public JSON Schema.

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
parent_context_chars: 4000 # 0-20000
use_cross_encoder: true
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

The result includes `search_id`, query variants, retrieval metadata, candidate text, provenance, `document_ir_block_ids`, bounded parent context, RRF score, component ranks, deterministic and optional cross-encoder scores, and the harness evidence contract. Parent context never replaces the supporting child locator. Every chunk has a default `atomlearn/multilingual-hash-v1` local vector, so BM25, default-dense, and deterministic reranking work without an API key. Neither the RRF nor reranker score is a confidence probability. Large corpora use a verified HNSW generation or skip dense retrieval with zero scanned chunks.

## Corrective Web Search orchestration

```yaml
coverage:
  context: intake
  intake_revision: 2
  goal_contract_revision: 0
  requirements: []
  verdicts: []
web_evidence: # optional on a correction round
  sources: []
```

`rag correct` runs the coverage gate and returns `web_search_tasks` for weak, missing, or unverified requirements only when the current intake Corpus Policy permits expansion. The harness executes those tasks with native Web Search, opens authoritative pages, and reruns the command with bounded `web_evidence` and verdicts. Evidence is ingested before candidates are refreshed. A task disappears only after the matching requirement passes. `closed_corpus` returns `corpus_gap_reported`, has no Web tasks, and rejects Web evidence ingestion.

## Optional provider embeddings

```yaml
model: approved-provider/model@version
model_revision: immutable-provider-revision
license: provider-model-license
replace_profile: false
confirmed: false
embeddings:
  - chunk_id: source-id.r1.c00001
    vector: [0.1, -0.2]
```

`model` is mandatory. All vectors in one batch must have the same dimension, chunk IDs must be unique and active, and a profile replacement must cover every active chunk atomically with explicit confirmation. The first batch establishes the workspace embedding profile; later batches and query embeddings must match its model identifier and dimension. Attach embeddings only after ingestion.

## Local learned embeddings and HNSW

```yaml
model:
  model_id: organization/multilingual-embedding
  revision: immutable-revision
  license: apache-2.0
  path: C:/absolute/path/to/local-model
  backend: torch # torch, onnx, or openvino
  batch_size: 16
replace_profile: false
confirmed: false
```

The public input is validated by `assets/schemas/semantic-model.schema.json`. Local learned models must use safe weights and installed Sentence Transformers modules; remote code, network loading, symlinks, and pickle-capable weights are rejected. `rag embed-local` embeds every active chunk in one transaction. `rag index-build --kind default|semantic|all` creates and verifies a new generation; `--full` disables incremental reuse. See [SEMANTIC_RAG.md](SEMANTIC_RAG.md).

## Cross-encoder gate

`rag evaluate-reranker` accepts `profile` plus the same `model` shape, and writes a portable report following `assets/schemas/reranker-evaluation.schema.json`. `rag activate-reranker` accepts:

```yaml
report_path: C:/absolute/path/to/passing-report.json
confirmed: true
```

Activation requires a passing current bundled profile, internally consistent metrics, and an unchanged local model tree. Search uses the active cross-encoder unless `use_cross_encoder: false` is set.

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
profile: core-release-v2 # optional named thresholds; mutually exclusive with thresholds
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

`rag evaluate` reports mean recall@k, MRR, nDCG@k, citation correctness, and unsupported-claim rate. Retrieval labels and support labels are active chunk IDs. With no profile or thresholds the result is `quality_gate: report_only`. To request an ad hoc deterministic `pass`/`fail` gate, provide all five core threshold values from 0 through 1; partial sets are rejected. Named profiles additionally bind source-diversity, freshness, correction-success, and residual-gap cases and thresholds.

Stable release gates use `core-release-v2`. Its schema-v2 contract fixes a read-only held-out split; dataset, parser, embedding, reranker, runtime, seed, resample count, and claim boundary; seven named evaluation profiles; at least 18 queries and 12 sources; all core thresholds plus grounding-detection accuracy; and real structured fixtures. The runner reports bootstrap intervals, per-profile gates, parser block-kind regression, and retrieval/reranking/locator/generation-grounding failure stages. It rejects tiny, empty-threshold, cross-language-leaking, hard-negative-incomplete, or structured-format-incomplete profiles before ingestion. `rag benchmark` runs only in a fresh RAG workspace and persists the report. The report cannot be cited as a learning-effect result.

## Commands

```text
python <SKILL_DIR>/scripts/atomlearn.py rag init <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest <workspace> --input <sources.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag ingest-web <workspace> --input <web-evidence.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag attach-embeddings <workspace> --input <embeddings.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag embed-local <workspace> --input <local-embedding.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag index-build <workspace> --kind default|semantic|all
python <SKILL_DIR>/scripts/atomlearn.py rag index-status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag search <workspace> --input <query.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag requirements <workspace> [--context intake|research]
python <SKILL_DIR>/scripts/atomlearn.py rag coverage <workspace> --input <coverage.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag correct <workspace> --input <rag-correction.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag evaluate <workspace> --input <rag-evaluation.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag benchmark <workspace> --profile core-release-v2
python <SKILL_DIR>/scripts/atomlearn.py rag evaluate-reranker <workspace> --input <reranker.yaml> --output <report.json>
python <SKILL_DIR>/scripts/atomlearn.py rag activate-reranker <workspace> --input <activation.yaml>
python <SKILL_DIR>/scripts/atomlearn.py rag document-ir <workspace> <source-id> [--revision <revision>]
python <SKILL_DIR>/scripts/atomlearn.py rag status <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag validate <workspace>
python <SKILL_DIR>/scripts/atomlearn.py rag render <workspace>
```
