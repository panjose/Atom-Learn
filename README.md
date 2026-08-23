# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

[![Validate AtomLearn](https://github.com/panjose/Atom-Learn/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/panjose/Atom-Learn/actions/workflows/validate.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-3776AB.svg)](pyproject.toml)

AtomLearn is a source-grounded AI Skill for progressive learning and research reading. It can reorganize textbooks into a prerequisite-aware Knowledge Atom graph, or map a research field into a guided paper graph for critical reading and evidence synthesis.

> Never advance while the current atom remains unclear.  
> Do not move to the next Knowledge Atom until the current one is genuinely understood.

> **Release status:** the latest signed stable release is `v0.15.0` and exposes only the `base` profile. Repository engineering status, stable delivery, harness/model behavior evidence, and human learning-effect evidence remain separate claims.

## Implemented Capabilities

- Start from complete textbooks or knowledge bases, a user outline, or only a topic name
- Index local sources and correct coverage gaps with harness Web Search
- Fuse BM25 and a default local multilingual hash projection in the stable base runtime, accept provider vectors, and expose local learned embeddings, HNSW, and reranking only in developer/source installs
- Require explicit evidence verdicts and stable source locators before sparse-input planning
- Generate a Knowledge Atom DAG from textbooks, PDFs, notes, or multiple sources
- Map roots, learning spines, branches, hubs, derivations, historical development, contrasts, applications, and each concept's lineage
- Enforce exactly one Active Atom and guard all prerequisites
- Turn “explain this in detail” into an ordered child-Atom tree instead of a long multi-concept lecture
- Let learners test out, defer, provisionally skip, or restore Atoms without fabricating mastery
- Show whether an unfamiliar related concept belongs now, before, later, on an optional branch, or outside the goal
- Evaluate mastery through explain/apply/discriminate/transfer/teach-back Evidence
- Restore state across sessions with revision conflict protection and event auditing
- Keep 1/3/7/30 reviews as the fixed default, with qualified per-Atom memory, shadow suggestions, and gated active scheduling
- Split or merge Atoms with user confirmation while preserving stable ID aliases
- Discover, screen, refresh, and citation-expand a research field through a protocol-bound paper graph
- Guide one Active Paper through locator-grounded structured claims and reviewable cross-paper synthesis
- Analyze past papers with reviewed joint mappings and separate structural, official, and empirical difficulty
- Build reviewed item families and capacity-checked daily learning, remediation, review, and practice plans
- Adapt response style, pacing, examples, feedback, and research orientation from privacy-safe session signals
- Analyze learning evidence and propose bounded, approval-gated course evolution
- Generate learning, research, personalization, and evolution views from canonical YAML state

The release source of truth is the machine-readable [capability ledger](atom-learn/assets/capabilities.yaml). Implemented describes repository code status, not stable release delivery. The ledger separately records delivery level, runtime, artifact, entrypoint, engineering verification, harness-behavior evidence, and learning-effect evidence. The signed `v0.15.0` runtime exposes only the `base` profile; `ocr`, `scale`, and `semantic` are developer/source extras and are not included in that stable runtime. No AtomLearn learning-gain effect has been established. Engineering checks, scorer calibration, local strategy experiments, and the study-recording contract must never be presented as that evidence.

## Installation

### Stable signed installation

Install a reviewed `atomlearn-manager` wheel independently from the Core it will manage. Then use one idempotent bootstrap command family to preview and apply trust initialization, the signed Core/base runtime, the Manager-owned Codex bridge, and the final capability doctor:

```powershell
python -m pip install <REVIEWED_ATOMLEARN_MANAGER_WHEEL>
atomlearn-manager bootstrap plan 0.15.0 --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222
atomlearn-manager bootstrap apply 0.15.0 --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222 --confirmed
atomlearn-manager bootstrap status
```

`bootstrap plan` is read-only and displays the active-key fingerprint, target platform/profile, exact write locations, Core action, and bridge ownership classification. Verify the fingerprint through an independent channel before applying. Repeating the same apply is idempotent. If an old `~/.codex/skills/atom-learn` is an exact tree from a known signed release, supply that release's local signed ZIP with `--artifact` during the first plan/apply; Manager retains the original as a timestamped recovery backup before installing the bridge. Unknown, modified, linked, or reparse-point Skills are never replaced. Use `atomlearn-manager bootstrap recover` after an interrupted onboarding.

### Developer/source installation

This path is explicitly unmanaged and must not be mixed with the stable Manager-owned bridge. Use it only for repository development, optional extras, or direct Skill iteration.

AtomLearn requires Python 3.10+, PyYAML, pypdf, and python-docx:

```powershell
python -m pip install -e ".[dev]"
atomlearn --help
```

The editable install exposes the short `atomlearn` console command. Direct `python atom-learn/scripts/atomlearn.py ...` invocation remains supported inside a copied Skill directory.

The deterministic small-corpus RAG path needs no model runtime. In a developer/source environment, install `.[ocr]` for the automatic OCR adapter, `.[scale]` for USearch HNSW generations, or `.[semantic]` for explicitly approved local Sentence Transformers models. These extras are not present in signed `v0.15.0` base runtimes and therefore are not stable release capabilities yet. Sidecar OCR and provider-supplied vector attachment remain available through the base path.

Copy or link the repository's `atom-learn` directory into your personal Codex Skills directory, for example:

```text
~/.codex/skills/atom-learn/
```

During development, you can also ask Codex to use the repository's `atom-learn/SKILL.md` directly.

## Quick Verification

```powershell
atomlearn version
atomlearn migrate status
atomlearn init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
atomlearn import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
atomlearn validate courses/calculus
atomlearn status courses/calculus --json
```

Every course render writes the five English views plus aligned `*.zh-CN.md` generated views, including `LEARNING_MAP.zh-CN.md`, `CURRENT.zh-CN.md`, and `PROGRESS.zh-CN.md`. Atom titles and learner content stay unchanged; navigation labels, statuses, and operational text are localized. See [SKILL.md](atom-learn/SKILL.md) for the complete command workflow and teaching behavior, and [SCHEMA.md](atom-learn/references/SCHEMA.md) for structured input formats. Runtime course state is stored in the learner's selected course workspace, not in the Skill installation directory.

Core `0.15.0` retains read-only compatibility and deterministic migration planning while adding the remediation gates documented below. `atomlearn migrate status|plan|validate` never applies a migration; checking status does not create the platform user-data directory. See [Core Version and Migrations](atom-learn/references/MIGRATIONS.md).

Cross-course personalization remains off until the learner explicitly runs `atomlearn profile enable <workspace>`. Global profiles contain only allowlisted enum signals, never import old workspace history automatically, and can be disabled, retired, exported, or reset without deleting their audit trail. `atomlearn policy effective|explain` merges current-turn, workspace, global, strategy, and Core layers with per-value provenance. See [User Profiles](atom-learn/references/USER_PROFILE.md) and [Effective Policy](atom-learn/references/EFFECTIVE_POLICY.md).

Teaching-strategy experiments require a second, independent opt-in through `atomlearn strategy enable-experiments`. Candidates are shadowed and deterministically replayed before live use; only episode-matched, preregistered A/B Evidence can become an outcome. Learning, process, UX, and guardrail metrics remain separate. Promotion requires at least 10 comparable outcomes per arm, 20 episodes, five delayed outcomes per arm, fixed-seed 95% intervals, every primary delayed/transfer learning lower bound above its minimum effect, and every adverse guardrail upper bound within tolerance. Small samples, wide intervals, immediate performance, speed, or satisfaction never promote by themselves. See [Strategy Experiments](atom-learn/references/STRATEGY_EXPERIMENTS.md).

## Evidence v3 and Mastery Feasibility

Evidence v3 requires every mastery score to lie in the intersection of the Atom's required dimensions, the item's declared dimensions, the versioned task-form matrix, and an immutable scorer profile. Choice tasks can establish recognition/discrimination but never explanation or derivation; numeric answers cannot prove an explanation; transfer requires a held-out novel-application contract. Each record freezes its scorer decision fields and registry hash, so a later scorer release cannot reinterpret historical Evidence. Test-only fixtures and unregistered, uncalibrated, disabled, or non-independent scorers remain feedback-only.

Mastery can aggregate several compatible Evidence items, while preserving each contributing task form, item family, scorer identity/hash, and immediate/delayed/transfer window. Per-Atom policy can require multiple families/forms, delayed retention, and held-out transfer. `measure feasibility` lists valid production paths and missing dimensions before teaching; `activate` fails closed when a mastery claim cannot be measured. A course can instead add a valid task/scorer, narrow the claim, or explicitly label an Atom as reading/exploration. Existing v2 Evidence remains historical and is never silently reinterpreted. See [Evidence v3 and Learning Measurement](atom-learn/references/MEASUREMENT.md).

Real learning-effect records use another explicit opt-in. `atomlearn study` preregisters the control, assignment, missing-data policy, immediate/7-day/30-day/near/far measures, and strata; accepts only opaque, minimized local observations; forbids raw answers and content text; never exports automatically; and lets withdrawal exclude all retained observations. The recording contract never claims a learning benefit on its own. See [Learning-effect Studies](atom-learn/references/LEARNING_EFFECT_STUDY.md).

```powershell
atomlearn measure registry
atomlearn measure task-forms
atomlearn measure feasibility courses/calculus
atomlearn measure grade --input deterministic-grade.yaml
atomlearn measure validate-bank --input measurement-bank.yaml
atomlearn measure calibrate --input calibration-set.yaml --output calibration-report.json
atomlearn measure validate-protocol
atomlearn migrate-evidence courses/calculus --confirmed --expected-revision 7
atomlearn study enroll study-transfer-pilot --input enrollment.yaml
atomlearn study status study-transfer-pilot
atomlearn study withdraw study-transfer-pilot --confirmed --expected-study-revision 2
```

## Per-Atom Adaptive Review

Fixed 1/3/7/30 scheduling remains the default and fallback. A review updates per-Atom stability, retrievability, and difficulty only when normal Active Atom Evidence proves a delayed, A/B-quality active-recall attempt. Recognition, passive rereading, legacy or unqualified scoring, satisfaction, chat duration, and response speed alone cannot update memory. Response time is retained only as an audit bucket and is excluded from the adapter calculation.

`adaptive-shadow` computes a suggested date without changing the real queue. `adaptive-active` changes only future schedules after the versioned engineering benchmark passes and the learner explicitly opts in; it never rewrites an existing pending date. Exam objectives respect a target and final-review window. The unified read-only daily queue fits failure remediation, due reviews, blocking prerequisites, new Atoms, and exam practice to time and cognitive-load capacity, returning a visible backlog when work does not fit.

```powershell
atomlearn review benchmark courses/calculus --expected-revision 7
atomlearn review configure courses/calculus --input atom-learn/assets/templates/review-policy.yaml --expected-revision 8
atomlearn review status courses/calculus
atomlearn review queue courses/calculus --date 2026-08-16 --minutes 60
atomlearn review pilot courses/calculus
```

The benchmark verifies deterministic adapter invariants, not learning benefit. A workspace pilot is observational, always blocks automatic promotion, and requires the separately consented study workflow before any causal learning-effect claim. See [Per-Atom Adaptive Review](atom-learn/references/ADAPTIVE_REVIEW.md) and the [Phase 7 implementation record](docs/V0_14_PHASE7_IMPLEMENTATION.md).

## Flexible Course Intake

AtomLearn supports three primary input modes: `sources` for complete textbooks or knowledge bases, `outline` for a syllabus or user-created structure, and `topic` when the user supplies only a field keyword, concept, skill, or name. All three produce the same source-traceable Knowledge Atom DAG, but use different discovery and atomization strategies.

The normal first-use path is the resumable `start` wizard. Topic-only users can provide one phrase; for source, outline, and mixed requests, the harness translates the learner's request into the public start schema. When a topic leaves the starting point, depth, or use case unresolved, Core offers `start_from_basics`, `map_first`, and `use_defaults` as one-click paths or issues one bounded 2–5 item adaptive diagnostic. A don't-know or skipped response stays unknown and is never penalized; raw responses are not persisted, and pre-course diagnostics cannot create mastery Evidence. Core then derives a revisioned Goal Contract and explicit Corpus Policy and returns revision-bound typed actions for clarification, diagnostic interaction, local-candidate coverage judgment, policy-allowed Web Search, planning, phase confirmation, and first-Atom activation. The learner never edits intermediate YAML, interrupted runs replay the exact current action, and stale submissions cannot mutate newer state.

```powershell
python atom-learn/scripts/atomlearn.py start courses/causal --topic "causal inference"
python atom-learn/scripts/atomlearn.py start courses/causal --topic "causal inference" --entry-strategy map_first
python atom-learn/scripts/atomlearn.py start courses/calculus --input atom-learn/assets/templates/start-sources.yaml
python atom-learn/scripts/atomlearn.py start courses/calculus --json
python atom-learn/scripts/atomlearn.py start courses/calculus --submission workflow-submission.json --json
python atom-learn/scripts/atomlearn.py start courses/calculus --print-schema
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

Complete-source mode inventories and reconciles materials; outline mode treats outline items as coverage anchors rather than final Atom boundaries; topic mode performs term disambiguation, records assumptions, resolves only the three high-value entry decisions, and discovers authoritative sources without asking the learner to invent a syllabus. The minimized diagnostic summary is stored in `.atomlearn/topic-diagnostic.yaml` and is passed to planning only as an entry-boundary hint. Mixed input preserves every source, outline, topic, and explicit anchor in one Goal Contract. Every mode must pass candidate-bound coverage before planning. `closed_corpus` reports unsupported goals without Web Search; `correct_gaps` and `discover` search only after local candidates are judged. A proposed plan is validated before phase confirmation, and the first eligible Atom is shown before activation. Legacy sources workspaces are upgraded in memory and cannot retain an old coverage bypass. Unified starter payloads are under `atom-learn/assets/templates/start-*.yaml`, and the machine-readable contracts are [start.schema.json](atom-learn/assets/schemas/start.schema.json), [topic-diagnostic.schema.json](atom-learn/assets/schemas/topic-diagnostic.schema.json), and the typed action/submission schemas. See [Unified Start Wizard](atom-learn/references/START_WIZARD.md), [Typed Workflow Actions](atom-learn/references/WORKFLOW_ACTIONS.md), and [Course Intake Workflows](atom-learn/references/COURSE_INTAKE.md).

## RAG and Corrective Web Search

AtomLearn persists a provider-neutral RAG index inside each learner workspace. Every new source revision first becomes a versioned layout-preserving Document IR shared by retrieval, exam processing, and research attachment. The stable base preserves HTML and DOCX structure, PDF tables and formulas, plus locatable sidecar OCR output in addition to TXT, Markdown, RST, JSON, YAML, and CSV. Retrieval returns exact supporting IR block IDs plus bounded parent context and fuses SQLite FTS5 BM25 with a default local multilingual hash vector; provider-supplied vectors can also be attached. Automatic OCR, approved local learned embeddings, USearch HNSW, and cross-encoder reranking are implemented developer/source paths, not signed `v0.15.0` base-runtime capabilities. Small corpora keep the dependency-light path; without an installed and verified HNSW generation, large dense retrieval skips that component with zero scanned chunks. The harness makes the final direct-support judgment; rank scores are never treated as confidence.

Phase 9 extends this IR for research-grade figure and table evidence. Tables retain row/column/header/span structure, while figures and tables may carry page geometry, a stable crop hash, caption and adjacent-text locators, extraction confidence, and review status. OCR/vision numeric observations are proposal-only by default. A quantitative research claim must point to a current Document IR block and matching crop/metadata, and cannot complete while the evidence is stale, rejected, or awaiting review.

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag document-ir courses/calculus calculus-text
python atom-learn/scripts/atomlearn.py rag embed-local courses/calculus --input local-embedding.yaml
python atom-learn/scripts/atomlearn.py rag index-build courses/calculus --kind all
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
python atom-learn/scripts/atomlearn.py rag correct courses/calculus --input rag-correction.yaml
python atom-learn/scripts/atomlearn.py rag evaluate courses/calculus --input rag-evaluation.yaml
python atom-learn/scripts/atomlearn.py rag benchmark courses/rag-benchmark --profile core-release-v2
```

`rag correct` turns weak, missing, or unverified requirements into structured harness Web Search tasks only when Corpus Policy permits expansion, ingests bounded returned evidence, refreshes retrieval, and repeats until the gate passes or support remains unavailable. `closed_corpus` instead returns an explicit gap and rejects Web evidence. A supported verdict may cite only chunks retrieved as candidates for that exact requirement. `rag evaluate` measures recall@k, MRR, nDCG@k, citation correctness, and unsupported-claim rate against a labeled set. Without all five thresholds or a named profile it returns `quality_gate: report_only`; a pass/fail decision is never inferred from permissive defaults. The held-out `core-release-v2` gate contains seven separately versioned profiles: lexical baseline, true cross-lingual, domain shift, hard negatives, structured documents, OCR/layout, and adversarial grounding. It runs actual HTML, DOCX, PDF, and OCR ingestion, reports bootstrap uncertainty, and separates retrieval, reranking, locator, and generation-grounding failures. Its deterministic hash path is labeled a baseline, not learned semantics, and the gate establishes retrieval/grounding engineering performance—not learning benefit. Local models are never downloaded silently, pickle-capable weights and custom code are rejected, and a cross-encoder can be activated only from a current passing portable benchmark report. No intake mode becomes planning-ready until every mandatory Goal Contract anchor has explicit support for the current intake, Goal Contract, and RAG revisions. See [Shared Document IR](atom-learn/references/DOCUMENT_IR.md), [Retrieval and Corrective Web Search](atom-learn/references/RAG.md), [Learned Semantic and Scale RAG](atom-learn/references/SEMANTIC_RAG.md), and [RAG Design](docs/RAG_DESIGN.md).

Research-field discovery uses the same gate with revision-bound anchors for the research question, surveys, method families, evaluations/datasets, and critique/replication evidence. Use `rag requirements --context research` when building a paper-oriented field map.

## Knowledge Lineage and Concept Maps

AtomLearn separates the authoritative prerequisite DAG from a source-grounded semantic layer. The structural view automatically identifies roots, leaves, the main learning spine, hubs, branches, and cross-module bridges. Optional annotations and typed relations explain each concept's central question, role, contribution, boundaries, motivation, derivation, contrasts, and applications without changing learning prerequisites.

```powershell
python atom-learn/scripts/atomlearn.py lineage init courses/calculus
python atom-learn/scripts/atomlearn.py lineage import courses/calculus --input lineage-import.yaml --expected-lineage-revision 0
python atom-learn/scripts/atomlearn.py lineage overview courses/calculus --lens all
python atom-learn/scripts/atomlearn.py lineage trace courses/calculus calculus.derivative.definition --depth 3
python atom-learn/scripts/atomlearn.py lineage route courses/calculus calculus.rate.average calculus.derivative.geometric
python atom-learn/scripts/atomlearn.py lineage graph-view courses/calculus --focus atom-current
python atom-learn/scripts/atomlearn.py lineage interactive courses/calculus --include-research
```

Use `overview` for a field map, `trace` for one concept's origins and consequences, and `route` to explain how two concepts connect. `graph-view` exports the schema-validated UI-agnostic `graph-view-v1` contract with distinct prerequisite, containment, scheduled-successor, optional-branch, citation, and semantic-related edges. `interactive` is an optional dependency-free standalone HTML adapter with search, focus, edge filters, and node inspection; it never owns or mutates canonical state. The Markdown overview remains the stable fallback. High-confidence semantic relations require a registered source locator, while only prerequisite edges authorize activation. See [Knowledge Lineage Workflow](atom-learn/references/KNOWLEDGE_LINEAGE.md), [Lineage Schema](atom-learn/references/LINEAGE_SCHEMA.md), the [Phase 10 implementation record](docs/V0_15_PHASE10_IMPLEMENTATION.md), and [Knowledge Lineage Design](docs/KNOWLEDGE_LINEAGE_DESIGN.md).

## Flexible Progression and Skips

When material is easy, already known, irrelevant to the current goal, or simply not timely, AtomLearn offers three distinct paths. Diagnostic mode is read-only and prepares the smallest mastery check; defer mode removes an Atom from current recommendations without unlocking successors; provisional mode unlocks the route after explicit confirmation but records no mastery Evidence. Every decision is visible and reversible.

```powershell
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode diagnostic
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode defer --reason-code time_constraint
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode provisional --reason-code already_mastered --confirmed
python atom-learn/scripts/atomlearn.py unskip courses/calculus calculus.limit.approach
```

A later downstream gap can backtrack into a skipped Atom and revoke the assumption automatically. `strict_mastery` courses reject provisional bypass; courses traversed with assumptions report `completed_with_skips` and keep mastered, skipped, and deferred totals separate. Exam plans can emit `verify_skip`, research reading exposes provisional concept assumptions, and lineage views show both skipped and deferred nodes. See [Flexible Progression Workflow](atom-learn/references/FLEXIBLE_PROGRESSION.md) and [Flexible Progression Design](docs/FLEXIBLE_PROGRESSION_DESIGN.md).

## Atomic Detailed Explanations

When a learner asks to explain an Atom in detail, step by step, or in smaller pieces, AtomLearn checks whether the request crosses multiple independently teachable objectives. If so, it creates 2–12 ordered child Atoms instead of returning one long answer. The first child becomes Active immediately; each later child is activated only after the preceding child has mastered Evidence.

```powershell
python atom-learn/scripts/atomlearn.py expand courses/calculus calculus.derivative.definition --plan expand-derivative.yaml
python atom-learn/scripts/atomlearn.py expand courses/calculus calculus.derivative.definition --plan expand-derivative.yaml --confirmed --expected-revision 4
```

The parent remains a real integration objective. After every child is mastered, AtomLearn activates the parent in `integrating` phase and requires a new synthesis check before downstream Atoms unlock. A child can be expanded again, producing a nested tree while still maintaining exactly one Active Atom. Diagnostic test-out remains available, but expanded children cannot be provisionally skipped; they may be deferred and restored. Learning and lineage views display containment separately from prerequisite edges. See [Atomic Detailed Expansion](atom-learn/references/DETAILED_EXPANSION.md) and [Detailed Expansion Design](docs/DETAILED_EXPANSION_DESIGN.md).

Use `atom-learn/assets/templates/expand-plan.yaml` as the starter payload.

## Relation-Aware Concept Routing

When an explanation mentions a concept the learner does not understand, AtomLearn first classifies the relationship instead of immediately opening another long explanation. The result is a compact card showing whether the concept is inside the current Atom, a required prerequisite, already scheduled later, an optional extension, or outside the current goal—plus why, its effect on progress, its destination, and the available choices.

```powershell
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml --action learn_prerequisite --confirmed --expected-revision 4
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml --action add_optional_branch --confirmed --expected-revision 4
```

Preview never changes state. A confirmed required prerequisite temporarily backtracks and then resumes the interrupted Atom. A confirmed optional branch is visible in the learning map and knowledge lineage, but does not block required course completion or outrank required new work. Scheduled concepts name the future Atom that owns them; definition-only context cannot turn into an early multi-concept lesson. See [Relation-Aware Concept Routing](atom-learn/references/CONCEPT_ROUTING.md) and [Concept Routing Design](docs/CONCEPT_ROUTING_DESIGN.md).

Use `atom-learn/assets/templates/concept-route.yaml` as the starter payload.

## Research Reading

AtomLearn can orient reading around a revisioned research protocol instead of treating papers as isolated summaries. Crossref, OpenAlex, PubMed, Semantic Scholar, arXiv, or harness Web Search discovery feeds DOI/title-deduplicated candidates into explicit screening. Direct providers share a normalized contract for identifiers, bibliographic metadata, abstract/license metadata, field completeness, pagination, rate limits, retryable failures, and exact bounded cache receipts. Bounded backward/forward citation expansion retains provider provenance; Semantic Scholar supports both directions and Crossref supports backward DOI references. Completed papers contribute claim-level locators and structured population, intervention/exposure, dataset, method, outcome, metric, effect direction, and assumption facets to a reviewable cross-paper evidence matrix.

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research set-protocol courses/agent-research --input research-protocol.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research discover courses/agent-research --provider semantic_scholar --query "reliable autonomous research agents" --expected-research-revision 1
python atom-learn/scripts/atomlearn.py research submit-discovery courses/agent-research --input research-discovery-submission.yaml --expected-research-revision 2
python atom-learn/scripts/atomlearn.py research screen courses/agent-research --input research-screening.yaml --expected-research-revision 3
python atom-learn/scripts/atomlearn.py research snowball courses/agent-research paper.field.survey --direction backward --provider semantic_scholar --stopping-rule "one depth or 50 candidates"
python atom-learn/scripts/atomlearn.py research refresh courses/agent-research --provider semantic_scholar
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

Research mode keeps at most one Active Paper, requires confirmed inclusion, blocks integrity alerts and unread prerequisites, and generates `RESEARCH_MAP.md`, `CURRENT_PAPER.md`, `LITERATURE_MATRIX.md`, and `RESEARCH_GAPS.md`. An indexed paper can be attached to shared Document IR without copying full text; claim block locators are verified against its source revision. Provider disagreements remain visible for review, and provider failure is never treated as paper absence. Synthesis emits a claim-level matrix with support/opposition stances, effect direction, conditional boundaries, and source locators; model screening and synthesis outputs remain proposals until reviewed. PRISMA-style counts describe only bounded results, and open questions never become novelty claims without current-literature verification. See [Research Reading Workflow](atom-learn/references/RESEARCH_READING.md), the [Phase 8 implementation record](docs/V0_15_PHASE8_IMPLEMENTATION.md), and the [Phase 6 implementation record](docs/V0_14_PHASE6_IMPLEMENTATION.md).

For figure/table claims, the research gate also checks the IR block kind, source revision, crop hash, caption reference, and review/numeric status. Unsupported quantitative claims are routed to review or abstention instead of being promoted into a completed synthesis.

## Exam Analysis and Targeted Preparation

AtomLearn can turn supplied past papers, sample exams, mock exams, or question banks into a source-traceable assessment corpus. It jointly uses question, answer, rubric, and exact source-locator evidence for reviewable Atom mappings; every automatic mapping remains pending until review. Deterministic lexical mapping is always available. Optional semantic candidates are used only when a learned embedding profile and its reranker benchmark are both current; otherwise `auto` reports a typed lexical fallback and `required` fails closed.

```powershell
python atom-learn/scripts/atomlearn.py exam init courses/calculus --title "Calculus Final" --target-date 2027-01-10
python atom-learn/scripts/atomlearn.py exam process-source courses/calculus --source-id past-paper --paper-id paper-2026 --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam process courses/calculus --input exam-process.yaml --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam review-mappings courses/calculus --input exam-mapping-review.yaml --expected-exam-revision 1
python atom-learn/scripts/atomlearn.py exam record-official courses/calculus --input exam-official-difficulty.yaml --expected-exam-revision 2
python atom-learn/scripts/atomlearn.py exam set-target courses/calculus --target-date 2027-01-17 --expected-exam-revision 3
python atom-learn/scripts/atomlearn.py exam calibrate courses/calculus --expected-exam-revision 4
python atom-learn/scripts/atomlearn.py exam record-empirical courses/calculus --input exam-empirical-difficulty.yaml --expected-exam-revision 5
python atom-learn/scripts/atomlearn.py exam propose-families courses/calculus --expected-exam-revision 6
python atom-learn/scripts/atomlearn.py exam review-families courses/calculus --input exam-family-review.yaml --expected-exam-revision 7
python atom-learn/scripts/atomlearn.py exam analyze courses/calculus
python atom-learn/scripts/atomlearn.py exam plan courses/calculus --mode mixed --limit 10
python atom-learn/scripts/atomlearn.py exam daily-plan courses/calculus --input exam-daily-plan.yaml
python atom-learn/scripts/atomlearn.py exam replan courses/calculus --input exam-daily-plan.yaml --reason initial --expected-schedule-revision 0
python atom-learn/scripts/atomlearn.py exam plan-status courses/calculus
python atom-learn/scripts/atomlearn.py exam record-day courses/calculus --input exam-day-outcome.yaml --expected-schedule-revision 1
# For structured data, use `exam import ... --expected-exam-revision 0` instead of `exam process`.
```

`exam process-source` consumes the same Document IR used by RAG and retains exact block provenance while canonical state keeps only concise summaries, associations, and locators. Structural complexity never overwrites reviewed, source-located official difficulty or empirical difficulty qualified by at least 30 attempts, a named population, and a complete time window. `daily-plan` is a read-only preview; `replan` writes an independently revisioned schedule that becomes stale after new Evidence, missed or changed availability, exam/mapping/difficulty changes, revoked skips, inserted prerequisites, or course-plan changes. `plan-status` exposes `due`, `overdue`, `replanned`, and `infeasible` outcomes without weakening mastery. External reminder adapters may consume these events, but the CLI remains complete on its own. Frequency describes only the supplied corpus and is never a prediction. See [Exam Preparation Workflow](atom-learn/references/EXAM_PREPARATION.md), the [v0.14 Phase 6 implementation record](docs/V0_14_PHASE6_IMPLEMENTATION.md), and the [v0.15 Phase 7 implementation record](docs/V0_15_PHASE7_IMPLEMENTATION.md).

## Session-Based Self-Adaptation

AtomLearn can learn durable interaction preferences from chat sessions while keeping the current request in control. The harness distills only allowlisted enum signals—such as detail level, explanation order, example mode, pacing, feedback style, and research orientation—into a workspace-local profile. An explicit preference applies immediately; behavioral or outcome-based inference requires corroboration across at least two distinct sessions.

```powershell
python atom-learn/scripts/atomlearn.py adapt guidance courses/calculus --context teaching
python atom-learn/scripts/atomlearn.py adapt observe-session courses/calculus --input adapt-session.yaml --expected-adaptation-revision 0
python atom-learn/scripts/atomlearn.py adapt profile courses/calculus
python atom-learn/scripts/atomlearn.py adapt retire courses/calculus response.detail --reason-code privacy_request --expected-adaptation-revision 1
```

Raw messages, quotes, free-text summaries, sensitive-trait guesses, and cross-workspace aggregation are forbidden. A new explicit correction overrides an older preference, users can retire any preference, research-only guidance does not leak into teaching, and a current-turn instruction always wins without becoming durable automatically. Start with `atom-learn/assets/templates/adapt-session.yaml`; see [Session Adaptation](atom-learn/references/SESSION_ADAPTATION.md) and [Session Adaptation Design](docs/SESSION_ADAPTATION_DESIGN.md).

Session end is no longer the only episode boundary. After a separate opt-in, a harness can checkpoint activation, exposure, teaching, Evidence attempts, outcomes, review, and finalization as enum-only transitions. Exact request retries replay idempotently; resume requires the same incomplete episode, Active Atom, and workspace revision. A sudden close preserves earlier checkpoints as `incomplete`, while missing outcomes never become strategy promotion samples. Status discloses the coverage start and every integration gap instead of claiming continuous self-evolution. Users can inspect, retire, or disable observation, and the schema has no field for raw messages, answers, quotes, prompts, free-text profiles, or sensitive-trait inference.

```powershell
python atom-learn/scripts/atomlearn.py episode status courses/calculus
python atom-learn/scripts/atomlearn.py episode enable courses/calculus --expected-observability-revision 0
python atom-learn/scripts/atomlearn.py episode begin courses/calculus calculus.limit.approach --episode-key turn-001 --request-key activate-001 --expected-observability-revision 1 --expected-workspace-revision 2
python atom-learn/scripts/atomlearn.py episode resume courses/calculus episode-0123456789abcdef01234567 --request-key resume-001 --expected-observability-revision 2 --expected-workspace-revision 2
```

Episode coverage is harness observability only—not mastery Evidence, a strategy outcome, model-behavior verification, or learning-effect evidence. See [Incremental Episode Checkpoints](atom-learn/references/EPISODE_CHECKPOINTS.md).

## Self-Evolution

AtomLearn can derive metrics from persisted Evidence, reviews, and prerequisite backtracking, then create testable proposals for teaching strategy, review intervals, mastery rubrics, dependency edges, or Atom structure. Evolution is `proposal_only` by default: every change must be previewed, approved by the required authority, validated, checkpointed, and monitored.

```powershell
python atom-learn/scripts/atomlearn.py evolve status courses/calculus
python atom-learn/scripts/atomlearn.py evolve analyze courses/calculus --propose
python atom-learn/scripts/atomlearn.py evolve preview courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve approve courses/calculus evo-000001 --authority learner --actor "learner"
python atom-learn/scripts/atomlearn.py evolve apply courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve monitor courses/calculus evo-000001
```

The engine keeps course and evolution revisions separate, stores no raw learner messages in evolution metrics, and refuses runtime `patch_skill` application. Automatic rollback is allowed only before later learning mutations; otherwise AtomLearn requires a compensating proposal that preserves newer Evidence. See [Bounded Self-Evolution](atom-learn/references/EVOLUTION.md) for the operating workflow.

If the learner explicitly chooses to share a product-level finding, `evolve capsule` can build a local enum-only, bucketed Capsule, enforce privacy lint, show the complete preview, and perform a one-time confirmed file export. Export never uploads, there is no submit or telemetry command, and maintainer conversion always requires an independent reproduction test before any normal reviewed Core change. See [Evolution Capsule](atom-learn/references/EVOLUTION_CAPSULE.md).

Before any human learning study, AtomLearn now provides a versioned harness/model behavior protocol with 18 English/Chinese cases. It measures protocol adherence, Atoms added per turn, future-content leakage, state mutation correctness, citation support, resume success, grading abstention, and exact human-review agreement. Engineering smoke can only return `engineering_smoke_only`. A model compatibility report requires complete bilingual coverage, two distinct human reviewers per case, adjudication of disagreement, and every threshold; even a pass applies only to the exact model, harness, prompt version, language, temperature, and seed recorded in that report.

```powershell
python atom-learn/scripts/atomlearn.py behavior validate-protocol
python atom-learn/scripts/atomlearn.py behavior validate-run --input behavior-run.yaml
python atom-learn/scripts/atomlearn.py behavior evaluate --input behavior-run.yaml --output behavior-report.yaml
python atom-learn/scripts/atomlearn.py behavior validate-report --input behavior-report.yaml
```

The release ledger remains `harness_behavior: not_evaluated` until maintainers review and publish real compatibility reports. Behavior reports always forbid learning-effect claims. See [Harness and Model Behavior Evaluation](atom-learn/references/HARNESS_BEHAVIOR_EVALUATION.md) and the [v0.15 Phase 6 implementation record](docs/V0_15_PHASE6_IMPLEMENTATION.md).

### Signed Release Manager

Core updates are handled by the independent `atomlearn-manager` distribution, never by a learning session. Runtime recipe v2 binds a finite profile name and capability set, full dependency lock, OS/architecture/Python ABI, model policy or explicit model-file lock, native-engine requirements, and a target-platform smoke report into the signed release. Manager installs each profile offline into `runtimes/<core>/<platform>/<profile-hash-prefix>/`, verifies the full hash in its immutable state, runs preflight and Core smoke, and only then atomically changes the active profile pointer. A failed or interrupted profile install leaves the old profile active; Core rollback and profile rollback retain separate paired transaction histories. The current signed `v0.15.0` delivery claim remains `base` only: `scale`, `semantic-cpu`, and `ocr` are candidate recipes until their complete signed matrices pass, while `semantic-gpu` is experimental.

```powershell
atomlearn-manager bootstrap plan 0.15.0 --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222
atomlearn-manager bootstrap apply 0.15.0 --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222 --confirmed
atomlearn-manager bootstrap status
atomlearn-manager bootstrap recover
atomlearn-manager update status
atomlearn-manager profile status
atomlearn-manager doctor
atomlearn-core version
```

The bridge marker binds its resolver to the exact Manager root selected during bootstrap, including custom roots. `codex migrate plan` is read-only; `codex migrate apply --confirmed` takes over only an exact known official source tree, retains the source backup, and journals crash recovery. `profile plan` and `profile apply` select only a profile asset declared by the active signed manifest. Semantic activation requires an absolute local model directory whose revision and every required file hash match the signed lock; it never downloads a model or enables remote code. OCR activation distinguishes installed Python adapters from the required native engine. `doctor` reports `available`, `declared`, `installed`, `usable`, and `stable` independently with a typed blocker and remediation. Public releases require no credential. For a private GitHub Release, Manager first tries the public URL and then uses `ATOMLEARN_GITHUB_TOKEN`, `GH_TOKEN`, or the GitHub CLI credential helper without storing the token in a manifest, workspace, or URL. See [Signed Release Manager](atom-learn/references/RELEASE_MANAGER.md) for profile commands, fingerprint verification, key rotation, recovery, rollback, migration, and transport boundaries.

All self-evolution v2 capabilities remain default-off and independently reversible. The hardened tag-only release workflow requires Windows/Linux Python 3.10–3.13, property tests, replay and v1 compatibility, migration fixtures, every-stage update fault injection, an independent Capsule privacy attack corpus, capability smoke including adaptive review, and a signed gate report before stable assets can be published. See the [Operations and Recovery Runbook](docs/SELF_EVOLUTION_V2_OPERATIONS.md), [0.15.0 Release Notes](docs/releases/v0.15.0.md), and [Changelog](CHANGELOG.md).

## Open Source and Community

AtomLearn is licensed under the [Apache License 2.0](LICENSE). Direct dependency and redistribution notices are recorded in [Third-Party Notices](THIRD_PARTY_NOTICES.md); the optional OCR path uses pypdfium2/PDFium instead of an AGPL renderer. The `NOTICE` file and all third-party license material must remain with redistributed artifacts.

- Read [Contributing](CONTRIBUTING.md) before opening a pull request. Contributions use synthetic or privacy-minimized fixtures and must preserve AtomLearn's state, Evidence, review, privacy, and release-claim boundaries.
- Community participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Report vulnerabilities through GitHub private vulnerability reporting, never a public issue; see the [Security Policy](SECURITY.md).
- Usage channels and diagnostic requirements are documented in [Support](SUPPORT.md), while maintainer and release authority are documented in [Governance](GOVERNANCE.md).
- Cite the exact release used through [CITATION.cff](CITATION.cff).
- Before changing repository visibility, complete the maintainer-only items in the [Open-Source Release Checklist](docs/OPEN_SOURCE_RELEASE_CHECKLIST.md).

Do not commit textbooks, paper corpora, exam answers, learner workspaces, `.atomlearn/` state, tokens, cookies, model credentials, or release private keys. Public examples and tests must remain synthetic, licensed, or privacy-minimized.

## Design Documentation

- [Product and Technical Design](docs/PRODUCT_DESIGN.md)
- [v0.15 Product-Readiness Remediation Design](docs/V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md)
- [v0.15 Phase 4 Stable Bootstrap and Migration Implementation](docs/V0_15_PHASE4_IMPLEMENTATION.md)
- [v0.15 Phase 5 Topic Diagnostic and RAG Evaluation Implementation](docs/V0_15_PHASE5_IMPLEMENTATION.md)
- [v0.15 Phase 6 Episode and Harness Behavior Implementation](docs/V0_15_PHASE6_IMPLEMENTATION.md)
- [v0.15 Phase 7 Exam Closed-Loop Implementation](docs/V0_15_PHASE7_IMPLEMENTATION.md)
- [v0.15 Phase 8 Research Provider Contract Implementation](docs/V0_15_PHASE8_IMPLEMENTATION.md)
- [Detailed Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [v0.14 Phase 6 Exam and Research Implementation](docs/V0_14_PHASE6_IMPLEMENTATION.md)
- [v0.14 Phase 7 Adaptive Review Implementation](docs/V0_14_PHASE7_IMPLEMENTATION.md)
- [Self-Evolution Design](docs/SELF_EVOLUTION_DESIGN.md)
- [Self-Evolution v2 Proposal](docs/SELF_EVOLUTION_V2_DESIGN.md)
- [Self-Evolution v2 Implementation Plan](docs/SELF_EVOLUTION_V2_IMPLEMENTATION_PLAN.md)
- [Self-Evolution v2 Threat Model](docs/SELF_EVOLUTION_V2_THREAT_MODEL.md)
- [Session Adaptation Design](docs/SESSION_ADAPTATION_DESIGN.md)
- [Exam Preparation Design](docs/EXAM_PREPARATION_DESIGN.md)
- [Research Reading Design](docs/RESEARCH_READING_DESIGN.md)
- [Flexible Intake Design](docs/INTAKE_DESIGN.md)
- [RAG Design](docs/RAG_DESIGN.md)
- [Start Wizard Design](docs/START_WIZARD_DESIGN.md)
- [Knowledge Lineage Design](docs/KNOWLEDGE_LINEAGE_DESIGN.md)
- [Flexible Progression Design](docs/FLEXIBLE_PROGRESSION_DESIGN.md)
- [Detailed Expansion Design](docs/DETAILED_EXPANSION_DESIGN.md)
- [Concept Routing Design](docs/CONCEPT_ROUTING_DESIGN.md)
- [Signed Release Manager Operations](atom-learn/references/RELEASE_MANAGER.md)
- [Self-Evolution v2 Operations and Recovery](docs/SELF_EVOLUTION_V2_OPERATIONS.md)
- [0.13.0 Release Notes](docs/releases/v0.13.0.md)
- [0.15.0 Release Notes](docs/releases/v0.15.0.md)
- [0.14.2 Release Notes](docs/releases/v0.14.2.md)

## Development Validation

```powershell
python -m pytest -m fast
python -m pytest -m integration
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/wizard.py atom-learn/scripts/workflow.py atom-learn/scripts/document_ir.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py atom-learn/scripts/exam.py atom-learn/scripts/exam_schedule.py atom-learn/scripts/lineage.py atom-learn/scripts/graph_adapter.py atom-learn/scripts/platform_state.py atom-learn/scripts/migrations.py atom-learn/scripts/user_profile.py atom-learn/scripts/effective_policy.py atom-learn/scripts/strategy.py atom-learn/scripts/strategy_analysis.py atom-learn/scripts/learning_study.py atom-learn/scripts/capsule.py atom-learn/scripts/measurement.py manager/atomlearn_manager/cli.py manager/atomlearn_manager/bootstrap.py manager/atomlearn_manager/codex.py manager/atomlearn_manager/manifest.py manager/atomlearn_manager/manager.py manager/atomlearn_manager/builder.py manager/atomlearn_manager/verify.py manager/atomlearn_manager/statecopy.py manager/atomlearn_manager/launcher.py release/gate.py
```

The fast suite covers CLI/help contracts, packaging, documentation, schemas, and deterministic helpers. The integration suite covers complete filesystem and subprocess workflows. CI runs both layers on Ubuntu and Windows with Python 3.10, 3.11, 3.12, and 3.13. Tests use isolated workspaces under `.test-workspaces/` and do not modify the example files.
