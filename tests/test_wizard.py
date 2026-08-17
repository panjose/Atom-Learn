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


def submission(name: str, action: dict, result: dict) -> Path:
    return payload(
        name,
        {
            "kind": "atomlearn.workflow-submission",
            "schema_version": 1,
            "action_id": action["action_id"],
            "workflow_revision": action["workflow_revision"],
            "idempotency_key": action["idempotency_key"],
            "result": result,
        },
    )


def workspace(name: str) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    return RUN_ROOT / f"wizard-{name}-{uuid.uuid4().hex}"


def test_short_topic_start_creates_all_runtime_layers_and_search_tasks() -> None:
    path = workspace("topic")
    result = output(invoke("start", path, "--topic", "causal inference", "--json"))

    assert result["status"] == "web_search_required"
    assert result["workflow_action"]["action"] == "web_search"
    assert result["workflow_action"]["tool_contract"]["capability"] == "harness.web_search"
    intake = yaml.safe_load((path / ".atomlearn" / "intake.yaml").read_text(encoding="utf-8"))
    assert len(intake["assumptions"]) == 3
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
    started = output(invoke("start", path, "--input", request, "--json"))
    assert started["status"] == "coverage_judgment_required"
    assert started["workflow_action"]["action"] == "judge_coverage"

    replay = output(invoke("start", path, "--json"))
    assert replay["wizard_revision"] == started["wizard_revision"]
    assert replay["workflow_action"] == started["workflow_action"]

    candidate = started["coverage_requirements"][0]["candidate_chunk_ids"][0]
    planned = output(
        invoke(
            "start",
            path,
            "--submission",
            submission(
                "coverage",
                started["workflow_action"],
                {
                    "verdicts": [
                        {
                            "requirement_id": "scope.goal",
                            "status": "supported",
                            "evidence_chunk_ids": [candidate],
                            "rationale": "The supplied notes directly explain the requested limit concept.",
                        }
                    ]
                },
            ),
            "--json",
        )
    )
    assert planned["status"] == "course_plan_required"
    assert planned["course_plan_task"]["source_ids"] == ["calculus-notes"]

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
    proposed = output(
        invoke(
            "start",
            path,
            "--submission",
            submission("plan", planned["workflow_action"], {"course_plan": plan}),
            "--json",
        )
    )
    assert proposed["status"] == "phase_confirmation_required"
    assert proposed["plan_preview"]["added"] == 1
    assert not any((path / ".atomlearn" / "atoms").glob("*.yaml"))

    stale = invoke(
        "start",
        path,
        "--submission",
        submission("stale-plan", planned["workflow_action"], {"course_plan": plan}),
        "--json",
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale workflow submission" in stale.stderr

    confirmed = output(
        invoke(
            "start",
            path,
            "--submission",
            submission("confirm", proposed["workflow_action"], {"confirmed": True}),
            "--json",
        )
    )
    assert confirmed["status"] == "first_atom_confirmation_required"
    assert confirmed["plan"]["added"] == 1
    assert output(invoke("status", path))["session"]["active_atom_id"] is None

    resumed = output(
        invoke(
            "start",
            path,
            "--submission",
            submission("activate", confirmed["workflow_action"], {"confirmed": True}),
            "--json",
        )
    )
    assert resumed["status"] == "complete"
    assert resumed["active_atom_id"] == "calculus.limit"
    assert output(invoke("validate", path))["ok"] is True
    assert yaml.safe_load((path / ".atomlearn" / "intake.yaml").read_text(encoding="utf-8"))["status"] == "planned"


def test_start_clarification_submission_reenters_evidence_discovery() -> None:
    path = workspace("clarify")
    request = payload(
        "clarify",
        {
            "topic": "alignment",
            "ambiguities": ["Does alignment mean AI alignment or sequence alignment?"],
        },
    )
    started = output(invoke("start", path, "--input", request, "--json"))
    assert started["status"] == "clarification_required"
    assert started["workflow_action"]["action"] == "clarify_goal"

    clarified = output(
        invoke(
            "start",
            path,
            "--submission",
            submission(
                "clarified",
                started["workflow_action"],
                {
                    "goal": "Understand technical approaches to AI alignment",
                    "desired_outcome": "research",
                    "target_depth": "advanced",
                },
            ),
            "--json",
        )
    )
    assert clarified["status"] == "web_search_required"
    assert clarified["workflow_action"]["action"] == "web_search"


def test_start_default_console_is_bilingual_and_requires_no_yaml_editing() -> None:
    path = workspace("console")
    result = invoke("start", path, "--topic", "linear algebra")

    assert "Find authoritative evidence" in result.stdout
    assert "查找权威证据" in result.stdout
    assert "Status: web_search_required" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


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


def test_closed_corpus_reports_gaps_without_web_search() -> None:
    path = workspace("closed-corpus")
    request = payload(
        "closed-corpus",
        {
            "title": "Closed notes",
            "goal": "Understand limits and measure theory",
            "sources": [
                {
                    "id": "limit-notes",
                    "title": "Limit notes",
                    "type": "notes",
                    "authority": "user",
                    "text": "# Limits\nA limit describes an approached value.",
                }
            ],
            "corpus_policy": {
                "role": "full",
                "expansion": "closed_corpus",
                "user_confirmed": True,
            },
        },
    )
    started = output(invoke("start", path, "--input", request, "--json"))
    assert started["status"] == "coverage_judgment_required"
    gap = output(
        invoke(
            "start",
            path,
            "--submission",
            submission(
                "closed-gap",
                started["workflow_action"],
                {
                    "verdicts": [
                        {
                            "requirement_id": "scope.goal",
                            "status": "weak",
                            "evidence_chunk_ids": [],
                            "rationale": "The notes cover limits but not measure theory.",
                        }
                    ]
                },
            ),
            "--json",
        )
    )
    assert gap["status"] == "corpus_gap_reported"
    assert gap["web_search_tasks"] == []
    assert gap["workflow_action"]["action"] == "clarify_goal"
    assert gap["workflow_action"]["tool_contract"]["required_result_fields"][-1] == "corpus_policy"
    rejected_web = payload(
        "closed-web",
        {
            "web_evidence": {
                "sources": [
                    {
                        "id": "forbidden-web",
                        "title": "Forbidden Web source",
                        "url": "https://example.org/forbidden",
                        "retrieved_at": "2026-08-17T00:00:00+00:00",
                        "query": "measure theory",
                        "authority": "official",
                        "passages": [{"locator": "intro", "text": "Measure theory material."}],
                    }
                ]
            },
            "verdicts": [],
        },
    )
    rejected = invoke("start", path, "--input", rejected_web, "--json", check=False)
    assert rejected.returncode == 2
    assert "closed_corpus forbids Web evidence" in rejected.stderr
    registry = yaml.safe_load((path / ".atomlearn" / "rag" / "sources.yaml").read_text(encoding="utf-8"))
    assert "forbidden-web" not in {item["id"] for item in registry["sources"]}


def test_mixed_input_preserves_every_goal_contract_anchor() -> None:
    path = workspace("mixed")
    request = payload(
        "mixed",
        {
            "title": "Mixed calculus",
            "goal": "Build a rigorous calculus review",
            "sources": [
                {
                    "id": "calculus-notes",
                    "title": "Calculus notes",
                    "type": "notes",
                    "authority": "textbook",
                    "text": "# Limits\nLimits support derivatives and continuity.",
                }
            ],
            "outline": [{"id": "outline.limits", "title": "Limits"}],
            "topic_terms": ["epsilon-delta proofs"],
            "mandatory_anchors": [
                {"id": "goal.proofs", "query": "Construct rigorous epsilon-delta proofs"}
            ],
        },
    )
    result = output(invoke("start", path, "--input", request, "--json"))
    assert result["status"] == "coverage_judgment_required"
    intake = yaml.safe_load((path / ".atomlearn" / "intake.yaml").read_text(encoding="utf-8"))
    assert intake["mode"] == "sources"
    assert intake["input_inventory"] == {"has_sources": True, "has_outline": True, "has_topic": True}
    assert {item["id"] for item in intake["goal_contract"]["mandatory_anchors"]} == {
        "outline.limits",
        "topic.1",
        "goal.proofs",
        "scope.goal",
    }
    assert set(yaml.safe_load((path / ".atomlearn" / "start.yaml").read_text(encoding="utf-8"))["source_ids"]) == {
        "calculus-notes",
        "user-outline",
    }
