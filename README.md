# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn is a source-grounded AI Skill for progressive learning and research reading. It can reorganize textbooks into a prerequisite-aware Knowledge Atom graph, or map a research field into a guided paper graph for critical reading and evidence synthesis.

> Never advance while the current atom remains unclear.  
> Do not move to the next Knowledge Atom until the current one is genuinely understood.

## Implemented Capabilities

- Start from complete textbooks or knowledge bases, a user outline, or only a topic name
- Index local sources and correct coverage gaps with harness Web Search
- Fuse BM25, default local embeddings, and optional provider embeddings, then deterministically rerank
- Require explicit evidence verdicts and stable source locators before sparse-input planning
- Generate a Knowledge Atom DAG from textbooks, PDFs, notes, or multiple sources
- Map roots, learning spines, branches, hubs, derivations, historical development, contrasts, applications, and each concept's lineage
- Enforce exactly one Active Atom and guard all prerequisites
- Turn “explain this in detail” into an ordered child-Atom tree instead of a long multi-concept lecture
- Let learners test out, defer, provisionally skip, or restore Atoms without fabricating mastery
- Show whether an unfamiliar related concept belongs now, before, later, on an optional branch, or outside the goal
- Evaluate mastery through explain/apply/discriminate/transfer/teach-back Evidence
- Restore state across sessions with revision conflict protection and event auditing
- Schedule reviews at 1/3/7/30-day intervals, with course-level overrides
- Split or merge Atoms with user confirmation while preserving stable ID aliases
- Map a research field into a role-aware paper dependency and citation graph
- Guide one Active Paper through critical notes, claim-evidence extraction, and cross-paper synthesis
- Analyze past papers and question banks for source-traceable coverage, difficulty, and corpus emphasis
- Generate targeted learning or review queues from exam emphasis, learner Evidence, and prerequisites
- Adapt response style, pacing, examples, feedback, and research orientation from privacy-safe session signals
- Analyze learning evidence and propose bounded, approval-gated course evolution
- Generate learning, research, personalization, and evolution views from canonical YAML state

## Installation

AtomLearn requires Python 3.10+, PyYAML, pypdf, and python-docx:

```powershell
python -m pip install -e ".[dev]"
atomlearn --help
```

The editable install exposes the short `atomlearn` console command. Direct `python atom-learn/scripts/atomlearn.py ...` invocation remains supported inside a copied Skill directory.

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

Core `0.13.0` adds a read-only compatibility manifest and deterministic migration planning. `atomlearn migrate status|plan|validate` never applies a migration; checking status does not create the platform user-data directory. See [Core Version and Migrations](atom-learn/references/MIGRATIONS.md).

Cross-course personalization remains off until the learner explicitly runs `atomlearn profile enable <workspace>`. Global profiles contain only allowlisted enum signals, never import old workspace history automatically, and can be disabled, retired, exported, or reset without deleting their audit trail. `atomlearn policy effective|explain` merges current-turn, workspace, global, strategy, and Core layers with per-value provenance. See [User Profiles](atom-learn/references/USER_PROFILE.md) and [Effective Policy](atom-learn/references/EFFECTIVE_POLICY.md).

Teaching-strategy experiments require a second, independent opt-in through `atomlearn strategy enable-experiments`. Candidates are shadowed before live use, assignments are deterministic per Atom episode, explicit preferences exclude an exposure from comparison, and only assessed Evidence can become an outcome. Promotion requires comparable strata, delayed reviews, quality improvement, and passing guardrails; pause removes the overlay without rewriting learning history. See [Strategy Experiments](atom-learn/references/STRATEGY_EXPERIMENTS.md).

## Flexible Course Intake

AtomLearn supports three primary input modes: `sources` for complete textbooks or knowledge bases, `outline` for a syllabus or user-created structure, and `topic` when the user supplies only a field keyword, concept, skill, or name. All three produce the same source-traceable Knowledge Atom DAG, but use different discovery and atomization strategies.

The normal first-use path is the resumable `start` wizard. Topic-only users can provide one phrase; source and outline users provide one JSON/YAML document validated by the public JSON Schema. The wizard creates course, intake, and RAG state, indexes supplied content, returns structured Web Search work when coverage is incomplete, and later accepts the generated course plan through the same command.

