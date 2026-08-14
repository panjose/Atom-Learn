from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
RUN_ROOT = ROOT / ".test-workspaces"


def invoke(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(item) for item in args)],
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


def write_payload(path: Path, name: str, value: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def base_plan() -> dict:
    return {
        "atoms": [
            {
                "id": "topic.core",
                "title": "Core idea",
                "module": "Core",
                "objective": "Explain and apply the core idea",
                "prerequisites": [],
            },
            {
                "id": "topic.next",
                "title": "Later application",
                "module": "Core",
                "objective": "Apply the core idea in a later setting",
                "prerequisites": ["topic.core"],
            },
        ]
    }


def workspace(label: str) -> tuple[Path, int]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"route-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"route.{label}",
            "--title",
            f"Routing {label}",
            "--goal",
            "Keep related concepts understandable without losing the current Atom",
        )
    )
    plan = write_payload(path, "plan.yaml", base_plan())
    imported = output(invoke("import-plan", path, "--input", plan, "--expected-revision", 0))
    activated = output(
        invoke("activate", path, "topic.core", "--expected-revision", imported["revision"])
    )
    return path, activated["revision"]


def route(path: Path, revision: int, payload: dict, action: str = "preview", confirmed: bool = False) -> dict:
    route_path = write_payload(path, f"route-{uuid.uuid4().hex}.yaml", payload)
    args: list[object] = [
        "route-concept",
        path,
        "--input",
        route_path,
        "--action",
        action,
        "--expected-revision",
        revision,
    ]
    if confirmed:
        args.append("--confirmed")
    return output(invoke(*args))


