# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn is a source-grounded AI Skill for progressive learning and research reading. It can reorganize textbooks into a prerequisite-aware Knowledge Atom graph, or map a research field into a guided paper graph for critical reading and evidence synthesis.

> Never advance while the current atom remains unclear.  
> Do not move to the next Knowledge Atom until the current one is genuinely understood.

## Implemented Capabilities

- Start from complete textbooks or knowledge bases, a user outline, or only a topic name
- Index local sources and correct coverage gaps with harness Web Search
- Fuse BM25, multilingual subword, and optional provider-embedding retrieval with RRF
- Require explicit evidence verdicts and stable source locators before sparse-input planning
- Generate a Knowledge Atom DAG from textbooks, PDFs, notes, or multiple sources
- Enforce exactly one Active Atom and guard all prerequisites
- Route in-Atom questions, blocking prerequisites, future questions, and Parking Lot items
- Evaluate mastery through explain/apply/discriminate/transfer/teach-back Evidence
- Restore state across sessions with revision conflict protection and event auditing
- Schedule reviews at 1/3/7/30-day intervals, with course-level overrides
- Split or merge Atoms with user confirmation while preserving stable ID aliases
- Map a research field into a role-aware paper dependency and citation graph
- Guide one Active Paper through critical notes, claim-evidence extraction, and cross-paper synthesis
- Adapt response style, pacing, examples, feedback, and research orientation from privacy-safe session signals
- Analyze learning evidence and propose bounded, approval-gated course evolution
- Generate learning, research, personalization, and evolution views from canonical YAML state

## Installation

AtomLearn requires Python 3.10+, PyYAML, pypdf, and python-docx:

```powershell
python -m pip install -e .
```

Copy or link the repository's `atom-learn` directory into your personal Codex Skills directory, for example:

```text
~/.codex/skills/atom-learn/
```

During development, you can also ask Codex to use the repository's `atom-learn/SKILL.md` directly.

## Quick Verification

```powershell
python atom-learn/scripts/atomlearn.py init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py validate courses/calculus
python atom-learn/scripts/atomlearn.py status courses/calculus --json
```

See [SKILL.md](atom-learn/SKILL.md) for the complete command workflow and teaching behavior, and [SCHEMA.md](atom-learn/references/SCHEMA.md) for structured input formats. Runtime course state is stored in the learner's selected course workspace, not in the Skill installation directory.

## Flexible Course Intake

AtomLearn supports three primary input modes: `sources` for complete textbooks or knowledge bases, `outline` for a syllabus or user-created structure, and `topic` when the user supplies only a field keyword, concept, skill, or name. All three produce the same source-traceable Knowledge Atom DAG, but use different discovery and atomization strategies.

```powershell
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

Complete-source mode inventories and reconciles materials; outline mode treats outline items as coverage anchors rather than final Atom boundaries; topic mode performs term disambiguation and authoritative source discovery without asking the learner to invent a syllabus. Intake completion verifies that every non-archived Atom has a source locator. Starter payloads are available under `atom-learn/assets/templates/intake-*.yaml`. See [Course Intake Workflows](atom-learn/references/COURSE_INTAKE.md).

## RAG and Corrective Web Search

AtomLearn persists a provider-neutral RAG index inside each learner workspace. It extracts structure-aware chunks from TXT, Markdown, RST, HTML, JSON, YAML, CSV, searchable PDF, and DOCX sources. Retrieval combines SQLite FTS5 BM25, multilingual subword similarity, and optional provider embeddings through reciprocal rank fusion. The harness then reranks the candidate evidence instead of treating the fusion score as confidence.

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
```

Weak, missing, or unverified outline/topic requirements fail closed and produce focused Web Search queries. The harness opens authoritative results and ingests only bounded evidence with URL, retrieval time, search query, authority, and stable locator using `rag ingest-web`. Outline and topic intake cannot become planning-ready until every mandatory anchor has an explicit supported verdict for the current intake revision. See [Retrieval and Corrective Web Search](atom-learn/references/RAG.md) and [RAG Design](docs/RAG_DESIGN.md).

Research-field discovery uses the same gate with revision-bound anchors for the research question, surveys, method families, evaluations/datasets, and critique/replication evidence. Use `rag requirements --context research` when building a paper-oriented field map.

## Research Reading

AtomLearn can orient reading around a research question instead of treating papers as isolated summaries. It builds a guided map across surveys, seminal work, theory and methods, benchmarks and datasets, critiques and replications, and applications. Each completed paper records evidence-linked claims, limitations, open questions, and relations to other work.

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research import courses/agent-research --input examples/research-mini/plan.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

Research mode keeps at most one Active Paper, blocks unread paper prerequisites, surfaces missing Knowledge Atoms, and generates `RESEARCH_MAP.md`, `CURRENT_PAPER.md`, `LITERATURE_MATRIX.md`, and `RESEARCH_GAPS.md`. It does not store complete paper text or claim novelty without a current literature search. See [Research Reading Workflow](atom-learn/references/RESEARCH_READING.md).

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

## Design Documentation

- [Product and Technical Design](docs/PRODUCT_DESIGN.md)
- [Detailed Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Self-Evolution Design](docs/SELF_EVOLUTION_DESIGN.md)
- [Session Adaptation Design](docs/SESSION_ADAPTATION_DESIGN.md)
- [Research Reading Design](docs/RESEARCH_READING_DESIGN.md)
- [Flexible Intake Design](docs/INTAKE_DESIGN.md)
- [RAG Design](docs/RAG_DESIGN.md)

## Development Validation

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py
```

The repository includes small calculus, operating-systems, and synthetic research-reading plans as test fixtures. Automated tests use isolated workspaces under `.test-workspaces/` and do not modify the example files.
