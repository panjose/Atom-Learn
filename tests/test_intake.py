from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
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


def output(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def payload(path: Path, name: str, data: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def workspace(label: str) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"intake-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"intake.{label}",
            "--title",
            f"Intake {label}",
            "--goal",
            "Build a source-grounded learning path",
        )
    )
    return path


def initialize(path: Path, data: dict) -> dict:
    intake = payload(path, f"intake-{uuid.uuid4().hex}.yaml", data)
    return output(invoke("intake", "init", path, "--input", intake))


def import_course(path: Path) -> dict:
    return output(invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0))


def complete(path: Path, revision: int) -> dict:
    return output(
        invoke(
            "intake",
            "complete",
            path,
            "--expected-intake-revision",
            revision,
        )
    )


def canonical_coverage(path: Path, verdicts: list[dict]) -> Path:
    request = yaml.safe_load(invoke("rag", "requirements", path, "--context", "intake").stdout)
    request["verdicts"] = verdicts
    return payload(path, f"coverage-{uuid.uuid4().hex}.yaml", request)


def test_full_sources_intake_requires_goal_coverage_and_is_traceable_to_imported_atoms() -> None:
    path = workspace("sources")
    state = initialize(
        path,
        {
            "request_summary": "Learn from the complete supplied calculus textbook.",
            "goal": "Understand derivatives from first principles.",
            "desired_outcome": "working_knowledge",
            "target_depth": "working",
            "source_materials": [
                {
                    "id": "calculus-notes",
                    "title": "Calculus source",
                    "type": "pdf",
                    "location": "C:/materials/calculus.pdf",
                    "version": "fixture",
                }
            ],
        },
    )
    assert state["mode"] == "sources"
    assert state["status"] == "discovering"
    assert state["guidance"]["ready_to_plan"] is False
    assert (path / "INTAKE.md").is_file()
    output(invoke("rag", "init", path))
    source_file = payload(
        path,
        "source-content.yaml",
        {
            "sources": [
                {
                    "id": "calculus-notes",
                    "title": "Calculus source",
                    "authority": "textbook",
                    "text": "# Derivatives\nA derivative is the limit of a difference quotient and represents instantaneous change.",
                }
            ]
        },
    )
    output(invoke("rag", "ingest", path, "--input", source_file))
    coverage_file = canonical_coverage(
        path,
        [
            {
                "requirement_id": "scope.goal",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00001"],
                "rationale": "The supplied source directly covers the requested derivative foundation.",
            }
        ],
    )
    assert output(invoke("rag", "coverage", path, "--input", coverage_file))["gate"] == "pass"
    assert output(invoke("intake", "guidance", path))["ready_to_plan"] is True
    import_course(path)
    completed = complete(path, 0)
    assert completed["intake_revision"] == 1
    assert completed["result"]["atoms"] == 5
    assert output(invoke("validate", path))["ok"] is True


def test_outline_intake_preserves_coverage_anchor_without_copying_structure() -> None:
    path = workspace("outline")
    state = initialize(
        path,
        {
            "request_summary": "Use my calculus outline as the starting structure.",
            "goal": "Build a prerequisite-aware calculus path.",
            "desired_outcome": "exam",
            "target_depth": "advanced",
            "outline_source_id": "calculus-notes",
            "outline_items": [
                {"id": "outline.limits", "title": "Limits", "parent_id": None, "notes": ""},
                {
                    "id": "outline.derivatives",
                    "title": "Derivatives",
                    "parent_id": "outline.limits",
                    "notes": "Includes the formal definition.",
                },
            ],
        },
    )
    assert state["mode"] == "outline"
    guidance = output(invoke("intake", "guidance", path))
    assert guidance["ready_to_plan"] is False
    assert any("RAG coverage" in item for item in guidance["blockers"])
    assert any("Atom boundaries" in item for item in guidance["actions"])
    output(invoke("rag", "init", path))
    source_file = payload(
        path,
        "outline-source.yaml",
        {
            "sources": [
                {
                    "id": "calculus-notes",
                    "title": "Calculus outline support",
                    "authority": "textbook",
                    "text": "# Limits\nA limit describes approach behavior.\n\n# Derivatives\nA derivative is a limit of difference quotients.",
                }
            ]
        },
    )
    output(invoke("rag", "ingest", path, "--input", source_file))
    coverage_file = canonical_coverage(
        path,
        [
            {
                "requirement_id": "outline.limits",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00001"],
                "rationale": "The textbook passage directly defines the limit concept.",
            },
            {
                "requirement_id": "outline.derivatives",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00002"],
                "rationale": "The textbook passage grounds derivatives in limits.",
            },
            {
                "requirement_id": "scope.goal",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00001"],
                "rationale": "The source supports the prerequisite-aware calculus goal.",
            },
        ],
    )
    assert output(invoke("rag", "coverage", path, "--input", coverage_file))["gate"] == "pass"
    assert output(invoke("intake", "guidance", path))["ready_to_plan"] is True
    import_course(path)
    assert complete(path, 0)["result"]["mode"] == "outline"