def master(path: Path, atom_id: str, revision: int) -> int:
    atom = yaml.safe_load((path / ".atomlearn" / "atoms" / f"{atom_id}.yaml").read_text(encoding="utf-8"))
    evidence = write_payload(
        path,
        f"evidence-{uuid.uuid4().hex}.yaml",
        {
            "atom_id": atom_id,
            "kind": "mastery_check",
            "prompt": "Explain and apply this Atom.",
            "response_summary": "The learner demonstrated the objective.",
            "scores": {dimension: 0.9 for dimension in atom["mastery"]["required_dimensions"]},
            "feedback": "All dimensions passed.",
            "rationale": "Observable performance met the rubric.",
        },
    )
    recorded = output(
        invoke("record-evidence", path, "--input", evidence, "--expected-revision", revision)
    )
    assessed = output(
        invoke(
            "assess",
            path,
            atom_id,
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    assert assessed["result"] == "mastered"
    return assessed["revision"]


def test_preview_returns_relation_card_without_mutation() -> None:
    path, revision = workspace("preview")
    result = route(
        path,
        revision,
        {
            "text": "What is the later application?",
            "concept": "later application",
            "relation": "scheduled_successor",
            "rationale": "It already has a mapped Atom after the current one.",
            "related_atom_id": "topic.next",
        },
    )
    assert result["applied"] is False
    assert result["mutated"] is False
    assert result["revision"] == revision
    assert result["card"]["label"] == "Scheduled later"
    assert result["card"]["destination"]["atom_id"] == "topic.next"
    assert result["card"]["recommended_action"] == "park"
    state = output(invoke("status", path, "--json"))
    assert state["course"]["revision"] == revision
    assert state["session"]["active_atom_id"] == "topic.core"


def test_scheduled_and_inside_routes_keep_the_active_atom() -> None:
    path, revision = workspace("nonblocking")
    scheduled = route(
        path,
        revision,
        {
            "text": "Can we cover the later application now?",
            "concept": "later application",
            "relation": "scheduled_successor",
            "rationale": "The destination is already scheduled after the current Atom.",
            "related_atom_id": "topic.next",
        },
        "park",
    )
    assert scheduled["active_atom_id"] == "topic.core"
    inside = route(
        path,
        scheduled["revision"],
        {
            "text": "What does core mean in this sentence?",
            "concept": "core",
            "relation": "inside_current",
            "rationale": "This is a boundary clarification inside the current objective.",
        },
        "explain_now",
    )
    assert inside["active_atom_id"] == "topic.core"
    questions = yaml.safe_load((path / ".atomlearn" / "questions.yaml").read_text(encoding="utf-8"))["items"]
    assert questions[0]["status"] == "parked"
    assert questions[0]["routing"]["relation"] == "scheduled_successor"
    assert questions[1]["status"] == "open"
    assert questions[1]["routing"]["relation"] == "inside_current"


def test_required_prerequisite_is_inserted_then_resumes_parent() -> None:
    path, revision = workspace("prerequisite")
    payload = {
        "text": "I do not understand the notation used here.",
        "concept": "notation prerequisite",
        "relation": "required_prerequisite",
        "rationale": "The learner cannot interpret the current Atom without it.",
        "new_atom": {
            "id": "topic.notation",
            "title": "Required notation",
            "objective": "Interpret the notation used by the core idea",
        },
    }
    rejected = invoke(
        "route-concept",
        path,
        "--input",
        write_payload(path, "unconfirmed.yaml", payload),
        "--action",
        "learn_prerequisite",
        "--expected-revision",
        revision,
        check=False,
    )
    assert rejected.returncode == 2
    assert "--confirmed" in rejected.stderr
    applied = route(path, revision, payload, "learn_prerequisite", confirmed=True)
    assert applied["created_atom_id"] == "topic.notation"
    assert applied["active_atom_id"] == "topic.notation"
    core = yaml.safe_load((path / ".atomlearn" / "atoms" / "topic.core.yaml").read_text(encoding="utf-8"))
    assert core["prerequisites"] == ["topic.notation"]
    revision = master(path, "topic.notation", applied["revision"])
    resumed = output(invoke("resume", path, "--expected-revision", revision))
    assert resumed["active_atom_id"] == "topic.core"
    question = yaml.safe_load((path / ".atomlearn" / "questions.yaml").read_text(encoding="utf-8"))["items"][0]
    assert question["status"] == "resolved"
    assert output(invoke("validate", path))["ok"] is True


def test_downstream_atom_cannot_be_misclassified_as_prerequisite() -> None:
    path, revision = workspace("cycle")
    payload = write_payload(
        path,
        "cycle.yaml",
        {
            "text": "Do I need the later application first?",
            "concept": "later application",
            "relation": "required_prerequisite",
            "rationale": "Deliberately incorrect classification for cycle protection.",
            "related_atom_id": "topic.next",
        },
    )
    result = invoke(
        "route-concept",
        path,
        "--input",
        payload,
        "--expected-revision",
        revision,
        check=False,
    )
    assert result.returncode == 2
    assert "would create a cycle" in result.stderr


def test_optional_branch_is_visible_but_does_not_block_main_path() -> None:
    path, revision = workspace("optional")
    added = route(
        path,
        revision,
        {
            "text": "Can I also learn the historical motivation?",
            "concept": "historical motivation",
            "relation": "optional_extension",
            "rationale": "It gives context but is not required for the current mastery objective.",
            "new_atom": {
                "id": "topic.history",
                "title": "Historical motivation",
                "objective": "Relate the historical motivation to the core idea",
            },
        },
        "add_optional_branch",
        confirmed=True,
    )
    assert added["active_atom_id"] == "topic.core"
    branch = yaml.safe_load((path / ".atomlearn" / "atoms" / "topic.history.yaml").read_text(encoding="utf-8"))
    assert branch["optional"] is True
    assert branch["branch"]["anchor_atom_id"] == "topic.core"
    graph = yaml.safe_load((path / ".atomlearn" / "graph.yaml").read_text(encoding="utf-8"))
    assert graph["branches"] == [
        {"anchor": "topic.core", "atom": "topic.history", "kind": "optional_extension"}
    ]
    learning_map = (path / "LEARNING_MAP.md").read_text(encoding="utf-8")
    assert "topic.history" in learning_map and "[optional branch]" in learning_map

    revision = master(path, "topic.core", added["revision"])
    suggestions = output(invoke("suggest-next", path))
    assert [item["id"] for item in suggestions][:2] == ["topic.next", "topic.history"]
    activated = output(invoke("activate", path, "topic.next", "--expected-revision", revision))
    revision = master(path, "topic.next", activated["revision"])
    state = output(invoke("status", path, "--json"))
    assert state["course"]["status"] == "completed"
    assert state["counts"]["available"] == 1
    output(invoke("lineage", "init", path))
    structure = output(invoke("lineage", "overview", path, "--lens", "structure"))["structure"]
    assert structure["optional_branches"] == [
        {
            "anchor_atom_id": "topic.core",
            "branch_atom_id": "topic.history",
            "kind": "optional_extension",
            "status": "available",
        }
    ]
    assert "topic.history" not in structure["main_learning_spine"]
    assert output(invoke("validate", path))["ok"] is True


def test_expanded_child_can_insert_and_resume_an_external_prerequisite() -> None:
    path, revision = workspace("expanded-prerequisite")
    expansion = write_payload(
        path,
        "expand.yaml",
        {
            "reason_code": "learner_requested_detail",
            "note": "Teach the requested detail one child at a time.",
            "child_atoms": [
                {
                    "id": "topic.core.why",
                    "title": "Why the core idea is needed",
                    "objective": "Explain the motivating problem",
                },
                {
                    "id": "topic.core.how",
                    "title": "How the core idea works",
                    "objective": "Apply the core mechanism",
                },
            ],
        },
    )
    expanded = output(
        invoke(
            "expand",
            path,
            "topic.core",
            "--plan",
            expansion,
            "--confirmed",
            "--expected-revision",
            revision,
        )
    )
    assert expanded["active_atom_id"] == "topic.core.why"
    routed = route(
        path,
        expanded["revision"],
        {
            "text": "I cannot read the notation in this motivation.",
            "concept": "motivation notation",
            "relation": "required_prerequisite",
            "rationale": "The active child cannot be understood without the notation.",
            "new_atom": {
                "id": "topic.core.notation",
                "title": "Motivation notation",
                "objective": "Interpret the notation used in the motivation",
            },
        },
        "learn_prerequisite",
        confirmed=True,
    )
    revision = master(path, "topic.core.notation", routed["revision"])
    resumed = output(invoke("resume", path, "--expected-revision", revision))
    assert resumed["active_atom_id"] == "topic.core.why"
    assert output(invoke("validate", path))["ok"] is True
