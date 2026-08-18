from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"


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


def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "episode-course"
    output(invoke("init", target, "--course-id", "episode.test", "--title", "Episode test"))
    output(invoke("import-plan", target, "--input", PLAN, "--expected-revision", 0))
    output(invoke("activate", target, "calculus.limit.approach", "--expected-revision", 1))
    return target


def test_incremental_checkpoint_survives_close_and_retries_idempotently(tmp_path: Path) -> None:
    target = workspace(tmp_path)
    initial = output(invoke("episode", "status", target))
    assert initial["initialized"] is False
    assert initial["enabled"] is False
    assert not (target / ".atomlearn" / "episodes").exists()

    enabled = output(
        invoke("episode", "enable", target, "--expected-observability-revision", 0)
    )
    assert enabled["observability_revision"] == 1
    assert enabled["coverage_boundary"]["workspace_revision"] == 2
    assert enabled["coverage_boundary"]["historical_episodes_backfilled"] is False

    begun = output(
        invoke(
            "episode",
            "begin",
            target,
            "calculus.limit.approach",
            "--episode-key",
            "turn-001",
            "--request-key",
            "activate-001",
            "--expected-observability-revision",
            1,
            "--expected-workspace-revision",
            2,
        )
    )
    episode_id = begun["episode"]["id"]
    assert begun["episode"]["status"] == "incomplete"
    assert begun["episode"]["checkpoints"][0]["event"] == "activated"

    retried_begin = output(
        invoke(
            "episode",
            "begin",
            target,
            "calculus.limit.approach",
            "--episode-key",
            "turn-001",
            "--request-key",
            "activate-001",
            "--expected-observability-revision",
            1,
            "--expected-workspace-revision",
            2,
        )
    )
    assert retried_begin["replayed"] is True
    assert retried_begin["episode"] == begun["episode"]

    taught = output(
        invoke(
            "episode",
            "checkpoint",
            target,
            episode_id,
            "--event",
            "teaching_step",
            "--request-key",
            "teach-001",
            "--interaction-pattern",
            "example",
            "--teaching-mode",
            "direct",
            "--expected-observability-revision",
            2,
            "--expected-workspace-revision",
            2,
        )
    )
    assert taught["observability_revision"] == 3
    retried_teaching = output(
        invoke(
            "episode",
            "checkpoint",
            target,
            episode_id,
            "--event",
            "teaching_step",
            "--request-key",
            "teach-001",
            "--interaction-pattern",
            "example",
            "--teaching-mode",
            "direct",
            "--expected-observability-revision",
            2,
            "--expected-workspace-revision",
            2,
        )
    )
    assert retried_teaching["replayed"] is True
    assert retried_teaching["checkpoint"] == taught["checkpoint"]

    resumed = output(
        invoke(
            "episode",
            "resume",
            target,
            episode_id,
            "--request-key",
            "resume-001",
            "--expected-observability-revision",
            3,
            "--expected-workspace-revision",
            2,
        )
    )
    assert resumed["next_checkpoint"] == "evidence_attempted"
    retried_resume = output(
        invoke(
            "episode",
            "resume",
            target,
            episode_id,
            "--request-key",
            "resume-001",
            "--expected-observability-revision",
            3,
            "--expected-workspace-revision",
            2,
        )
    )
    assert retried_resume["replayed"] is True
    assert retried_resume["checkpoint"] == resumed["checkpoint"]

    status = output(invoke("episode", "status", target))
    assert status["coverage"]["observed_episodes"] == 1
    assert status["coverage"]["teaching_step_rate"] == 1.0
    assert status["coverage"]["incomplete_without_outcome"] == 1
    assert "not mastery Evidence" in status["claim_boundary"]
    assert output(invoke("episode", "validate", target))["ok"] is True
    assert output(invoke("validate", target))["ok"] is True
    migration = output(invoke("migrate", "validate", "--workspace", target))
    assert migration["ok"] is True
    assert "workspace_episodes" in {item["namespace"] for item in migration["targets"]}

    state = yaml.safe_load((target / ".atomlearn" / "episodes" / "state.yaml").read_text(encoding="utf-8"))
    serialized = json.dumps(state, ensure_ascii=False)
    assert "turn-001" not in serialized
    assert "raw_message" not in serialized
    assert "quote" not in serialized


