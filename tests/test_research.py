from __future__ import annotations

import copy
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))
from research import ProviderError, ResearchEngine

CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
PLAN = ROOT / "examples" / "research-mini" / "plan.yaml"
CALCULUS_PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
RUN_ROOT = ROOT / ".test-workspaces"


def invoke(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def output(result: subprocess.CompletedProcess[str]) -> dict | list:
    return json.loads(result.stdout)


def payload(path: Path, name: str, data: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def workspace(label: str) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"research-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"research.{label}",
            "--title",
            f"Research {label}",
            "--goal",
            "Build a critical map of a research field",
        )
    )
    initialized = output(
        invoke(
            "research",
            "init",
            path,
            "--field",
            "Reliable autonomous research agents",
            "--question",
            "Which design choices improve reliability?",
            "--scope",
            "Architectures, evaluations, and replications",
            "--include",
            "Contains inspectable evidence",
            "--exclude",
            "Product announcement only",
        )
    )
    assert initialized["research_revision"] == 0
    return path


def import_plan(path: Path, revision: int = 0) -> dict:
    return output(
        invoke(
            "research",
            "import",
            path,
            "--input",
            PLAN,
            "--expected-research-revision",
            revision,
        )
    )


def critical_note(
    path: Path,
    paper_id: str,
    *,
    relation: dict | None = None,
    open_question: str = "Which setting would falsify the central claim?",
) -> Path:
    data = {
        "problem": "Existing systems do not expose enough evidence about reliability.",
        "contributions": ["Defines a testable reliability mechanism."],
        "approach": "Compares a bounded mechanism against a documented baseline.",
        "datasets": ["Synthetic evaluation suite"],
        "claims": [
            {
                "statement": "The mechanism improves auditability under the reported conditions.",
                "evidence_summary": "The controlled comparison reports fewer unsupported actions.",
                "strength": "moderate",
                "effect": "Fewer unsupported actions than the documented baseline.",
                "uncertainty": "Single task-family evaluation.",
                "facets": {
                    "population": ["autonomous research agents"],
                    "setting": ["controlled evaluation"],
                    "dataset": ["Synthetic evaluation suite"],
                    "method": ["bounded verification mechanism"],
                    "baseline": ["documented baseline"],
                    "outcome": ["unsupported action rate"],
                    "metric": ["unsupported actions"],
                    "assumption": ["inspectable tool traces"],
                },
                "evidence_locator": {
                    "locator": "Results section, sentence 3",
                    "kind": "sentence",
                    "extraction_method": "human",
                    "confidence": 1.0,
                    "source_id": None,
                    "source_revision": None,
                    "block_ids": [],
                },
            }
        ],
        "limitations": ["The evaluation covers only one task family."],
        "open_questions": [open_question],
        "field_positioning": "Extends reliability work with an explicit verification step.",
        "relations": [relation] if relation else [],
    }
    return payload(path, f"note-{paper_id}.yaml", data)


def mutate(path: Path, action: str, revision: int, *args: object) -> dict:
    return output(
        invoke(
            "research",
            action,
            path,
            *args,
            "--expected-research-revision",
            revision,
        )
    )


def test_research_initialization_import_guided_queue_and_views() -> None:
    path = workspace("orientation")
    imported = import_plan(path)
    assert imported["research_revision"] == 1
    assert imported["result"]["total_papers"] == 3

    state = output(invoke("research", "status", path))
    assert state["valid"] is True
    assert state["counts"] == {"queued": 3}
    assert [item["id"] for item in state["next_candidates"]] == ["paper.field.survey"]
    assert output(invoke("validate", path))["ok"] is True
    assert output(invoke("research", "validate", path))["papers"] == 3
    for filename in ["RESEARCH_MAP.md", "CURRENT_PAPER.md", "LITERATURE_MATRIX.md", "RESEARCH_GAPS.md"]:
        assert (path / filename).is_file()