```powershell
python atom-learn/scripts/atomlearn.py start courses/causal --topic "causal inference"
python atom-learn/scripts/atomlearn.py start courses/calculus --input atom-learn/assets/templates/start-sources.yaml
python atom-learn/scripts/atomlearn.py start courses/calculus --print-schema
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

Complete-source mode inventories and reconciles materials; outline mode treats outline items as coverage anchors rather than final Atom boundaries; topic mode performs term disambiguation and authoritative source discovery without asking the learner to invent a syllabus. Intake completion verifies that every non-archived Atom has a source locator. Unified starter payloads are under `atom-learn/assets/templates/start-*.yaml`, and the machine-readable contract is [start.schema.json](atom-learn/assets/schemas/start.schema.json). See [Unified Start Wizard](atom-learn/references/START_WIZARD.md) and [Course Intake Workflows](atom-learn/references/COURSE_INTAKE.md).

## RAG and Corrective Web Search

AtomLearn persists a provider-neutral RAG index inside each learner workspace. It preserves HTML and DOCX structure, PDF tables and formulas, and locatable OCR output in addition to TXT, Markdown, RST, JSON, YAML, and CSV. Retrieval fuses SQLite FTS5 BM25, a default local multilingual hash embedding, and optional provider embeddings, then applies a testable deterministic reranker. The harness makes the final direct-support judgment; rank scores are never treated as confidence.

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
python atom-learn/scripts/atomlearn.py rag correct courses/calculus --input rag-correction.yaml
python atom-learn/scripts/atomlearn.py rag evaluate courses/calculus --input rag-evaluation.yaml
```

`rag correct` turns weak, missing, or unverified requirements into structured harness Web Search tasks, ingests bounded returned evidence, refreshes retrieval, and repeats until the gate passes or support remains unavailable. A supported verdict may cite only chunks retrieved as candidates for that exact requirement. `rag evaluate` measures recall@k, MRR, nDCG@k, citation correctness, and unsupported-claim rate against a labeled set. Outline and topic intake cannot become planning-ready until every mandatory anchor has explicit support for the current intake revision. See [Retrieval and Corrective Web Search](atom-learn/references/RAG.md) and [RAG Design](docs/RAG_DESIGN.md).

Research-field discovery uses the same gate with revision-bound anchors for the research question, surveys, method families, evaluations/datasets, and critique/replication evidence. Use `rag requirements --context research` when building a paper-oriented field map.

## Knowledge Lineage and Concept Maps

AtomLearn separates the authoritative prerequisite DAG from a source-grounded semantic layer. The structural view automatically identifies roots, leaves, the main learning spine, hubs, branches, and cross-module bridges. Optional annotations and typed relations explain each concept's central question, role, contribution, boundaries, motivation, derivation, contrasts, and applications without changing learning prerequisites.

```powershell
python atom-learn/scripts/atomlearn.py lineage init courses/calculus
python atom-learn/scripts/atomlearn.py lineage import courses/calculus --input lineage-import.yaml --expected-lineage-revision 0
python atom-learn/scripts/atomlearn.py lineage overview courses/calculus --lens all
python atom-learn/scripts/atomlearn.py lineage trace courses/calculus calculus.derivative.definition --depth 3
python atom-learn/scripts/atomlearn.py lineage route courses/calculus calculus.rate.average calculus.derivative.geometric
```

Use `overview` for a field map, `trace` for one concept's origins and consequences, and `route` to explain how two concepts connect. The same map can overlay current learning status, sample-contained exam emphasis, or the concepts demanded by mapped research papers. High-confidence semantic relations require a registered source locator, while the prerequisite DAG remains the only authority for activation. See [Knowledge Lineage Workflow](atom-learn/references/KNOWLEDGE_LINEAGE.md), [Lineage Schema](atom-learn/references/LINEAGE_SCHEMA.md), and [Knowledge Lineage Design](docs/KNOWLEDGE_LINEAGE_DESIGN.md).

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

AtomLearn can orient reading around a research question instead of treating papers as isolated summaries. It normalizes DOI identifiers, merges DOI/title duplicates, verifies provider metadata, acquires outgoing citation relations from Crossref/OpenAlex or harness snapshots, and builds a guided role-aware paper map. Completed papers feed source-preserving cross-paper claim themes that keep agreements, contradictions, evidence strength, limitations, and provenance explicit.

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research import courses/agent-research --input examples/research-mini/plan.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research reconcile-metadata courses/agent-research --input research-metadata.yaml --expected-research-revision 1
python atom-learn/scripts/atomlearn.py research fetch-metadata courses/agent-research --provider crossref --expected-research-revision 2
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

Research mode keeps at most one Active Paper, blocks unread paper prerequisites, surfaces missing Knowledge Atoms, and generates `RESEARCH_MAP.md`, `CURRENT_PAPER.md`, `LITERATURE_MATRIX.md`, and `RESEARCH_GAPS.md`. Metadata conflicts and unresolved external references remain auditable; a synthesized single-source theme never masquerades as consensus. It does not store complete paper text or claim novelty without a current literature search. See [Research Reading Workflow](atom-learn/references/RESEARCH_READING.md).

## Exam Analysis and Targeted Preparation

AtomLearn can turn supplied past papers, sample exams, mock exams, or question banks into a source-traceable assessment corpus. It automatically splits numbered questions, associates answer/marking sections, proposes reviewable knowledge-point and Atom mappings, and estimates a transparent five-factor difficulty rubric. Official anchors can calibrate non-official estimates. Analysis reports cross-paper coverage, score share, sample-contained emphasis, confidence, review status, and unmapped course gaps.