def test_resume_fails_closed_on_workspace_change_and_no_outcome_never_becomes_strategy_input(
    tmp_path: Path,
) -> None:
    target = workspace(tmp_path)
    enabled = output(invoke("episode", "enable", target, "--expected-observability-revision", 0))
    begun = output(
        invoke(
            "episode",
            "begin",
            target,
            "calculus.limit.approach",
            "--episode-key",
            "turn-002",
            "--request-key",
            "activate-002",
            "--expected-observability-revision",
            enabled["observability_revision"],
            "--expected-workspace-revision",
            2,
        )
    )
    session_payload = target / "session.yaml"
    session_payload.write_text(
        yaml.safe_dump(
            {
                "phase": "teaching",
                "learner_understands": [],
                "learner_confusions": [],
                "next_action": "Continue the current Atom.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output(invoke("update-session", target, "--input", session_payload, "--expected-revision", 2))
    retried_begin = output(
        invoke(
            "episode",
            "begin",
            target,
            "calculus.limit.approach",
            "--episode-key",
            "turn-002",
            "--request-key",
            "activate-002",
            "--expected-observability-revision",
            enabled["observability_revision"],
            "--expected-workspace-revision",
            2,
        )
    )
    assert retried_begin["replayed"] is True
    assert retried_begin["episode"]["id"] == begun["episode"]["id"]
    stale_resume = invoke(
        "episode",
        "resume",
        target,
        begun["episode"]["id"],
        "--request-key",
        "resume-after-gap",
        "--expected-observability-revision",
        begun["observability_revision"],
        "--expected-workspace-revision",
        3,
        check=False,
    )
    assert stale_resume.returncode == 2
    assert "workspace state changed after the last checkpoint" in stale_resume.stderr

    finalized = output(
        invoke(
            "episode",
            "finalize",
            target,
            begun["episode"]["id"],
            "--request-key",
            "final-no-outcome",
            "--mode",
            "no_outcome",
            "--expected-observability-revision",
            begun["observability_revision"],
            "--expected-workspace-revision",
            3,
        )
    )
    assert finalized["episode"]["status"] == "finalized"
    assert finalized["episode"]["finalization_mode"] == "no_outcome"
    assert finalized["strategy_promotion_input"] is False

    session_payload.write_text(
        yaml.safe_dump(
            {
                "phase": "teaching",
                "learner_understands": [],
                "learner_confusions": [],
                "next_action": "Retry the completed finalization safely.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output(invoke("update-session", target, "--input", session_payload, "--expected-revision", 3))
    retried_finalization = output(
        invoke(
            "episode",
            "finalize",
            target,
            begun["episode"]["id"],
            "--request-key",
            "final-no-outcome",
            "--mode",
            "no_outcome",
            "--expected-observability-revision",
            begun["observability_revision"],
            "--expected-workspace-revision",
            3,
        )
    )
    assert retried_finalization["replayed"] is True
    assert retried_finalization["observability_revision"] == finalized["observability_revision"]
    status = output(invoke("episode", "status", target))
    assert status["coverage"]["outcome_recorded_count"] == 0
    assert status["episode_counts"]["finalized"] == 1

    disabled = output(
        invoke(
            "episode",
            "disable",
            target,
            "--expected-observability-revision",
            finalized["observability_revision"],
        )
    )
    assert disabled["enabled"] is False
    assert disabled["episode_counts"]["finalized"] == 1
    retired = output(
        invoke(
            "episode",
            "retire",
            target,
            begun["episode"]["id"],
            "--reason",
            "privacy_request",
            "--expected-observability-revision",
            disabled["observability_revision"],
        )
    )
    assert retired["episode"]["status"] == "retired"
    assert output(invoke("episode", "validate", target))["ok"] is True


def test_outcome_checkpoint_requires_matching_assessed_strategy_eligible_evidence(tmp_path: Path) -> None:
    target = workspace(tmp_path)
    enabled = output(invoke("episode", "enable", target, "--expected-observability-revision", 0))
    begun = output(
        invoke(
            "episode",
            "begin",
            target,
            "calculus.limit.approach",
            "--episode-key",
            "turn-003",
            "--request-key",
            "activate-003",
            "--expected-observability-revision",
            enabled["observability_revision"],
            "--expected-workspace-revision",
            2,
        )
    )
    evidence_payload = target / "episode-evidence.yaml"
    evidence_payload.write_text(
        yaml.safe_dump(
            {
                "atom_id": "calculus.limit.approach",
                "kind": "mastery_check",
                "measurement_kind": "near_transfer",
                "measurement_item_id": "calculus.limit.approach.episode-v2",
                "episode_id": "turn-003",
                "assessment": {
                    "method": "human",
                    "grader_id": "atomlearn/human-adjudication-v1",
                    "rubric_version": "human-v1",
                    "calibration_set_version": None,
                    "independent": True,
                    "answer_hash": "sha256:" + "a" * 64,
                },
                "prompt": "Explain the distinction and apply it to a new case.",
                "response_summary": "The response explained and discriminated the ideas.",
                "scores": {"explain": 0.9, "discriminate": 0.9, "presentation_fluency": 0.0},
                "feedback": "Both required dimensions passed.",
                "rationale": "The response met the declared rubric.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    recorded = output(
        invoke("record-evidence", target, "--input", evidence_payload, "--expected-revision", 2)
    )
    assessed = output(
        invoke(
            "assess",
            target,
            "calculus.limit.approach",
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    checkpointed = output(
        invoke(
            "episode",
            "checkpoint",
            target,
            begun["episode"]["id"],
            "--event",
            "outcome_recorded",
            "--request-key",
            "outcome-003",
            "--strategy-outcome-ref",
            "out-0123456789abcdef01234567",
            "--evidence-ref",
            recorded["evidence_id"],
            "--expected-observability-revision",
            begun["observability_revision"],
            "--expected-workspace-revision",
            assessed["revision"],
        )
    )
    finalized = output(
        invoke(
            "episode",
            "finalize",
            target,
            begun["episode"]["id"],
            "--request-key",
            "final-003",
            "--mode",
            "strategy_outcome_recorded",
            "--expected-observability-revision",
            checkpointed["observability_revision"],
            "--expected-workspace-revision",
            assessed["revision"],
        )
    )
    assert finalized["episode"]["finalization_mode"] == "strategy_outcome_recorded"
    assert finalized["strategy_promotion_input"] is False
    assert output(invoke("episode", "status", target))["coverage"]["outcome_recorded_count"] == 1
    assert output(invoke("episode", "validate", target))["ok"] is True
