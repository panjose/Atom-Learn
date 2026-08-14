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


def test_full_sources_intake_is_ready_and_traceable_to_imported_atoms() -> None:
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
    assert state["status"] == "ready_to_plan"
    assert state["guidance"]["ready_to_plan"] is True
    assert (path / "INTAKE.md").is_file()
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
    assert guidance["ready_to_plan"] is True
    assert any("Atom boundaries" in item for item in guidance["actions"])
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
    assert "Authoritative discovery sources" in blocked.stderr

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
    assert updated["result"]["ready_to_plan"] is True
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
