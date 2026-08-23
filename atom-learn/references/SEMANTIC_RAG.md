# Learned semantic and scale RAG

AtomLearn keeps the deterministic BM25 plus multilingual hash path enabled for every workspace. Learned embeddings, HNSW, and cross-encoder reranking are independent opt-ins. In `v0.15.0`, these local learned/scale paths are developer/source capabilities installed from optional extras; they are not present in the signed stable `base` runtime. None of them downloads a model, contacts a hosted provider, or weakens the requirement-level evidence gate.

## Optional dependencies

Use these commands only in an editable developer/source installation:

```powershell
python -m pip install -e ".[scale]"
python -m pip install -e ".[semantic]"
```

`scale` installs USearch's persisted HNSW implementation. AtomLearn runs a bounded native health probe in a child process before importing the backend; an incompatible wheel therefore produces a typed error instead of terminating the learning process. `semantic` installs Sentence Transformers for explicitly supplied local embedding and cross-encoder models.

USearch was selected because its maintained Python SDK exposes HNSW construction, addition, removal, save/load, and current CPython wheels. Its official [Python SDK](https://unum-cloud.github.io/USearch/python/index.html) documents the HNSW controls and serialization contract. The learned adapter follows Sentence Transformers' distinct query/document encoding interface and local-only model loading described in the official [semantic-search usage guide](https://www.sbert.net/docs/sentence_transformer/usage/usage.html).

## Local model approval

A local model payload follows `assets/schemas/semantic-model.schema.json`:

```yaml
model:
  model_id: organization/multilingual-embedding
  revision: immutable-model-revision
  license: apache-2.0
  path: C:/absolute/path/to/approved-model
  backend: torch
  batch_size: 16
replace_profile: false
confirmed: false
```

The path must be absolute and cannot be a symlink. AtomLearn recursively hashes the directory, records its byte size, rejects custom Sentence Transformers modules, disables remote code and network access, and rejects pickle-capable `.bin`, `.pt`, `.pth`, `.pkl`, and `.pickle` weights. Safetensors, ONNX, and OpenVINO models are accepted; an OpenVINO `.bin` is accepted only beside its same-name `.xml` graph. The recorded path, content hash, and byte size are rechecked before every load.

Changing model identity, revision, content hash, backend, or declared license requires both `replace_profile: true` and `confirmed: true`. Model generation is all-or-nothing across the current active corpus. Provider-supplied vectors remain supported through `rag attach-embeddings`, with the same explicit replacement and active-chunk rules.

```text
atomlearn rag embed-local <workspace> --input <local-embedding.yaml> --expected-rag-revision <n>
```

## Scale boundary and HNSW generations

`rag init --dense-bruteforce-limit` controls the small-corpus boundary. At or below the boundary, dense scoring reads only the relevant vector column and source filter. Above it, AtomLearn either queries a verified HNSW generation or skips that dense component with `scanned_chunks: 0` and an actionable `index-build` message. It never silently performs a large Python full scan.

```text
atomlearn rag index-status <workspace>
atomlearn rag index-build <workspace> --kind default --expected-rag-revision <n>
atomlearn rag index-build <workspace> --kind semantic --expected-rag-revision <n>
atomlearn rag index-build <workspace> --kind all --full --expected-rag-revision <n>
```

Each build writes a new `vector-index/<kind>/gNNNNNN/` directory. It saves the index, records its SHA-256, reloads it, runs self-retrieval checks, then atomically moves `active.yaml` to the verified generation. A failed build never replaces the prior pointer. Source revisions and vector changes use incremental remove/add until accumulated tombstones exceed the configured rebuild ratio; the next generation then performs a full deterministic rebuild. Earlier generations are retained as recoverable artifacts.

SQLite remains authoritative for text, provenance, source revisions, active state, and chunk-to-label mappings. Index metadata binds the embedding profile, corpus epoch, vector hashes, source revisions, and index hash. Any disagreement is reported as `stale` or `corrupt`.

## Parent-child retrieval

Search results identify the exact supporting chunk and its Document IR block IDs. `parent_context_chars` independently controls bounded expansion to the owning heading and sibling blocks. The broader context is for interpretation only: its `evidence_citation_rule` requires claims to cite the supporting child locator, not the parent context by itself. Set the value to `0` to disable expansion.

## Named retrieval gate

The bundled `core-release-v2` profile is validated by `assets/schemas/rag-benchmark-profile.schema.json`. It is an explicit read-only held-out release set with fixed dataset, parser, embedding, reranker, base-runtime, and bootstrap identities. Seven named/versioned profiles cover an honest lexical baseline, true cross-lingual retrieval without bilingual relevant blocks, domain shift, four hard-negative trap types, production structured-document parsing, OCR/layout, and adversarial grounding. Its non-empty thresholds gate recall@k, MRR, nDCG, citation correctness, unsupported-claim rate, grounding detection, source diversity, freshness, correction success, and residual gaps.

Run it only in a fresh dedicated RAG workspace because the command ingests its immutable fixtures:

```text
atomlearn rag benchmark <benchmark-workspace> --profile core-release-v2 --expected-rag-revision 0
```

The report records percentile-bootstrap uncertainty, per-profile gates, real HTML/DOCX/PDF/OCR parser results, and retrieval/reranking/locator/generation-grounding failure stages. Ad hoc `rag evaluate` without thresholds remains `report_only`. A named `profile` and explicit `thresholds` are mutually exclusive, so a stable gate cannot be converted into a pass by supplying permissive values. The default hash projection remains labeled a non-learned baseline. A candidate learned profile additionally needs its actual distributed runtime and this unchanged held-out set before any stable delivery claim; neither result is a learning-effect claim.

## Cross-encoder evaluation and activation

Cross-encoders use the same local-model safety policy and Sentence Transformers' official [CrossEncoder interface](https://www.sbert.net/docs/package_reference/cross_encoder/model.html). Evaluate the model against the already-ingested bundled benchmark workspace:

```yaml
profile: core-release-v2
model:
  model_id: organization/multilingual-reranker
  revision: immutable-model-revision
  license: apache-2.0
  path: C:/absolute/path/to/approved-reranker
```

```text
atomlearn rag evaluate-reranker <benchmark-workspace> --input <reranker.yaml> --output <absolute-report.json>
```

The portable report compares the candidate MRR and nDCG against both absolute thresholds and maximum allowed regression from the deterministic baseline. Activation revalidates the report schema, current bundled profile hash, metrics, threshold decisions, model tree, and model hash:

```yaml
report_path: C:/absolute/path/to/reranker-report.json
confirmed: true
```

```text
atomlearn rag activate-reranker <workspace> --input <activation.yaml> --expected-rag-revision <n>
```

An active cross-encoder reranks only the bounded fused candidate set, records its raw score alongside the deterministic score and provenance, and can be bypassed for diagnosis with `use_cross_encoder: false`. A model-load or integrity failure is explicit; AtomLearn does not silently claim that deterministic results came from the approved cross-encoder.

## Trust boundary

Retrieval and reranking produce candidates, not truth or mastery. The harness still treats source passages as untrusted data, decides whether a passage directly supports a requirement, and may cite only candidates returned for that requirement. Weak or missing support continues through the structured corrective Web Search loop. Private source text, vectors, models, queries, reports, and generations stay in the chosen local workspace unless the user explicitly moves them.