```powershell
python atom-learn/scripts/atomlearn.py exam init courses/calculus --title "Calculus Final" --target-date 2027-01-10
python atom-learn/scripts/atomlearn.py exam process courses/calculus --input exam-process.yaml --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam review-mappings courses/calculus --input exam-mapping-review.yaml --expected-exam-revision 1
python atom-learn/scripts/atomlearn.py exam calibrate courses/calculus --expected-exam-revision 2
python atom-learn/scripts/atomlearn.py exam analyze courses/calculus
python atom-learn/scripts/atomlearn.py exam plan courses/calculus --mode mixed --limit 10
# For structured data, use `exam import ... --expected-exam-revision 0` instead of `exam process`.
```

The targeted queue combines corpus emphasis, current learner Evidence, calibrated question difficulty, and prerequisite order to recommend `learn`, `remediate`, `review`, or `repair_prerequisites`. Full questions, answers, and marking schemes remain in the private source/RAG layer; canonical exam state keeps concise summaries, associations, and locators. Frequency describes only the supplied corpus and is never presented as a prediction of future questions. See [Exam Preparation Workflow](atom-learn/references/EXAM_PREPARATION.md) and [Exam Preparation Design](docs/EXAM_PREPARATION_DESIGN.md).

## Session-Based Self-Adaptation

AtomLearn can learn durable interaction preferences from chat sessions while keeping the current request in control. The harness distills only allowlisted enum signals—such as detail level, explanation order, example mode, pacing, feedback style, and research orientation—into a workspace-local profile. An explicit preference applies immediately; behavioral or outcome-based inference requires corroboration across at least two distinct sessions.

```powershell
python atom-learn/scripts/atomlearn.py adapt guidance courses/calculus --context teaching
python atom-learn/scripts/atomlearn.py adapt observe-session courses/calculus --input adapt-session.yaml --expected-adaptation-revision 0
python atom-learn/scripts/atomlearn.py adapt profile courses/calculus
python atom-learn/scripts/atomlearn.py adapt retire courses/calculus response.detail --reason-code privacy_request --expected-adaptation-revision 1
```

Raw messages, quotes, free-text summaries, sensitive-trait guesses, and cross-workspace aggregation are forbidden. A new explicit correction overrides an older preference, users can retire any preference, research-only guidance does not leak into teaching, and a current-turn instruction always wins without becoming durable automatically. Start with `atom-learn/assets/templates/adapt-session.yaml`; see [Session Adaptation](atom-learn/references/SESSION_ADAPTATION.md) and [Session Adaptation Design](docs/SESSION_ADAPTATION_DESIGN.md).

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

### Signed Release Manager

Core updates are handled by the independent `atomlearn-manager` distribution, never by a learning session. It verifies an Ed25519-signed immutable GitHub release, rejects hostile archives, migrates only copied state, installs versions side by side, runs the new Core against mirrored user/workspace state, and switches the active pointer only after health checks pass. Failed or interrupted updates retain the old Core and use a guarded transaction journal for recovery; downgrades are limited to the paired previous Core and its matching state snapshot.

```powershell
python -m pip install -e ./manager
atomlearn-manager --help
atomlearn-manager update status
atomlearn-manager update recover
atomlearn-core version
```

See [Signed Release Manager](atom-learn/references/RELEASE_MANAGER.md) for trust bootstrap, update planning, recovery, rollback, offline behavior, threat boundaries, and maintainer release construction.

All self-evolution v2 capabilities remain default-off and independently reversible. The hardened tag-only release workflow now requires Windows/Linux Python 3.10–3.13, property tests, replay and v1 compatibility, migration fixtures, every-stage update fault injection, an independent Capsule privacy attack corpus, and a signed gate report before stable assets can be published. See the [Operations and Recovery Runbook](docs/SELF_EVOLUTION_V2_OPERATIONS.md), [0.13.0 Release Notes](docs/releases/v0.13.0.md), and [Changelog](CHANGELOG.md).

## Design Documentation

- [Product and Technical Design](docs/PRODUCT_DESIGN.md)
- [Detailed Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
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

## Development Validation

```powershell
python -m pytest -m fast
python -m pytest -m integration
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/wizard.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py atom-learn/scripts/exam.py atom-learn/scripts/lineage.py atom-learn/scripts/platform_state.py atom-learn/scripts/migrations.py atom-learn/scripts/user_profile.py atom-learn/scripts/effective_policy.py atom-learn/scripts/strategy.py atom-learn/scripts/capsule.py manager/atomlearn_manager/cli.py manager/atomlearn_manager/manager.py manager/atomlearn_manager/builder.py manager/atomlearn_manager/verify.py manager/atomlearn_manager/statecopy.py manager/atomlearn_manager/launcher.py release/gate.py
```

The fast suite covers CLI/help contracts, packaging, documentation, schemas, and deterministic helpers. The integration suite covers complete filesystem and subprocess workflows. CI runs both layers on Ubuntu and Windows with Python 3.10, 3.11, 3.12, and 3.13. Tests use isolated workspaces under `.test-workspaces/` and do not modify the example files.
