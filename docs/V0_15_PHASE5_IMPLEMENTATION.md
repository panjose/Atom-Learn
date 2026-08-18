# v0.15 Phase 5 implementation record

## Scope

Phase 5 implements Workstream E from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: a low-burden topic entry diagnostic and an expanded, held-out RAG release suite. It does not widen mastery or learning-effect claims.

## Low-burden topic entry

The shortest topic request now resolves only three decisions that can materially change the path: starting point, target depth, and use case. The typed `diagnose_topic` action first offers `start_from_basics`, `map_first`, and `use_defaults`. Adaptive mode is bounded to 2–5 items and must cover every unresolved decision.

`topic-diagnostic.schema.json` and Core runtime checks enforce the privacy and inference boundary:

- `dont_know` and `skipped` must remain `unknown` and cannot support a diagnostic-signal recommendation;
- test-out requires an answered secure starting-point signal;
- persisted state contains prompt hashes and bounded signals, never raw responses;
- the result guides the plan entry boundary but cannot create mastery Evidence before an Active Atom and qualified scorer exist.

The minimized record is inspectable at `.atomlearn/topic-diagnostic.yaml`. Explicit depth or use-case choices update intake; defaults remain disclosed defaults. The plan task receives the minimized summary and the non-mastery boundary.

## Held-out RAG release suite

`core-release-v2` replaces the legacy aggregate profile as the default stable gate. Its read-only release protocol fixes dataset, Document IR parser, default embedding, deterministic reranker, base runtime, bootstrap method, seed, resample count, and the prohibition on training use. It requires at least 18 queries, 12 sources, non-permissive thresholds, and exactly seven named/versioned evaluation profiles:

1. `lexical-baseline`;
2. `true-cross-lingual`;
3. `domain-shift`;
4. `hard-negatives`;
5. `structured-docs`;
6. `ocr-layout`;
7. `grounding-adversarial`.

Core rejects same-language or bilingual leakage in the cross-lingual relevant blocks, incomplete hard-negative trap sets, missing structured formats, tiny suites, and empty gates before ingestion. The benchmark materializes and ingests real HTML, DOCX, PDF, and blank-PDF-plus-sidecar OCR fixtures, then checks their expected Document IR block kinds in the same release decision as retrieval and grounding.

The evaluator now retains a bounded candidate ranking and distinguishes retrieval misses, reranking misses, locator/parser failures, and generation-grounding failures. Quality output includes overall and per-profile recall@k, MRR, nDCG@k, citation correctness, unsupported-claim rate, adversarial grounding-detection accuracy, deterministic percentile-bootstrap intervals, parser results, and the existing source-diversity/freshness/correction metrics.

The default hash vector remains explicitly labeled `deterministic_lexical_hash_hybrid`, not learned semantics. A candidate learned profile must pass this unchanged release set and its actual distributed runtime before a stable delivery claim. No result from this suite establishes a learning effect.

## Verification

Automated coverage includes:

- shortest-topic routing, all one-click alternatives, adaptive persistence, skipped-item non-penalization, and the no-mastery-Evidence boundary;
- schema rejection before mutation and stale typed-submission protection;
- 22 held-out queries across all seven profiles;
- monolingual cross-language relevant blocks and four hard-negative trap types;
- production HTML, DOCX, PDF formula/table, and OCR parser regression;
- bootstrap uncertainty, grounding adversarial detection, failure taxonomy, and non-learning claim boundaries;
- cross-encoder evaluation and activation against the new release profile;
- rejection of tiny, permissive, and language-leaking release profiles.

Final local validation on 2026-08-18:

- `python -m pytest -m fast -q`: 64 passed;
- `python -m pytest -m integration -q`: 159 passed, 1 skipped; the skip is the existing optional `scale`/HNSW dependency case;
- focused wizard/RAG/semantic/documentation/CLI regression: 34 passed, 1 optional HNSW skip;
- Skill Creator `quick_validate.py`: passed for both the full Skill and packaged bridge Skill;
- release `validate-skill`: passed with 407 Skill lines and no planned capability claims.
- Core wheel and sdist build: passed; both artifacts contain `core-release-v2`, the topic-diagnostic schema, and the v2 benchmark-profile schema.
- isolated wheel install: the packaged runtime ran all 22 release queries, all seven profile gates, and the structured parser gate with `quality_gate: pass`.