def test_topic_only_intake_requires_discovery_then_becomes_plannable() -> None:
    path = workspace("topic")
    state = initialize(
        path,
        {
            "request_summary": "I want to learn derivatives.",
            "goal": "Understand what derivatives mean and how to use them.",
            "desired_outcome": "orientation",
            "target_depth": "overview",
            "topic_terms": ["derivative"],
            "ambiguities": ["The intended application domain is not specified."],
            "assumptions": ["Begin with single-variable calculus."],
        },
    )
    assert state["mode"] == "topic"
    assert state["status"] == "discovering"
    blocked = invoke(
        "intake",
        "complete",
        path,
        "--expected-intake-revision",
        0,
        check=False,
    )
    assert blocked.returncode == 2
    assert "RAG coverage has not been evaluated" in blocked.stderr

    update_file = payload(
        path,
        "topic-discovery.yaml",
        {
            "discovery_sources": [
                {
                    "id": "calculus-notes",
                    "title": "Authoritative calculus source",
                    "type": "book",
                    "location": "fixture:calculus-mini",
                    "version": "fixture",
                }
            ]
        },
    )
    updated = output(
        invoke(
            "intake",
            "update",
            path,
            "--input",
            update_file,
            "--expected-intake-revision",
            0,
        )
    )
    assert updated["intake_revision"] == 1
    assert updated["result"]["ready_to_plan"] is False
    stale = invoke(
        "intake",
        "update",
        path,
        "--input",
        update_file,
        "--expected-intake-revision",
        0,
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale intake revision" in stale.stderr
    output(invoke("rag", "init", path))
    web_file = payload(
        path,
        "topic-web.yaml",
        {
            "sources": [
                {
                    "id": "calculus-notes",
                    "title": "Authoritative calculus source",
                    "url": "https://example.edu/calculus",
                    "retrieved_at": "2025-01-01T00:00:00+00:00",
                    "query": "derivative foundations",
                    "authority": "official",
                    "passages": [{"locator": "derivatives", "text": "The derivative is the limit of a difference quotient."}],
                },
                {
                    "id": "calculus-second",
                    "title": "Calculus technical reference",
                    "url": "https://example.org/calculus-reference",
                    "retrieved_at": "2025-01-01T00:00:00+00:00",
                    "query": "derivative applications",
                    "authority": "peer_reviewed",
                    "passages": [{"locator": "rates", "text": "Derivatives model instantaneous rates of change."}],
                },
            ]
        },
    )
    output(invoke("rag", "ingest-web", path, "--input", web_file))
    coverage_file = canonical_coverage(
        path,
        [
            {
                "requirement_id": "topic.1",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00001"],
                "rationale": "The official source provides the core definition.",
            },
            {
                "requirement_id": "scope.goal",
                "status": "supported",
                "evidence_chunk_ids": ["calculus-notes.r1.c00001", "calculus-second.r1.c00001"],
                "rationale": "Two authoritative sources cover meaning and use.",
            },
        ],
    )
    assert output(invoke("rag", "coverage", path, "--input", coverage_file))["gate"] == "pass"
    assert output(invoke("intake", "guidance", path))["ready_to_plan"] is True
    import_course(path)
    completed = complete(path, 1)
    assert completed["result"]["mode"] == "topic"
    assert output(invoke("intake", "status", path))["status"] == "planned"


def test_outline_cycles_are_rejected() -> None:
    path = workspace("invalid-outline")
    intake = payload(
        path,
        "cycle.yaml",
        {
            "mode": "outline",
            "request_summary": "Use this invalid outline.",
            "goal": "The cycle must be detected.",
            "outline_items": [
                {"id": "outline.a", "title": "A", "parent_id": "outline.b"},
                {"id": "outline.b", "title": "B", "parent_id": "outline.a"},
            ],
        },
    )
    blocked = invoke("intake", "init", path, "--input", intake, check=False)
    assert blocked.returncode == 2
    assert "outline hierarchy cycle" in blocked.stderr


def test_legacy_sources_state_is_upgraded_in_memory_without_rewriting() -> None:
    path = workspace("legacy-sources")
    initialize(
        path,
        {
            "mode": "sources",
            "request_summary": "Legacy source intake.",
            "goal": "Learn the supplied material.",
            "source_materials": [
                {
                    "id": "legacy-notes",
                    "title": "Legacy notes",
                    "type": "notes",
                    "location": "inline:legacy-notes",
                }
            ],
        },
    )
    state_path = path / ".atomlearn" / "intake.yaml"
    legacy = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    for field in [
        "mandatory_anchors",
        "input_inventory",
        "corpus_policy",
        "goal_contract_revision",
        "goal_contract",
        "planned_intake_revision",
        "planned_goal_contract_revision",
    ]:
        legacy.pop(field, None)
    legacy["status"] = "ready_to_plan"
    state_path.write_text(yaml.safe_dump(legacy, allow_unicode=True, sort_keys=False), encoding="utf-8")
    before = state_path.read_bytes()

    status = output(invoke("intake", "status", path))

    assert status["status"] == "discovering"
    assert status["corpus_policy"]["expansion"] == "correct_gaps"
    assert state_path.read_bytes() == before
