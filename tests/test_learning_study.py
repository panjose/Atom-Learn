from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"


def invoke(data_root: Path, *args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["ATOMLEARN_DATA_DIR"] = str(data_root.resolve())
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def write_yaml(path: Path, name: str, value: dict) -> Path:
    target = path / name
    target.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def enrollment(tmp_path: Path, explicit: bool = True) -> Path:
    return write_yaml(
        tmp_path,
        "enrollment.yaml",
        {
            "study_id": "study-transfer-pilot",
            "consent": {
                "explicit": explicit,
                "consent_version": "consent-v1",
                "granted_at": "2026-08-16T00:00:00Z",
                "withdrawable": True,
                "data_categories": [
                    "assessment_scores", "process_counts", "timing_buckets", "ux_ratings"
                ],
            },
            "design": {
                "control_condition": "core-default",
                "candidate_condition": "example-first",
                "assignment_method": "randomized",
                "measures": [
                    "immediate_mastery", "delayed_retention_7d", "delayed_retention_30d",
                    "near_transfer", "far_transfer", "completion", "withdrawal", "total_time", "prompt_burden",
                ],
                "strata": ["domain", "prior_knowledge"],
                "missing_data_policy": "intention_to_treat",
                "analysis_version": "learning-effect-study-v1",
            },
        },
    )


def observation(tmp_path: Path, **extra: object) -> Path:
    payload = {
        "participant_ref": "participant-0123456789abcdef01234567",
        "episode_ref": "episode-0123456789abcdef01234567",
        "assignment": "candidate",
        "measurement_kind": "delayed_retention_7d",
        "score": 0.8,
        "missing_reason": None,
        "completed": True,
        "duration_bucket": "15_to_30m",
        "prompt_count": 2,
        "domain_bucket": "mathematics",
        "prior_knowledge_bucket": "developing",
        "ux": {"satisfaction": 4, "burden": 2},
        **extra,
    }
    return write_yaml(tmp_path, "observation.yaml", payload)


def test_explicit_local_study_consent_minimization_and_withdrawal(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    refused = invoke(
        data_root,
        "study", "enroll", "study-transfer-pilot", "--input", enrollment(tmp_path, False),
        check=False,
    )
    assert refused.returncode == 2
    assert "explicit learning-study consent" in refused.stderr

    enrolled = json.loads(invoke(
        data_root,
        "study", "enroll", "study-transfer-pilot", "--input", enrollment(tmp_path),
    ).stdout)
    assert enrolled["privacy"] == {
        "raw_answers": False,
        "content_text": False,
        "opaque_refs_only": True,
        "local_only": True,
        "automatic_export": False,
    }

    privacy_attack = invoke(
        data_root,
        "study", "record", "study-transfer-pilot", "--input", observation(tmp_path, raw_answer="secret"),
        "--expected-study-revision", 1,
        check=False,
    )
    assert privacy_attack.returncode == 2
    assert "raw_answer" in privacy_attack.stderr

    recorded = json.loads(invoke(
        data_root,
        "study", "record", "study-transfer-pilot", "--input", observation(tmp_path),
        "--expected-study-revision", 1,
    ).stdout)
    assert recorded["observation"]["included_in_analysis"] is True
    assert "raw_answer" not in recorded["observation"]

    unconfirmed = invoke(
        data_root,
        "study", "withdraw", "study-transfer-pilot", "--expected-study-revision", 2,
        check=False,
    )
    assert unconfirmed.returncode == 2
    assert "--confirmed" in unconfirmed.stderr
    withdrawn = json.loads(invoke(
        data_root,
        "study", "withdraw", "study-transfer-pilot", "--confirmed", "--expected-study-revision", 2,
    ).stdout)
    assert withdrawn["excluded_observations"] == 1

    status = json.loads(invoke(data_root, "study", "status", "study-transfer-pilot").stdout)
    assert status["status"] == "withdrawn"
    assert status["included_observation_count"] == 0
    assert status["learning_effect_claim_supported"] is False
    assert json.loads(invoke(data_root, "study", "validate", "study-transfer-pilot").stdout)["ok"] is True

    rejected_after_withdrawal = invoke(
        data_root,
        "study", "record", "study-transfer-pilot", "--input", observation(tmp_path),
        "--expected-study-revision", 3,
        check=False,
    )
    assert rejected_after_withdrawal.returncode == 2
    assert "status is withdrawn" in rejected_after_withdrawal.stderr
