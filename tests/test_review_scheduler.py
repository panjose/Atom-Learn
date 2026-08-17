from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atom-learn" / "scripts"
CLI = SCRIPTS / "atomlearn.py"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
sys.path.insert(0, str(SCRIPTS))

from review_scheduler import (  # noqa: E402
    ReviewSchedulerError,
    apply_qualified_event,
    benchmark_current,
    configure,
    default_benchmark,
    default_policy,
    initialize_review_state,
    pilot_report,
    run_benchmark,
    schedule_choice,
)


def invoke(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def output(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def write_yaml(path: Path, value: dict) -> Path:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def new_workspace(tmp_path: Path) -> tuple[Path, int]:
    workspace = tmp_path / f"review-{uuid.uuid4().hex}"
    output(invoke("init", workspace, "--course-id", "test.review", "--title", "Review", "--goal", "Test memory"))
    imported = output(invoke("import-plan", workspace, "--input", PLAN, "--expected-revision", 0))
    return workspace, imported["revision"]


def evidence(atom_id: str, *, kind: str, score: float, hints: int = 0, retrieval: str = "active_recall") -> dict:
    payload = {
        "atom_id": atom_id,
        "kind": kind,
        "measurement_kind": "delayed_retention" if kind == "review" else "immediate_mastery",
        "measurement_item_id": f"{atom_id}.{uuid.uuid4().hex}",
        "episode_id": f"episode-{uuid.uuid4().hex}",
        "assessment": {
            "method": "human",
            "grader_id": "atomlearn/human-adjudication-v1",
            "rubric_version": "human-v1",
            "calibration_set_version": None,
            "independent": True,
            "answer_hash": "sha256:" + "b" * 64,
        },
        "prompt": "Recall, explain, and apply the Atom without consulting notes.",
        "response_summary": "The learner completed the requested active-retrieval response.",
        "scores": {"explain": score, "apply": score, "discriminate": score},
        "feedback": "Scored against the current Atom rubric.",
        "rationale": "Independent observable performance is used for the review outcome.",
    }
    if kind == "review":
        payload["review_observation"] = {
            "retrieval_mode": retrieval,
            "hint_count": hints,
            "delayed": True,
            "response_time_seconds": 90,
        }
    return payload


def record_and_assess(workspace: Path, revision: int, atom_id: str, payload: dict, at: str) -> tuple[int, str]:
    path = write_yaml(workspace / f"evidence-{uuid.uuid4().hex}.yaml", payload)
    recorded = output(invoke("record-evidence", workspace, "--input", path, "--expected-revision", revision))
    assessed = output(
        invoke(
            "assess", workspace, atom_id, "--evidence-id", recorded["evidence_id"],
            "--now", at, "--expected-revision", recorded["revision"],
        )
    )
    return assessed["revision"], assessed["result"]


def test_adapter_ignores_response_time_and_failure_shortens_stability() -> None:
    first = {
        "atom_id": "a", "assessed_at": "2026-01-01T00:00:00+00:00", "correctness": 0.9,
        "hint_count": 0, "recalled": True,
    }
    fast, _ = apply_qualified_event(None, {**first, "response_time_bucket": "under_30s"}, 0.9)
    slow, _ = apply_qualified_event(None, {**first, "response_time_bucket": "over_300s"}, 0.9)
    assert fast == slow
    failed = {
        "atom_id": "a", "assessed_at": "2026-01-10T00:00:00+00:00", "correctness": 0.2,
        "hint_count": 2, "recalled": False,
    }
    after, prediction = apply_qualified_event(fast, failed, 0.9)
    assert prediction is not None
    assert after["stability_days"] < fast["stability_days"]
    assert after["difficulty"] > fast["difficulty"]


def test_active_mode_requires_benchmark_and_opt_in() -> None:
    reviews = {"items": []}
    initialize_review_state(reviews)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        configure(reviews, {"mode": "adaptive-active", "active_opt_in": True}, at)
    except ReviewSchedulerError as exc:
        assert "benchmark" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("active scheduling unexpectedly bypassed its benchmark gate")
    report = run_benchmark(reviews, at)
    assert report["status"] == "passed"
    configured = configure(reviews, {"mode": "adaptive-active", "active_opt_in": True}, at)
    assert configured["policy"]["mode"] == "adaptive-active"
    event = {
        "atom_id": "a", "assessed_at": "2026-01-01T00:00:00+00:00", "correctness": 0.9,
        "hint_count": 0, "recalled": True,
    }
    reviews["memory"]["a"], _ = apply_qualified_event(None, event, 0.9)
    active = schedule_choice(reviews, "a", at, 1)
    assert active is not None
    assert active["effective_scheduler"] == "adaptive"
    assert active["interval_days"] == reviews["memory"]["a"]["suggested_interval_days"]
    configure(
        reviews,
        {"objective": "exam", "exam_target_date": "2026-01-10", "final_review_days": 3},
        at,
    )
    reviews["memory"]["a"]["suggested_interval_days"] = 20
    bounded = schedule_choice(reviews, "a", at, 1)
    assert bounded is not None
    assert bounded["adaptive_due_at"] == "2026-01-07T00:00:00+00:00"
    reviews["benchmark"]["metrics"]["brier_score"] = 0.0
    assert benchmark_current(reviews) is False


def test_shadow_review_updates_memory_without_replacing_fixed_due_date(tmp_path: Path) -> None:
    workspace, revision = new_workspace(tmp_path)
    benchmark = output(invoke("review", "benchmark", workspace, "--now", "2026-01-01T00:00:00+00:00", "--expected-revision", revision))
    policy = write_yaml(
        workspace / "review-policy.yaml",
        {"mode": "adaptive-shadow", "desired_retention": 0.9, "objective": "long_term", "exam_target_date": None,
         "final_review_days": 7, "active_opt_in": False},
    )
    configured = output(invoke("review", "configure", workspace, "--input", policy, "--expected-revision", benchmark["revision"]))
    atom_id = "calculus.limit.approach"
    activated = output(invoke("activate", workspace, atom_id, "--expected-revision", configured["revision"]))
    revision, result = record_and_assess(
        workspace, activated["revision"], atom_id, evidence(atom_id, kind="mastery_check", score=0.9),
        "2026-01-01T00:00:00+00:00",
    )
    assert result == "mastered"
    refreshed = output(invoke("refresh-reviews", workspace, "--now", "2026-01-02T00:00:00+00:00", "--expected-revision", revision))
    activated = output(invoke("activate", workspace, atom_id, "--expected-revision", refreshed["revision"]))
    revision, result = record_and_assess(
        workspace, activated["revision"], atom_id, evidence(atom_id, kind="review", score=0.9, hints=4),
        "2026-01-02T00:00:00+00:00",
    )
    assert result == "mastered"
    reviews = yaml.safe_load((workspace / ".atomlearn" / "reviews.yaml").read_text(encoding="utf-8"))
    pending = next(item for item in reviews["items"] if item["status"] == "pending")
    assert pending["scheduler_mode"] == "adaptive-shadow"
    assert pending["effective_scheduler"] == "fixed"
    assert pending["interval_days"] == 3
    assert pending["adaptive_interval_days"] < pending["fixed_interval_days"]
    assert reviews["memory"][atom_id]["qualified_event_count"] == 1
    status_before = output(invoke("status", workspace, "--json"))
    queue = output(invoke("review", "queue", workspace, "--date", "2026-01-06", "--minutes", 10))
    status_after = output(invoke("status", workspace, "--json"))
    assert queue["behind_schedule"] is True
    assert any(item["category"] == "due_review" for item in queue["unscheduled_tasks"])
    assert status_after["course"]["revision"] == status_before["course"]["revision"] == revision
    reviews["memory"][atom_id]["difficulty"] = 9.99
    (workspace / ".atomlearn" / "reviews.yaml").write_text(
        yaml.safe_dump(reviews, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    tampered = invoke("validate", workspace, check=False)
    assert tampered.returncode == 2
    assert "deterministic event replay" in tampered.stderr


def test_passive_review_is_audited_but_does_not_create_memory(tmp_path: Path) -> None:
    workspace, revision = new_workspace(tmp_path)
    atom_id = "calculus.limit.approach"
    activated = output(invoke("activate", workspace, atom_id, "--expected-revision", revision))
    revision, _ = record_and_assess(
        workspace, activated["revision"], atom_id, evidence(atom_id, kind="mastery_check", score=0.9),
        "2026-01-01T00:00:00+00:00",
    )
    refreshed = output(invoke("refresh-reviews", workspace, "--now", "2026-01-02T00:00:00+00:00", "--expected-revision", revision))
    activated = output(invoke("activate", workspace, atom_id, "--expected-revision", refreshed["revision"]))
    revision, _ = record_and_assess(
        workspace, activated["revision"], atom_id, evidence(atom_id, kind="review", score=0.9, retrieval="passive_review"),
        "2026-01-02T00:00:00+00:00",
    )
    state = output(invoke("review", "status", workspace, "--now", "2026-01-02T00:00:00+00:00"))
    assert state["qualified_event_count"] == 0
    assert state["ineligible_event_count"] == 1
    assert state["memory"] == []
    reviews = yaml.safe_load((workspace / ".atomlearn" / "reviews.yaml").read_text(encoding="utf-8"))
    assert reviews["events"][0]["ineligibility_reasons"] == ["not_active_recall"]
    assert output(invoke("validate", workspace))["revision"] == revision
    evidence_state = yaml.safe_load((workspace / ".atomlearn" / "evidence.yaml").read_text(encoding="utf-8"))
    review_evidence = next(item for item in evidence_state["items"] if item["kind"] == "review")
    assert review_evidence["review_event_id"] == reviews["events"][0]["id"]
    reviews["events"][0]["qualified"] = True
    reviews["events"][0]["ineligibility_reasons"] = []
    (workspace / ".atomlearn" / "reviews.yaml").write_text(
        yaml.safe_dump(reviews, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    tampered = invoke("validate", workspace, check=False)
    assert tampered.returncode == 2
    assert "disagrees with Evidence normalization" in tampered.stderr
    reviews["events"] = []
    (workspace / ".atomlearn" / "reviews.yaml").write_text(
        yaml.safe_dump(reviews, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    missing = invoke("validate", workspace, check=False)
    assert missing.returncode == 2
    assert "invalid review_event_id" in missing.stderr


def test_pilot_report_never_promotes_the_candidate() -> None:
    reviews = {
        "items": [], "policy": default_policy(), "memory": {}, "benchmark": default_benchmark(),
        "events": [
            {
                "atom_id": "a", "qualified": True, "prediction_before": None, "recalled": True,
                "fixed_interval_days": 1, "adaptive_interval_days": 3,
            },
            {
                "atom_id": "b", "qualified": True, "prediction_before": 0.8, "recalled": True,
                "fixed_interval_days": 3, "adaptive_interval_days": 5,
            },
        ],
    }
    report = pilot_report(reviews, 2)
    assert report["status"] == "reportable"
    assert report["promotion_allowed"] is False
    assert "causal" in report["limitations"][0]
