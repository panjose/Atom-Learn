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
                "title": "Core mechanism",
                "module": "Core",
                "objective": "Explain and apply the complete mechanism",
                "prerequisites": [],
                "mastery": {
                    "required_dimensions": ["explain", "apply"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
            {
                "id": "topic.transfer",
                "title": "Transfer",
                "module": "Core",
                "objective": "Transfer the mechanism to a new case",
                "prerequisites": ["topic.core"],
                "mastery": {
                    "required_dimensions": ["transfer"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
        ]
    }


def expansion_plan(prefix: str = "topic.core") -> dict:
    return {
        "reason_code": "learner_requested_detail",
        "note": "The learner asked for a detailed explanation without a multi-concept lecture.",
        "child_atoms": [
            {
                "id": f"{prefix}.why",
                "title": "Why the mechanism is needed",
                "objective": "Explain the problem that motivates the mechanism",
                "mastery": {
                    "required_dimensions": ["explain"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
            {
                "id": f"{prefix}.how",
                "title": "How the mechanism works",
                "objective": "Apply the mechanism one step at a time",
                "mastery": {
                    "required_dimensions": ["apply"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
        ],
    }


def workspace(label: str) -> tuple[Path, int]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"detail-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"detail.{label}",
            "--title",
            f"Detailed {label}",
            "--goal",
            "Preserve one-Atom-at-a-time teaching when more depth is requested",
        )
    )
    plan = write_payload(path, "base-plan.yaml", base_plan())
    imported = output(invoke("import-plan", path, "--input", plan, "--expected-revision", 0))
    return path, imported["revision"]


def activate(path: Path, atom_id: str, revision: int) -> int:
    return output(invoke("activate", path, atom_id, "--expected-revision", revision))["revision"]


def expand(path: Path, atom_id: str, revision: int, plan: dict | None = None) -> dict:
    plan_path = write_payload(path, f"expand-{uuid.uuid4().hex}.yaml", plan or expansion_plan(atom_id))
    return output(
        invoke(
            "expand",
            path,
            atom_id,
            "--plan",
            plan_path,
            "--confirmed",
            "--expected-revision",
            revision,
        )
    )


def master_active(path: Path, revision: int) -> tuple[int, str]:
    state = output(invoke("status", path, "--json"))
    atom = state["active_atom"]
    assert atom is not None
    dimensions = atom["mastery"]["required_dimensions"]
    evidence = write_payload(
        path,
        f"evidence-{uuid.uuid4().hex}.yaml",
        {
            "atom_id": atom["id"],
            "kind": "mastery_check",
            "measurement_kind": "immediate_mastery",
            "measurement_item_id": f"{atom['id']}.fixture-v2",
            "episode_id": f"episode-{uuid.uuid4().hex}",
            "assessment": {
                "method": "human",
                "grader_id": "atomlearn/human-adjudication-v1",
                "rubric_version": "human-v1",
                "calibration_set_version": None,
                "independent": True,
                "answer_hash": "sha256:" + "c" * 64,
            },
            "prompt": "Demonstrate the current Atom without relying on later branch material.",
            "response_summary": "The learner demonstrated the declared objective independently.",
            "scores": {dimension: 0.9 for dimension in dimensions},
            "feedback": "The observable response met every required dimension.",
            "rationale": "The scores are tied to the Atom-specific check.",
        },
    )
    recorded = output(
        invoke("record-evidence", path, "--input", evidence, "--expected-revision", revision)
    )
    assessed = output(
        invoke(
            "assess",
            path,
            atom["id"],
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    assert assessed["result"] == "mastered"
    return assessed["revision"], atom["id"]


def test_expand_preview_does_not_mutate() -> None:
    path, revision = workspace("preview")
    plan = write_payload(path, "expand.yaml", expansion_plan())
    preview = output(
        invoke(
            "expand",
            path,
            "topic.core",
            "--plan",
            plan,
            "--expected-revision",
            revision,
        )
    )
    assert preview["applied"] is False
    assert output(invoke("status", path, "--json"))["course"]["revision"] == revision


def test_detailed_request_becomes_ordered_children_then_parent_integration() -> None:
    path, revision = workspace("flow")
    revision = activate(path, "topic.core", revision)
    expanded = expand(path, "topic.core", revision)
    assert expanded["created_atom_ids"] == ["topic.core.why", "topic.core.how"]
    assert expanded["active_atom_id"] == "topic.core.why"

    atoms = {
        atom_id: yaml.safe_load((path / ".atomlearn" / "atoms" / f"{atom_id}.yaml").read_text(encoding="utf-8"))
        for atom_id in ["topic.core", "topic.core.why", "topic.core.how"]
    }
    assert atoms["topic.core"]["prerequisites"] == ["topic.core.how"]
    assert atoms["topic.core.why"]["prerequisites"] == []
    assert atoms["topic.core.how"]["prerequisites"] == ["topic.core.why"]
    assert atoms["topic.core.why"]["parent_atom_id"] == "topic.core"
    graph = yaml.safe_load((path / ".atomlearn" / "graph.yaml").read_text(encoding="utf-8"))
    assert graph["expansions"] == [
        {"parent": "topic.core", "children": ["topic.core.why", "topic.core.how"]}
    ]

    revision, mastered = master_active(path, expanded["revision"])
    assert mastered == "topic.core.why"
    state = output(invoke("status", path, "--json"))
    assert state["active_atom"]["id"] == "topic.core.how"
    assert "Do not preview later children" in state["session"]["next_action"]

    revision, mastered = master_active(path, revision)
    assert mastered == "topic.core.how"
    state = output(invoke("status", path, "--json"))
    assert state["active_atom"]["id"] == "topic.core"
    assert state["session"]["phase"] == "integrating"
    assert state["detailed_expansions"][0]["integration_status"] == "ready"

    deferred = output(
        invoke(
            "skip",
            path,
            "topic.core",
            "--mode",
            "defer",
            "--reason-code",
            "time_constraint",
            "--expected-revision",
            revision,
        )
    )
    restored = output(
        invoke(
            "unskip",
            path,
            "topic.core",
            "--expected-revision",
            deferred["revision"],
        )
    )
    activated = output(
        invoke(
            "activate",
            path,
            "topic.core",
            "--expected-revision",
            restored["revision"],
        )
    )
    revision = activated["revision"]
    assert output(invoke("status", path, "--json"))["session"]["phase"] == "integrating"

    blocked = invoke(
        "skip",
        path,
        "topic.core",
        "--mode",
        "provisional",
        "--reason-code",
        "already_mastered",
        "--confirmed",
        "--expected-revision",
        revision,
        check=False,
    )
    assert blocked.returncode == 2
    assert "integration check" in blocked.stderr

    revision, mastered = master_active(path, revision)
    assert mastered == "topic.core"
    state = output(invoke("status", path, "--json"))
    assert state["session"]["expansion_stack"] == []
    assert state["detailed_expansions"][0]["integration_status"] == "completed"
    assert state["next_candidates"][0]["id"] == "topic.transfer"
    assert "  - ↳" in (path / "LEARNING_MAP.md").read_text(encoding="utf-8")
    assert output(invoke("validate", path))["ok"] is True


def test_nested_detail_request_preserves_single_active_atom() -> None:
    path, revision = workspace("nested")
    revision = activate(path, "topic.core", revision)
    outer = expand(path, "topic.core", revision)
    inner_plan = {
        "reason_code": "learner_requested_detail",
        "child_atoms": [
            {
                "id": "topic.core.why.problem",
                "title": "The motivating problem",
                "objective": "Identify the exact problem",
                "mastery": {
                    "required_dimensions": ["explain"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
            {
                "id": "topic.core.why.constraint",
                "title": "The governing constraint",
                "objective": "Discriminate the governing constraint",
                "mastery": {
                    "required_dimensions": ["discriminate"],
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                },
            },
        ],
    }
    inner = expand(path, "topic.core.why", outer["revision"], inner_plan)
    assert inner["active_atom_id"] == "topic.core.why.problem"
    assert len(output(invoke("status", path, "--json"))["session"]["expansion_stack"]) == 2

    revision = inner["revision"]
    expected_active = [
        "topic.core.why.constraint",
        "topic.core.why",
        "topic.core.how",
        "topic.core",
    ]
    for expected in expected_active:
        revision, _ = master_active(path, revision)
        state = output(invoke("status", path, "--json"))
        assert state["active_atom"]["id"] == expected
        assert state["counts"]["active"] == 1
    revision, mastered = master_active(path, revision)
    assert mastered == "topic.core"
    state = output(invoke("status", path, "--json"))
    assert state["session"]["expansion_stack"] == []
    assert {item["integration_status"] for item in state["detailed_expansions"]} == {"completed"}


def test_expanded_children_require_evidence_but_can_be_deferred() -> None:
    path, revision = workspace("flex")
    revision = activate(path, "topic.core", revision)
    expanded = expand(path, "topic.core", revision)
    blocked = invoke(
        "skip",
        path,
        "topic.core.why",
        "--mode",
        "provisional",
        "--reason-code",
        "already_mastered",
        "--confirmed",
        "--expected-revision",
        expanded["revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "require mastered Evidence" in blocked.stderr

    deferred = output(
        invoke(
            "skip",
            path,
            "topic.core.why",
            "--mode",
            "defer",
            "--reason-code",
            "time_constraint",
            "--expected-revision",
            expanded["revision"],
        )
    )
    assert output(invoke("status", path, "--json"))["session"]["expansion_stack"] == []
    restored = output(
        invoke(
            "unskip",
            path,
            "topic.core.why",
            "--expected-revision",
            deferred["revision"],
        )
    )
    activated = output(
        invoke(
            "activate",
            path,
            "topic.core.why",
            "--expected-revision",
            restored["revision"],
        )
    )
    state = output(invoke("status", path, "--json"))
    assert state["active_atom"]["id"] == "topic.core.why"
    assert len(state["session"]["expansion_stack"]) == 1
    assert activated["revision"] == restored["revision"] + 1


def test_import_plan_preserves_an_existing_expansion_tree() -> None:
    path, revision = workspace("import")
    revision = activate(path, "topic.core", revision)
    expanded = expand(path, "topic.core", revision)
    plan = write_payload(path, "reimport.yaml", base_plan())
    imported = output(
        invoke(
            "import-plan",
            path,
            "--input",
            plan,
            "--expected-revision",
            expanded["revision"],
        )
    )
    parent = yaml.safe_load(
        (path / ".atomlearn" / "atoms" / "topic.core.yaml").read_text(encoding="utf-8")
    )
    assert parent["prerequisites"] == ["topic.core.how"]
    assert parent["expansion"]["child_atom_ids"] == ["topic.core.why", "topic.core.how"]
    assert output(invoke("validate", path))["revision"] == imported["revision"]


def test_lineage_exposes_detailed_expansion_tree() -> None:
    path, revision = workspace("lineage")
    revision = activate(path, "topic.core", revision)
    expand(path, "topic.core", revision)
    output(invoke("lineage", "init", path))
    overview = output(invoke("lineage", "overview", path, "--lens", "structure"))
    assert overview["structure"]["detailed_expansions"] == [
        {
            "parent_atom_id": "topic.core",
            "child_atom_ids": ["topic.core.why", "topic.core.how"],
            "completed": False,
        }
    ]
    rendered = (path / "KNOWLEDGE_LINEAGE.md").read_text(encoding="utf-8")
    assert "## Detailed Expansion Trees" in rendered
    assert "`topic.core.why`" in rendered
