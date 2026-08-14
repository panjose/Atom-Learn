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


def payload(name: str, data: dict) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"wizard-{name}-{uuid.uuid4().hex}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def workspace(name: str) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    return RUN_ROOT / f"wizard-{name}-{uuid.uuid4().hex}"


def test_short_topic_start_creates_all_runtime_layers_and_search_tasks() -> None:
    path = workspace("topic")
    result = output(invoke("start", path, "--topic", "causal inference"))

    assert result["status"] == "web_search_required"
    assert result["web_search_tasks"]
    assert {item["requirement_id"] for item in result["web_search_tasks"]} == {"topic.1", "scope.goal"}
    assert (path / ".atomlearn" / "course.yaml").is_file()
    assert (path / ".atomlearn" / "intake.yaml").is_file()
    assert (path / ".atomlearn" / "rag" / "state.yaml").is_file()
    assert (path / ".atomlearn" / "start.yaml").is_file()
    assert (path / "START.md").is_file()


def test_source_start_accepts_one_payload_then_resumes_with_the_plan() -> None:
    path = workspace("sources")
    request = payload(
        "sources",
        {
            "title": "Compact calculus",
            "goal": "Understand limits",
            "sources": [
                {
                    "id": "calculus-notes",
                    "title": "Calculus notes",
                    "type": "notes",
                    "authority": "textbook",
                    "text": "# Limits\nA limit describes the value approached by a function.",
                }
            ],
        },
    )
    started = output(invoke("start", path, "--input", request))
    assert started["status"] == "course_plan_required"
    assert started["course_plan_task"]["source_ids"] == ["calculus-notes"]

    plan = {
        "sources": [
            {
                "id": "calculus-notes",
                "title": "Calculus notes",
                "type": "notes",
                "location": "inline:calculus-notes",
            }
        ],
        "atoms": [
            {
                "id": "calculus.limit",
                "title": "Limits",
                "objective": "Explain what a function approaches near a point.",
                "prerequisites": [],
                "sources": [{"source_id": "calculus-notes", "locator": "Limits"}],
            }
        ],
    }
    resumed = output(invoke("start", path, "--input", payload("plan", {"course_plan": plan})))
    assert resumed["status"] == "complete"
    assert resumed["plan"]["added"] == 1
    assert output(invoke("validate", path))["ok"] is True
    assert yaml.safe_load((path / ".atomlearn" / "intake.yaml").read_text(encoding="utf-8"))["status"] == "planned"


def test_start_payload_is_checked_against_the_public_json_schema() -> None:
    path = workspace("invalid")
    bad = payload("invalid", {"topic": "", "unexpected": True})
    result = invoke("start", path, "--input", bad, check=False)

    assert result.returncode == 2
    assert "start.schema.json" in result.stderr
    assert "unexpected" in result.stderr
    assert not (path / ".atomlearn").exists()


def test_start_can_print_its_machine_readable_schema() -> None:
    result = invoke("start", "unused", "--print-schema")
    schema = json.loads(result.stdout)
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["title"] == "AtomLearn unified start payload"