def test_research_requires_prerequisites_single_focus_and_critical_completion() -> None:
    path = workspace("guards")
    revision = import_plan(path)["research_revision"]
    blocked = invoke(
        "research",
        "activate",
        path,
        "paper.method.alpha",
        "--expected-research-revision",
        revision,
        check=False,
    )
    assert blocked.returncode == 2
    assert "prerequisites are unread" in blocked.stderr

    activated = mutate(path, "activate", revision, "paper.field.survey")
    revision = activated["research_revision"]
    second_active = invoke(
        "research",
        "activate",
        path,
        "paper.method.alpha",
        "--expected-research-revision",
        revision,
        check=False,
    )
    assert second_active.returncode == 2
    assert "before activating another paper" in second_active.stderr

    incomplete = payload(
        path,
        "incomplete-note.yaml",
        {
            "problem": "A problem is stated.",
            "contributions": [],
            "approach": "",
            "claims": [],
            "limitations": [],
            "field_positioning": "",
        },
    )
    recorded = mutate(path, "note", revision, "paper.field.survey", "--input", incomplete)
    revision = recorded["research_revision"]
    blocked_completion = invoke(
        "research",
        "complete",
        path,
        "paper.field.survey",
        "--expected-research-revision",
        revision,
        check=False,
    )
    assert blocked_completion.returncode == 2
    assert "not critically complete" in blocked_completion.stderr

    note = critical_note(path, "paper.field.survey")
    recorded = mutate(path, "note", revision, "paper.field.survey", "--input", note)
    completed = mutate(path, "complete", recorded["research_revision"], "paper.field.survey")
    candidates = output(invoke("research", "next", path))
    assert [item["id"] for item in candidates] == ["paper.method.alpha"]

    stale = invoke(
        "research",
        "activate",
        path,
        "paper.method.alpha",
        "--expected-research-revision",
        completed["research_revision"] - 1,
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale research revision" in stale.stderr


def test_research_synthesis_surfaces_claims_contradictions_and_gaps() -> None:
    path = workspace("synthesis")
    revision = import_plan(path)["research_revision"]
    for paper_id, relation in [
        ("paper.field.survey", None),
        (
            "paper.method.alpha",
            {
                "paper_id": "paper.field.survey",
                "type": "extends",
                "note": "Adds an operational mechanism to the survey taxonomy.",
            },
        ),
        (
            "paper.replication.alpha",
            {
                "paper_id": "paper.method.alpha",
                "type": "contradicts",
                "note": "The effect disappears under a broader task distribution.",
            },
        ),
    ]:
        activated = mutate(path, "activate", revision, paper_id)
        note = critical_note(path, paper_id, relation=relation)
        recorded = mutate(path, "note", activated["research_revision"], paper_id, "--input", note)
        completed = mutate(path, "complete", recorded["research_revision"], paper_id)
        revision = completed["research_revision"]

    synthesized = mutate(path, "synthesize", revision)
    result = synthesized["result"]
    assert len(result["integrated_paper_ids"]) == 3
    assert len(result["contradictions"]) == 1
    assert result["contradictions"][0]["target_paper_id"] == "paper.method.alpha"
    assert len(result["evidence_synthesis"]["themes"]) == 1
    assert result["evidence_synthesis"]["themes"][0]["assessment"] == "contested"
    assert result["evidence_synthesis"]["themes"][0]["review_status"] == "proposed"
    assert len(result["evidence_synthesis"]["themes"][0]["claims"]) == 3
    assert result["evidence_synthesis"]["themes"][0]["conditional_differences"] == {}
    reviewed = output(
        invoke(
            "research", "review-synthesis", path,
            "--input", payload(path, "synthesis-review.yaml", {
                "reviews": [{"theme_id": "theme.001", "decision": "confirm", "label": None, "reason": "Shared structured facets and explicit relations."}]
            }),
            "--expected-research-revision", synthesized["research_revision"],
        )
    )
    assert reviewed["result"]["confirmed"] == 1
    matrix = (path / "LITERATURE_MATRIX.md").read_text(encoding="utf-8")
    gaps = (path / "RESEARCH_GAPS.md").read_text(encoding="utf-8")
    assert "evidence-linked claim" not in matrix
    assert "improves auditability" in matrix
    assert "contradicts `paper.method.alpha`" in gaps
    assert "Which setting would falsify" in gaps
    assert output(invoke("research", "status", path))["status"] == "complete"


def test_research_import_deduplicates_doi_and_title_and_rewrites_citations() -> None:
    path = workspace("deduplicate")
    plan = {
        "papers": [
            {
                "id": "paper.canonical",
                "title": "A Reliable Method",
                "doi": "https://doi.org/10.1234/Example.1",
                "authors": ["A. Author"],
                "role": "method",
                "priority": 1,
                "status": "queued",
                "prerequisite_paper_ids": [],
                "cites": [],
            },
            {
                "id": "paper.duplicate",
                "title": "A reliable method!",
                "doi": "doi:10.1234/example.1",
                "authors": ["B. Collaborator"],
                "role": "method",
                "priority": 2,
                "status": "queued",
                "prerequisite_paper_ids": [],
                "cites": [],
            },
            {
                "id": "paper.followup",
                "title": "A Follow-up Evaluation",
                "doi": "10.1234/example.2",
                "authors": ["C. Evaluator"],
                "role": "replication",
                "priority": 2,
                "status": "queued",
                "prerequisite_paper_ids": [],
                "cites": ["paper.duplicate"],
            },
        ]
    }
    imported = output(
        invoke("research", "import", path, "--input", payload(path, "duplicates.yaml", plan))
    )
    assert imported["result"]["total_papers"] == 2
    assert imported["result"]["deduplicated"] == [
        {"duplicate_id": "paper.duplicate", "canonical_id": "paper.canonical"}
    ]
    papers = output(invoke("research", "list", path))
    followup = next(item for item in papers if item["id"] == "paper.followup")
    assert followup["cites"] == ["paper.canonical"]
    assert next(item for item in papers if item["id"] == "paper.canonical")["doi"] == "10.1234/example.1"


def test_research_metadata_reconciliation_verifies_records_and_acquires_internal_citations() -> None:
    path = workspace("metadata")
    plan = {
        "papers": [
            {
                "id": "paper.base",
                "title": "Base Evidence",
                "authors": ["A. Researcher"],
                "year": 2022,
                "doi": "10.5555/base",
                "role": "seminal",
                "priority": 1,
                "status": "queued",
                "prerequisite_paper_ids": [],
                "cites": [],
            },
            {
                "id": "paper.new",
                "title": "New Evidence",
                "authors": ["B. Researcher"],
                "year": 2024,
                "doi": "10.5555/new",
                "role": "replication",
                "priority": 2,
                "status": "queued",
                "prerequisite_paper_ids": [],
                "cites": [],
            },
        ]
    }
    imported = output(invoke("research", "import", path, "--input", payload(path, "papers.yaml", plan)))
    metadata = {
        "records": [
            {
                "paper_id": "paper.base",
                "provider": "crossref-fixture",
                "provider_id": "10.5555/base",
                "title": "Base Evidence",
                "authors": ["A. Researcher"],
                "year": 2022,
                "doi": "10.5555/base",
                "venue": "Evidence Journal",
                "references": [],
            },
            {
                "paper_id": "paper.new",
                "provider": "crossref-fixture",
                "provider_id": "10.5555/new",
                "title": "New Evidence",
                "authors": ["B. Researcher"],
                "year": 2024,
                "doi": "10.5555/new",
                "references": [{"doi": "10.5555/base"}, {"doi": "10.5555/external"}],
            },
        ]
    }
    reconciled = output(
        invoke(
            "research",
            "reconcile-metadata",
            path,
            "--input",
            payload(path, "metadata.yaml", metadata),
            "--expected-research-revision",
            imported["research_revision"],
        )
    )
    assert reconciled["result"]["verified_paper_ids"] == ["paper.base", "paper.new"]
    assert reconciled["result"]["citation_edges_added"] == [{"from": "paper.new", "to": "paper.base"}]
    papers = output(invoke("research", "list", path))
    newer = next(item for item in papers if item["id"] == "paper.new")
    assert newer["cites"] == ["paper.base"]
    assert newer["external_citations"] == ["10.5555/external"]
    assert output(invoke("research", "validate", path))["ok"] is True


def test_research_rejects_full_text_storage() -> None:
    path = workspace("copyright")
    revision = import_plan(path)["research_revision"]
    activated = mutate(path, "activate", revision, "paper.field.survey")
    unsafe = payload(path, "unsafe-note.yaml", {"full_text": "Do not store complete papers here."})
    blocked = invoke(
        "research",
        "note",
        path,
        "paper.field.survey",
        "--input",
        unsafe,
        "--expected-research-revision",
        activated["research_revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "Do not store full paper text" in blocked.stderr


def test_research_discloses_provisionally_skipped_concept_assumptions() -> None:
    path = workspace("flexible-skip")
    course = output(invoke("import-plan", path, "--input", CALCULUS_PLAN, "--expected-revision", 0))
    plan = yaml.safe_load(PLAN.read_text(encoding="utf-8"))
    plan["papers"][0]["concept_atom_ids"] = ["calculus.derivative.definition"]
    imported = output(
        invoke(
            "research",
            "import",
            path,
            "--input",
            payload(path, "research-with-concepts.yaml", plan),
            "--expected-research-revision",
            0,
        )
    )
    output(
        invoke(
            "skip",
            path,
            "calculus.derivative.definition",
            "--mode",
            "provisional",
            "--reason-code",
            "already_mastered",
            "--confirmed",
            "--expected-revision",
            course["revision"],
        )
    )
    candidate = output(invoke("research", "next", path))[0]
    assert candidate["knowledge_gap_atom_ids"] == []
    assert candidate["provisional_knowledge_atom_ids"] == ["calculus.derivative.definition"]
    activated = output(
        invoke(
            "research",
            "activate",
            path,
            "paper.field.survey",
            "--expected-research-revision",
            imported["research_revision"],
        )
    )
    assert activated["result"]["provisional_knowledge_atom_ids"] == ["calculus.derivative.definition"]


def test_research_attach_source_binds_shared_document_ir_without_copying_full_text() -> None:
    path = workspace("document-ir")
    imported = import_plan(path)
    output(invoke("rag", "init", path))
    source = {
        "sources": [
            {
                "id": "survey-source",
                "title": "Field survey source",
                "authority": "peer_reviewed",
                "text": "# Reliability survey\nThe survey compares verification mechanisms across agent systems.",
            }
        ]
    }
    output(invoke("rag", "ingest", path, "--input", payload(path, "survey-source.yaml", source)))

    attached = output(
        invoke(
            "research",
            "attach-source",
            path,
            "paper.field.survey",
            "--source-id",
            "survey-source",
            "--expected-research-revision",
            imported["research_revision"],
        )
    )
    assert attached["result"]["copied_full_text"] is False
    assert attached["result"]["source_revision"] == 1
    paper = next(
        item for item in output(invoke("research", "list", path))
        if item["id"] == "paper.field.survey"
    )
    assert paper["locator"] == "document-ir:survey-source@r1"
    assert paper["metadata_verification"]["checks"]["document_ir"]["block_count"] >= 2
    assert "full_text" not in paper
    assert output(invoke("research", "validate", path))["ok"] is True


def test_research_protocol_discovery_screening_snowball_and_refresh_close_the_audit_loop() -> None:
    path = workspace("discovery-loop")
    protocol = {
        "research_question": "Which mechanisms improve reliability?",
        "scope": "Inspectable empirical studies.",
        "languages": ["English"], "date_from": "2020-01-01", "date_to": None,
        "literature_types": ["journal-article", "proceedings-article"],
        "inclusion_criteria": ["Contains inspectable evidence"],
        "exclusion_criteria": ["Product announcement only"],
        "target_outcomes": ["unsupported action rate"],
        "search_limits": ["Provider result cap"],
    }
    revision = output(
        invoke("research", "set-protocol", path, "--input", payload(path, "protocol.yaml", protocol))
    )["research_revision"]
    discovery = output(
        invoke(
            "research", "discover", path, "--provider", "harness", "--query", "reliable research agents",
            "--limit", 10, "--expected-research-revision", revision,
        )
    )
    action_id = discovery["result"]["action"]["action_id"]
    assert discovery["result"]["submission_required"] is True
    submission = {
        "action_id": action_id, "retrieved_at": "2026-08-16T10:00:00+08:00", "complete": True, "failure": None,
        "records": [
            {
                "provider_id": "W100", "title": "Inspectable Agent Reliability", "authors": ["A. Author"],
                "year": 2025, "venue": "AgentConf", "doi": "10.5555/reliable.1", "url": "https://doi.org/10.5555/reliable.1",
                "references": [], "integrity_status": "not_retracted", "integrity_locator": "https://openalex.org/W100",
            }
        ],
    }
    submitted = output(
        invoke(
            "research", "submit-discovery", path, "--input", payload(path, "submission.yaml", submission),
            "--expected-research-revision", discovery["research_revision"],
        )
    )
    paper_id = submitted["result"]["imported_paper_ids"][0]
    proposed_screen = {
        "decisions": [{
            "paper_id": paper_id, "decision": "include", "matched_criteria": ["Contains inspectable evidence"],
            "exclusion_criterion": None, "reason": "Model proposes inclusion.", "confirmed": False,
        }]
    }
    screened = output(
        invoke(
            "research", "screen", path, "--input", payload(path, "screen-proposed.yaml", proposed_screen),
            "--expected-research-revision", submitted["research_revision"],
        )
    )
    assert screened["result"]["needs_review_paper_ids"] == [paper_id]
    proposed_screen["decisions"][0].update({"reason": "Human-confirmed protocol match.", "confirmed": True})
    included = output(
        invoke(
            "research", "screen", path, "--input", payload(path, "screen-confirmed.yaml", proposed_screen),
            "--expected-research-revision", screened["research_revision"],
        )
    )
    assert included["result"]["included_paper_ids"] == [paper_id]

    snowball = output(
        invoke(
            "research", "snowball", path, paper_id, "--direction", "backward", "--provider", "harness",
            "--stopping-rule", "Stop after one depth or ten candidates.",
            "--expected-research-revision", included["research_revision"],
        )
    )
    snowball_submission = {
        "action_id": snowball["result"]["action"]["action_id"], "retrieved_at": "2026-08-16T11:00:00+08:00",
        "complete": True, "failure": None,
        "records": [{
            "provider_id": "W050", "title": "Earlier Reliability Evidence", "authors": ["B. Author"],
            "year": 2022, "venue": "EvidenceConf", "doi": "10.5555/reliable.0", "url": "https://doi.org/10.5555/reliable.0",
            "references": [], "integrity_status": "not_retracted", "integrity_locator": "https://openalex.org/W050",
        }],
    }
    expanded = output(
        invoke(
            "research", "submit-discovery", path, "--input", payload(path, "snowball-submission.yaml", snowball_submission),
            "--expected-research-revision", snowball["research_revision"],
        )
    )
    cited_id = expanded["result"]["imported_paper_ids"][0]
    papers = output(invoke("research", "list", path))
    assert cited_id in next(item for item in papers if item["id"] == paper_id)["cites"]

    refresh = output(
        invoke(
            "research", "refresh", path, "--provider", "harness",
            "--expected-research-revision", expanded["research_revision"],
        )
    )
    refresh_submission = copy.deepcopy(submission)
    refresh_submission["action_id"] = refresh["result"]["action"]["action_id"]
    refresh_submission["records"][0]["integrity_status"] = "retracted"
    refreshed = output(
        invoke(
            "research", "submit-discovery", path, "--input", payload(path, "refresh-submission.yaml", refresh_submission),
            "--expected-research-revision", refresh["research_revision"],
        )
    )
    papers = output(invoke("research", "list", path))
    seed = next(item for item in papers if item["id"] == paper_id)
    assert seed["status"] == "queued"
    assert seed["integrity"]["status"] == "retracted"
    blocked = invoke(
        "research", "activate", path, paper_id,
        "--expected-research-revision", refreshed["research_revision"], check=False,
    )
    assert blocked.returncode == 2
    assert "integrity alert" in blocked.stderr
    status = output(invoke("research", "status", path))
    assert status["screening"]["claim"].startswith("PRISMA-style")
    assert status["discovery"]["coverage_claim"] == "bounded_provider_results_not_exhaustive"


def test_research_completion_fails_without_claim_level_locator() -> None:
    path = workspace("locator-guard")
    revision = import_plan(path)["research_revision"]
    activated = mutate(path, "activate", revision, "paper.field.survey")
    note_path = critical_note(path, "paper.field.survey")
    note = yaml.safe_load(note_path.read_text(encoding="utf-8"))
    note["claims"][0].pop("evidence_locator")
    missing = payload(path, "missing-locator.yaml", note)
    recorded = mutate(path, "note", activated["research_revision"], "paper.field.survey", "--input", missing)
    blocked = invoke(
        "research", "complete", path, "paper.field.survey",
        "--expected-research-revision", recorded["research_revision"], check=False,
    )
    assert blocked.returncode == 2
    assert "completion requires a sentence, table, figure, equation, or block locator" in blocked.stderr


def test_research_rejects_quantitative_claim_from_unreviewed_ocr_block() -> None:
    path = workspace("quantitative-ocr-guard")
    revision = import_plan(path)["research_revision"]
    activated = mutate(path, "activate", revision, "paper.field.survey")
    source = {
        "sources": [
            {
                "id": "scan-source",
                "title": "Scanned results",
                "authority": "peer_reviewed",
                "text": "# Results\nThe treatment effect is 12 percent.",
            }
        ]
    }
    output(invoke("rag", "init", path))
    output(invoke("rag", "ingest", path, "--input", payload(path, "scan-source.yaml", source)))
    document = output(invoke("rag", "document-ir", path, "scan-source"))
    block = next(item for item in document["blocks"] if item["kind"] == "paragraph")
    ir_path = path / ".atomlearn" / "rag" / "document-ir" / "scan-source.r1.json"
    stored = json.loads(ir_path.read_text(encoding="utf-8"))
    stored_block = next(item for item in stored["blocks"] if item["block_id"] == block["block_id"])
    stored_block.update({"kind": "figure", "extraction_method": "harness_vision", "review_status": "proposed", "numeric_status": "proposal"})
    ir_path.write_text(json.dumps(stored), encoding="utf-8")
    note_path = critical_note(path, "paper.field.survey")
    note = yaml.safe_load(note_path.read_text(encoding="utf-8"))
    note["claims"][0]["statement"] = "The treatment improves outcomes by 12%."
    note["claims"][0]["evidence_locator"] = {
        "locator": "page 1 OCR",
        "kind": "block",
        "extraction_method": "document_ir",
        "confidence": 0.7,
        "source_id": "scan-source",
        "source_revision": 1,
        "block_ids": [block["block_id"]],
    }
    blocked = invoke(
        "research", "note", path, "paper.field.survey", "--input", payload(path, "ocr-note.yaml", note),
        "--expected-research-revision", activated["research_revision"], check=False,
    )
    assert blocked.returncode == 2
    assert "unsupported quantitative claim requires figure/table review or abstention" in blocked.stderr


def test_research_direct_provider_contracts_normalize_cache_and_citation_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    path = workspace("provider-contract")
    engine = ResearchEngine.load(str(path))
    calls: list[str] = []

    crossref = {
        "message": {
            "items": [{
                "DOI": "10.1000/crossref",
                "title": ["Crossref Evidence"],
                "author": [{"given": "A", "family": "Author"}],
                "published-print": {"date-parts": [[2022]]},
                "container-title": ["Evidence Journal"],
                "URL": "https://doi.org/10.1000/crossref",
                "abstract": "<jats:p>Provider abstract.</jats:p>",
                "license": [{"URL": "https://example.org/license"}],
                "reference": [{"DOI": "10.1000/reference", "article-title": "Earlier Evidence"}],
            }],
        }
    }
    openalex = {
        "results": [{
            "id": "https://openalex.org/W1",
            "display_name": "OpenAlex Evidence",
            "authorships": [{"author": {"display_name": "B Researcher"}}],
            "publication_year": 2021,
            "primary_location": {"source": {"display_name": "Open Journal"}, "landing_page_url": "https://example.org/open"},
            "doi": "https://doi.org/10.1000/openalex",
            "abstract_inverted_index": {"Reliable": [1], "evidence": [0]},
            "open_access": {"license": "cc-by"},
            "referenced_works": ["https://openalex.org/W0"],
            "is_retracted": False,
        }],
        "meta": {"next_cursor": None},
    }
    pubmed_search = {"esearchresult": {"idlist": ["12345"]}}
    pubmed_summary = {"result": {"12345": {
        "title": "PubMed Evidence", "authors": [{"name": "C Clinician"}], "pubdate": "2020 Jan",
        "fulljournalname": "Medicine Journal", "articleids": [{"idtype": "doi", "value": "10.1000/pubmed"}],
    }}}
    semantic = {"data": [{
        "paperId": "S1", "title": "Semantic Evidence", "authors": [{"name": "D Scientist"}], "year": 2023,
        "venue": "Semantic Journal", "url": "https://semanticscholar.org/paper/S1",
        "externalIds": {"DOI": "10.1000/semantic", "ArXiv": "2301.00001"}, "abstract": "Semantic abstract",
        "references": [{"paperId": "S0", "title": "Prior Evidence", "externalIds": {"DOI": "10.1000/prior"}}],
        "citations": [{"paperId": "S2", "title": "Later Evidence", "externalIds": {"DOI": "10.1000/later"}}],
    }], "next": None}
    arxiv = """<feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\">
      <entry><id>http://arxiv.org/abs/2401.00001</id><title>arXiv Evidence</title>
      <summary>ArXiv abstract.</summary><published>2024-01-01T00:00:00Z</published>
      <author><name>E Researcher</name></author><category term=\"cs.AI\"/>
      <arxiv:doi>10.1000/arxiv</arxiv:doi><arxiv:license>http://creativecommons.org/licenses/by/4.0/</arxiv:license>
      </entry></feed>"""

    def fake_json(url: str, user_agent: str, timeout: float) -> dict:
        calls.append(url)
        if "crossref.org" in url:
            return crossref
        if "openalex.org" in url:
            return openalex
        if "esearch.fcgi" in url:
            return pubmed_search
        if "esummary.fcgi" in url:
            return pubmed_summary
        if "semanticscholar.org" in url:
            return semantic
        raise AssertionError(f"unexpected provider URL: {url}")

    monkeypatch.setattr("research.fetch_json", fake_json)
    monkeypatch.setattr("research.fetch_text", lambda url, user_agent, timeout: arxiv)
    queries = {
        "crossref": "crossref evidence",
        "openalex": "openalex evidence",
        "pubmed": "pubmed evidence",
        "semantic_scholar": "semantic evidence",
        "arxiv": "arxiv evidence",
    }
    results = {}
    for provider, query in queries.items():
        results[provider] = engine.discover(query, provider, 5, None, None, 5.0, "")
        contract = results[provider]["provider_contract"]
        assert contract["cache_hit"] is False
        assert contract["field_completeness"]["title"] == 1
        assert results[provider]["imported_paper_ids"]

    cached = engine.discover("semantic evidence", "semantic_scholar", 5, None, None, 5.0, "")
    assert cached["provider_contract"]["cache_hit"] is True
    assert any("semanticscholar.org" in url for url in calls)
    semantic_paper = next(
        item for item in engine.papers.values() if item.get("title") == "Semantic Evidence"
    )
    assert any(item["direction"] == "forward" for item in semantic_paper["citation_provenance"])
    assert any(item["direction"] == "backward" for item in semantic_paper["citation_provenance"])
    assert len(engine.state["provider_cache"]) == 5
    semantic_calls_before_refresh = sum("semanticscholar.org" in url for url in calls)
    refreshed = engine.refresh("semantic_scholar", 5)
    assert refreshed["provider_contract"]["cache_hit"] is False
    assert sum("semanticscholar.org" in url for url in calls) > semantic_calls_before_refresh
    engine.commit("test.provider_contract")


def test_research_provider_failure_is_typed_and_not_treated_as_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    path = workspace("provider-failure")
    engine = ResearchEngine.load(str(path))

    def unavailable(url: str, user_agent: str, timeout: float) -> dict:
        raise ProviderError("rate_limited", "fixture rate limit", retryable=True)

    monkeypatch.setattr("research.fetch_json", unavailable)
    failed = engine.discover("rate limited field", "semantic_scholar", 5, None, None, 5.0, "")
    assert failed["action_status"] == "failed"
    assert failed["failure"]["code"] == "rate_limited"
    assert failed["retryable"] is True
    assert engine.state["provider_failures"][-1]["provider"] == "semantic_scholar"
    assert engine.state["provider_failures"][-1]["operation"] == "discovery"
    engine.commit("test.provider_failure")


def test_research_metadata_keeps_provider_disagreements_and_citation_provenance() -> None:
    path = workspace("provider-disagreement")
    import_plan(path)
    engine = ResearchEngine.load(str(path))
    result = engine.reconcile_metadata({"records": [
        {
            "paper_id": "paper.field.survey", "provider": "crossref", "provider_id": "10.1000/survey",
            "title": "A synthetic survey of reliable research agents", "authors": ["Example Author"], "year": 2024,
            "doi": "10.1000/survey", "venue": "Journal A", "references": [{"title": "A synthetic tool-verification method"}],
            "retrieved_at": "2026-08-19T10:00:00+08:00",
        },
        {
            "paper_id": "paper.field.survey", "provider": "openalex", "provider_id": "W-SURVEY",
            "title": "A synthetic survey of reliable research agents", "authors": ["Example Author"], "year": 2025,
            "doi": "10.1000/survey", "venue": "Journal B", "references": [],
            "retrieved_at": "2026-08-19T10:01:00+08:00",
        },
    ]})
    assert result["verified_paper_ids"] == ["paper.field.survey"]
    assert result["conflicts"]
    paper = engine.papers["paper.field.survey"]
    assert {item["provider"] for item in paper["provider_observations"]} == {"crossref", "openalex"}
    assert {item["field"] for item in paper["provider_disagreements"]} >= {"year", "venue"}
    assert any(item["target_paper_id"] == "paper.method.alpha" for item in paper["citation_provenance"])
    assert paper["cites"] == ["paper.method.alpha"]
    engine.commit("test.provider_disagreement")


def test_research_synthesis_exposes_claim_matrix_effect_direction_and_boundaries() -> None:
    path = workspace("claim-matrix")
    revision = import_plan(path)["research_revision"]
    for paper_id, relation in [
        ("paper.field.survey", None),
        ("paper.method.alpha", {"paper_id": "paper.field.survey", "type": "supports", "note": "Replicates the same direction."}),
        ("paper.replication.alpha", {"paper_id": "paper.method.alpha", "type": "contradicts", "note": "Fails under a broader setting."}),
    ]:
        activated = mutate(path, "activate", revision, paper_id)
        note_path = critical_note(path, paper_id, relation=relation)
        note = yaml.safe_load(note_path.read_text(encoding="utf-8"))
        note["claims"][0]["effect_direction"] = "positive" if paper_id != "paper.replication.alpha" else "null"
        note["claims"][0]["facets"]["intervention_exposure"] = [
            "verification mechanism" if paper_id != "paper.replication.alpha" else "alternative mechanism"
        ]
        recorded = mutate(path, "note", activated["research_revision"], paper_id, "--input", payload(path, f"matrix-note-{paper_id}.yaml", note))
        revision = mutate(path, "complete", recorded["research_revision"], paper_id)["research_revision"]
    synthesized = mutate(path, "synthesize", revision)
    theme = synthesized["result"]["evidence_synthesis"]["themes"][0]
    assert {row["effect_direction"] for row in theme["evidence_matrix"]} == {"positive", "null"}
    assert theme["supporting_claim_ids"]
    assert theme["opposing_claim_ids"]
    assert any(item["facet"] == "intervention_exposure" for item in theme["conditional_boundaries"])
    matrix = (path / "LITERATURE_MATRIX.md").read_text(encoding="utf-8")
    assert "Provider Disagreements" in matrix
    assert "Matrix `" in matrix


def test_research_direct_snowball_executes_supported_graph_and_types_unsupported_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    path = workspace("direct-snowball")
    import_plan(path)
    engine = ResearchEngine.load(str(path))
    semantic = {
        "paperId": "S-SEED",
        "title": "A synthetic survey of reliable research agents",
        "authors": [{"name": "Example Author"}],
        "year": 2024,
        "venue": "Survey Journal",
        "externalIds": {"DOI": "10.1000/survey"},
        "references": [{"paperId": "S-REF", "title": "A cited paper", "externalIds": {"DOI": "10.1000/cited"}}],
        "citations": [{"paperId": "S-CITE", "title": "A citing paper", "externalIds": {"DOI": "10.1000/citing"}}],
    }
    monkeypatch.setattr("research.fetch_json", lambda url, user_agent, timeout: semantic)
    backward = engine.snowball("paper.field.survey", "backward", "semantic_scholar", 1, 5, "one depth", 5.0, "")
    assert backward["submission_required"] is False
    assert backward["provider_contract"]["cache_hit"] is False
    assert backward["imported_paper_ids"]
    forward = engine.snowball("paper.field.survey", "forward", "semantic_scholar", 1, 5, "one depth", 5.0, "")
    assert forward["submission_required"] is False
    unsupported = engine.snowball("paper.field.survey", "forward", "pubmed", 1, 5, "one depth", 5.0, "")
    assert unsupported["action_status"] == "failed"
    assert unsupported["failure"]["code"] == "citation_graph_unavailable"
    engine.commit("test.direct_snowball")
