from __future__ import annotations

import copy
import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
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
