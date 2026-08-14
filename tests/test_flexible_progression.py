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


def payload(path: Path, name: str, data: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def workspace(label: str) -> tuple[Path, int]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"flex-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"flex.{label}",
            "--title",
            f"Flexible {label}",
            "--goal",
            "Adapt the route without inventing mastery",
        )
    )
    imported = output(invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0))
    return path, imported["revision"]


def skip(path: Path, atom_id: str, revision: int, *, mode: str = "provisional") -> dict:
    args: list[object] = [
        "skip",
        path,
        atom_id,
        "--mode",
        mode,
        "--reason-code",
        "already_mastered" if mode == "provisional" else "time_constraint",
        "--expected-revision",
        revision,
    ]
    if mode == "provisional":
        args.append("--confirmed")
    return output(invoke(*args))


def test_diagnostic_is_default_and_does_not_mutate_state() -> None:
    path, revision = workspace("diagnostic")
    guidance = output(
        invoke(
            "skip",
            path,
            "calculus.limit.approach",
            "--expected-revision",
            revision,
        )
    )
    assert guidance["mode"] == "diagnostic"
    assert guidance["mutated"] is False
    assert guidance["atom"]["required_dimensions"] == ["explain", "discriminate"]
    assert guidance["can_activate_for_diagnostic"] is True
    state = output(invoke("status", path, "--json"))
    assert state["course"]["revision"] == revision
    assert state["active_flexibility_decisions"] == []


def test_provisional_skip_requires_confirmation_unlocks_and_is_reversible() -> None:
    path, revision = workspace("provisional")
    rejected = invoke(
        "skip",
        path,
        "calculus.limit.approach",
        "--mode",
        "provisional",
        "--reason-code",
        "too_easy",
        "--expected-revision",
        revision,
        check=False,
    )
    assert rejected.returncode == 2
    assert "does not prove mastery" in rejected.stderr
    assert output(invoke("status", path, "--json"))["course"]["revision"] == revision

    first = skip(path, "calculus.limit.approach", revision)
    assert first["status"] == "skipped"
    assert first["mastery_claimed"] is False
    second = skip(path, "calculus.rate.average", first["revision"])
    state = output(invoke("status", path, "--json"))
    assert state["counts"]["skipped"] == 2
    assert {item["atom_id"] for item in state["active_flexibility_decisions"]} == {
        "calculus.limit.approach",
        "calculus.rate.average",
    }
    assert state["next_candidates"][0]["id"] == "calculus.derivative.definition"
    assert yaml.safe_load((path / ".atomlearn" / "evidence.yaml").read_text(encoding="utf-8"))["items"] == []

    restored = output(
        invoke(
            "unskip",
            path,
            "calculus.rate.average",
            "--expected-revision",
            second["revision"],
        )
    )
    assert restored["status"] == "available"
    atoms = {
        item["id"]: item
        for item in output(invoke("status", path, "--json"))["next_candidates"]
    }
    assert "calculus.rate.average" in atoms
    derivative = yaml.safe_load(
        (path / ".atomlearn" / "atoms" / "calculus.derivative.definition.yaml").read_text(encoding="utf-8")
    )
    assert derivative["status"] == "locked"
    assert output(invoke("validate", path))["ok"] is True


def test_defer_removes_an_active_atom_without_unlocking_successors() -> None:
    path, revision = workspace("defer")
    activated = output(
        invoke("activate", path, "calculus.limit.approach", "--expected-revision", revision)
    )
    deferred = skip(path, "calculus.limit.approach", activated["revision"], mode="defer")
    state = output(invoke("status", path, "--json"))
    assert deferred["status"] == "deferred"
    assert state["session"]["active_atom_id"] is None
    assert state["counts"]["deferred"] == 1
    assert {item["id"] for item in state["next_candidates"]} == {"calculus.rate.average"}
    restored = output(
        invoke("unskip", path, "calculus.limit.approach", "--expected-revision", deferred["revision"])
    )
    assert restored["status"] == "available"


def test_strict_mastery_policy_disallows_provisional_bypass() -> None:
    path, revision = workspace("strict")
    policy_plan = payload(path, "strict.yaml", {"course": {"settings": {"skip_policy": "strict_mastery"}}})
    updated = output(
        invoke("import-plan", path, "--input", policy_plan, "--expected-revision", revision)
    )
    blocked = invoke(
        "skip",
        path,
        "calculus.limit.approach",
        "--mode",
        "provisional",
        "--reason-code",
        "already_mastered",
        "--confirmed",
        "--expected-revision",
        updated["revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "strict_mastery" in blocked.stderr
    deferred = skip(path, "calculus.limit.approach", updated["revision"], mode="defer")
    assert deferred["status"] == "deferred"


def test_backtracking_reopens_a_skipped_prerequisite() -> None:
    path, revision = workspace("backtrack")
    revision = skip(path, "calculus.limit.approach", revision)["revision"]
    revision = skip(path, "calculus.rate.average", revision)["revision"]
    activated = output(
        invoke("activate", path, "calculus.derivative.definition", "--expected-revision", revision)
    )
    question = payload(
        path,
        "question.yaml",
        {
            "text": "I cannot explain why average rate becomes instantaneous rate.",
            "classification": "blocking_prerequisite",
            "related_atom_id": "calculus.rate.average",
            "rationale": "The provisional assumption failed during downstream work.",
            "priority": "high",
        },
    )
    recorded = output(
        invoke(
            "record-question",
            path,
            "--input",
            question,
            "--expected-revision",
            activated["revision"],
        )
    )
    reopened = output(
        invoke(
            "backtrack",
            path,
            "--to",
            "calculus.rate.average",
            "--question-id",
            recorded["question_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    assert reopened["active_atom_id"] == "calculus.rate.average"
    state = output(invoke("status", path, "--json"))
    assert state["active_atom"]["status"] == "active"
    assert {item["atom_id"] for item in state["active_flexibility_decisions"]} == {
        "calculus.limit.approach"
    }
    assert output(invoke("validate", path))["ok"] is True


def test_course_completion_discloses_provisional_skips() -> None:
    path, revision = workspace("completion")
    for atom_id in [
        "calculus.limit.approach",
        "calculus.rate.average",
        "calculus.derivative.definition",
        "calculus.derivative.compute-square",
        "calculus.derivative.geometric",
    ]:
        revision = skip(path, atom_id, revision)["revision"]
    state = output(invoke("status", path, "--json"))
    assert state["course"]["status"] == "completed_with_skips"
    assert state["counts"] == {"skipped": 5}
    progress = (path / "PROGRESS.md").read_text(encoding="utf-8")
    assert "Mastered with Evidence: 0 / 5" in progress
    assert "Provisionally skipped: 5" in progress
    assert output(invoke("validate", path))["ok"] is True
