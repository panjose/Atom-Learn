# Changelog

## Unreleased - v0.15

- Phase 2 adds a versioned task-form-to-dimension matrix, immutable scorer-v2 profiles and Evidence v3 snapshots, conservative v1/v2 compatibility, multi-Evidence mastery aggregation, evidence-family/window policy, and an activation-blocking Mastery Feasibility Preflight. Choice and numeric correctness can no longer be copied into incompatible higher-order mastery dimensions.
- Phase 0 upgrades the capability ledger to schema v2 and separates repository implementation from stable delivery. The release gate now attests the stable runtime profile, developer-only extras, artifact/entrypoint availability, harness-behavior evidence, and product learning-effect evidence. The signed `v0.14.2` line truthfully exposes only `base`; `ocr`, `scale`, and `semantic` remain developer/source extras until immutable signed runtime profiles ship.
- Public documentation now labels source installation separately from Manager onboarding, distinguishes sidecar OCR and provider vectors from optional automatic OCR/local learned retrieval, and states that engineering tests, calibration, local experiments, and the study contract do not establish a causal AtomLearn learning-gain result.

## 0.14.2 - release-ready

- Patch release `0.14.2` preserves the complete Phase 0–7 feature set and adds `review` to the release-manifest-v2 smoke-capability contract. A regression test now proves every capability required by the ledger is accepted by the signed release schema.
- The immutable `v0.14.1` tag passed all Windows/Linux Python 3.10–3.13 release gates but failed closed during final manifest validation, before publishing a GitHub Release or stable asset. Its tag remains unmoved as an auditable failed release attempt.

See [0.14.2 release notes](docs/releases/v0.14.2.md) for the patch boundary and release invariants.

## 0.14.1 - release-ready

- Patch release `0.14.1` preserves the complete Phase 0–7 feature set and replaces the Python 3.11-only `tomllib` call in the tag workflow with Python 3.10-compatible installed-package metadata. The `v0.14.0` tag failed before publishing a GitHub Release or stable assets and remains unmoved as an auditable failed release attempt.

- Phase 0 makes Manager transport failures typed and assertion-free, makes the uploaded and embedded release gate report byte-identical canonical JSON, and adds a release-gated capability/implementation ledger.
- RAG evaluation now reports metrics without claiming pass when thresholds are absent, rejects partial threshold sets, and strategy outcomes ignore non-required Evidence dimensions.
- Phase 1 adds manifest v2, a fixed Manager-owned Codex bridge, signed offline wheelhouse recipes, release-specific runtimes, runtime content verification, capability-aware release smoke tests, a published fingerprinted trust bundle with signed rotation and break-glass recovery, and credential-bounded private GitHub Release transport.
- Phase 2 adds Evidence v2 scorer provenance and eligibility gates, deterministic exact-choice and numeric/unit grading, held-out mastery/retention/transfer item banks, reproducible stratified open-response calibration reports, legacy migration and strategy exclusion, and an explicit three-layer learning benchmark claim protocol.
- Phase 3 adds outcome-eligible strategy experiments with deterministic stratified uncertainty intervals, minimum effects, sample floors, delayed/transfer requirements, fixed stopping rules, replayed shadow assignments, conservative v1 migration, and a separately consented local-only learning-effect study contract with withdrawal and privacy minimization.
- Phase 4 adds a revision-bound typed harness action/submission protocol, bilingual no-YAML start console, explicit phase and first-Atom confirmation, and a versioned layout-preserving Document IR shared by RAG, exam source processing, and research source attachment.
- Phase 5 adds developer/source adapters for explicitly approved local learned embeddings and cross-encoders, crash-contained USearch HNSW generations with incremental rebuilds, child-grounded parent context, strict provider-profile replacement, and a bundled multilingual/multidomain/multistructure retrieval gate that cannot pass through empty thresholds. These optional adapters are implemented but are not included in the signed `v0.14.2` base runtime.
- Phase 6 adds joint question/answer/rubric exam processing, reviewed mappings and item families, source-located empirical difficulty, capacity-checked exam calendars, protocol-bound Crossref/OpenAlex/harness research discovery, citation expansion, integrity refresh, Document IR claim locators, and reviewed structured cross-paper synthesis.
- Phase 7 adds qualified per-Atom D/S/R memory, fixed/shadow/benchmark-and-opt-in active scheduling, exam target windows, a capacity-aware unified daily queue, deterministic replay validation, observational non-promoting pilot reports, installed-runtime review smoke coverage, and constrained hashing for CPython's contained Linux venv `lib64 -> lib` alias.

See [0.14.1 release notes](docs/releases/v0.14.1.md) for compatibility, review gates, limitations, and verification scope.

## 0.13.0 - release-ready

- Delivered self-evolution v2 Phases 0–6 with default-off cross-course personalization, conservative strategy experiments, privacy-linted local Capsules, deterministic migrations, and a signed side-by-side Release Manager.
- Added complete RAG correction/evaluation, research synthesis, exam processing, knowledge lineage, flexible progression, atomic detailed expansion, and relation-aware concept routing completed earlier in this release line.
- Added Windows/Linux Python 3.10–3.13 validation plus property, replay, privacy-attack, migration, release identity, hostile archive, and every-stage fault-recovery gates.

See [0.13.0 release notes](docs/releases/v0.13.0.md) for compatibility, security, update, rollback, and known-limit details.
