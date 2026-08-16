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


def write_yaml(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def item(
    item_id: str,
    measurement_kind: str,
    *,
    answer_type: str = "exact_choice",
    held_out: bool = False,
    delay: int | None = None,
) -> dict:
    if answer_type == "exact_choice":
        answer_spec = {"type": "exact_choice", "accepted": ["B"], "case_sensitive": False}
        grader_id = "atomlearn/exact-choice-v1"
        rubric_version = "exact-v1"
    elif answer_type == "numeric_unit":
        answer_spec = {
            "type": "numeric_unit",
            "expected": 9.81,
            "absolute_tolerance": 0.02,
            "relative_tolerance": 0,
            "unit": "m/s^2",
            "unit_aliases": ["m s^-2"],
        }
        grader_id = "atomlearn/numeric-unit-v1"
        rubric_version = "numeric-v1"
    else:
        answer_spec = {"type": "open_response", "anchor_set": "anchors-v1"}
        grader_id = "atomlearn/fixture-anchored-v1"
        rubric_version = "open-v1"
    return {
        "id": item_id,
        "atom_family_id": "calculus.limit.family",
        "measurement_kind": measurement_kind,
        "required_dimensions": ["explain", "discriminate"],
        "prompt": "Provide the independently assessed response.",
        "grader_id": grader_id,
        "rubric_version": rubric_version,
        "answer_spec": answer_spec,
        "holdout": {
            "visibility": "held_out" if held_out else "teaching_visible",
            "context_isolated": held_out,
            "family_id": f"family.{item_id}",
        },
        "retention_delay_days": delay,
        "language": "en",
        "domain": "calculus",
        "difficulty": "introductory",
    }


def test_registry_protocol_and_all_measurement_kinds_are_versioned(tmp_path: Path) -> None:
    registry = output(invoke("measure", "registry"))
    assert registry["registry_version"] == "scorer-v1"
    assert {entry["max_quality_tier"] for entry in registry["scorers"]} >= {"A", "B", "C", "legacy"}
    protocol = output(invoke("measure", "validate-protocol"))
    assert protocol["protocol"]["layers"] == ["engineering", "calibration", "learning_effect"]
    bank = {
        "kind": "atomlearn.measurement-bank",
        "schema_version": 1,
        "bank_id": "measurement.fixture",
        "bank_version": "v1",
        "items": [
            item("item.immediate", "immediate_mastery"),
            item("item.retention", "delayed_retention", held_out=True, delay=7),
            item("item.near", "near_transfer", answer_type="open_response", held_out=True),
            item("item.far", "far_transfer", answer_type="numeric_unit", held_out=True),
        ],
    }
    bank_file = write_yaml(tmp_path, "bank.yaml", bank)
    report = output(invoke("measure", "validate-bank", "--input", bank_file))
    assert report["measurement_counts"] == {
        "delayed_retention": 1,
        "far_transfer": 1,
        "immediate_mastery": 1,
        "near_transfer": 1,
    }
    bank["items"][2]["holdout"] = {
        "visibility": "teaching_visible",
        "context_isolated": False,
        "family_id": "family.item.near",
    }
    unsafe = invoke("measure", "validate-bank", "--input", write_yaml(tmp_path, "unsafe.yaml", bank), check=False)
    assert unsafe.returncode == 2
    assert "isolated and held out" in unsafe.stderr


def test_deterministic_graders_hash_but_do_not_echo_raw_answers(tmp_path: Path) -> None:
    exact = {"item": item("item.exact", "immediate_mastery"), "response": "  b  "}
    exact_file = write_yaml(tmp_path, "exact.yaml", exact)
    exact_result = output(invoke("measure", "grade", "--input", exact_file))["result"]
    assert exact_result["passed"] is True
    assert exact_result["scores"] == {"explain": 1.0, "discriminate": 1.0}
    assert exact_result["answer_hash"].startswith("sha256:")
    assert "  b  " not in invoke("measure", "grade", "--input", exact_file).stdout

    numeric = {"item": item("item.numeric", "immediate_mastery", answer_type="numeric_unit"), "response": "9.80 m/s^2"}
    numeric_result = output(
        invoke("measure", "grade", "--input", write_yaml(tmp_path, "numeric.yaml", numeric))
    )["result"]
    assert numeric_result["passed"] is True
    numeric["response"] = "9.80 kg"
    mismatch = output(
        invoke("measure", "grade", "--input", write_yaml(tmp_path, "numeric-bad.yaml", numeric))
    )["result"]
    assert mismatch["passed"] is False


def calibration_set() -> dict:
    strata = [
        ("en", "calculus", "introductory", "short"),
        ("zh-CN", "physics", "intermediate", "medium"),
        ("en", "history", "advanced", "long"),
        ("zh-CN", "biology", "introductory", "short"),
    ]
    items = []
    for index, (language, domain, difficulty, answer_length) in enumerate(strata):
        human = {"explain": 0.8 + index * 0.02, "apply": 0.75 + index * 0.02}
        predicted = {key: round(value + 0.02, 3) for key, value in human.items()}
        items.append(
            {
                "id": f"calibration.item-{index}",
                "strata": {
                    "language": language,
                    "domain": domain,
                    "difficulty": difficulty,
                    "answer_length": answer_length,
                },
                "human_scores": human,
                "prediction": {"abstain": False, "review_required": index == 3, "scores": predicted},
            }
        )
    return {
        "kind": "atomlearn.calibration-set",
        "schema_version": 1,
        "id": "calibration-open-v1",
        "grader_id": "atomlearn/fixture-anchored-v1",
        "rubric_version": "open-v1",
        "thresholds": {
            "min_samples": 4,
            "max_mae": 0.05,
            "min_agreement": 0.9,
            "max_abstain_rate": 0.1,
            "agreement_tolerance": 0.05,
        },
        "baseline": {
            "report_sha256": "sha256:" + "1" * 64,
            "grader_id": "atomlearn/fixture-anchored-v0",
            "rubric_version": "open-v0",
            "calibration_set_version": "calibration-open-v0",
            "mae": 0.03,
            "bias": 0.01,
            "agreement": 0.95,
            "abstain_rate": 0.0,
            "review_required_rate": 0.25,
            "max_allowed_metric_delta": 0.1,
        },
        "items": items,
    }


def test_open_response_calibration_report_is_reproducible_and_stratified(tmp_path: Path) -> None:
    calibration_file = write_yaml(tmp_path, "calibration.yaml", calibration_set())
    first = output(invoke("measure", "calibrate", "--input", calibration_file))["report"]
    second = output(invoke("measure", "calibrate", "--input", calibration_file))["report"]
    assert first == second
    assert first["qualified"] is True
    assert first["overall"] == {"count": 8, "mae": 0.02, "bias": 0.02, "agreement": 1.0}
    assert len(first["strata"]) == 4
    assert sum(first["pass_confusion"].values()) == 4
    assert first["review_required_rate"] == 0.25
    assert first["drift"]["compared"] is True
    assert first["drift"]["metric_deltas"]["mae"] == -0.01
    assert first["drift"]["threshold_exceeded"] is False
    underpowered = calibration_set()
    underpowered["thresholds"]["min_samples"] = 5
    report = output(
        invoke("measure", "calibrate", "--input", write_yaml(tmp_path, "underpowered.yaml", underpowered))
    )["report"]
    assert report["qualified"] is False
    assert "sample_floor_not_met" in report["qualification_reasons"]
    without_baseline = calibration_set()
    without_baseline.pop("baseline")
    report = output(
        invoke("measure", "calibrate", "--input", write_yaml(tmp_path, "without-baseline.yaml", without_baseline))
    )["report"]
    assert report["drift"]["compared"] is False
    assert report["qualified"] is True
    excessive_drift = calibration_set()
    excessive_drift["baseline"]["max_allowed_metric_delta"] = 0.01
    report = output(
        invoke("measure", "calibrate", "--input", write_yaml(tmp_path, "drift.yaml", excessive_drift))
    )["report"]
    assert report["drift"]["threshold_exceeded"] is True
    assert "drift_threshold_exceeded" in report["qualification_reasons"]
    committed_input = ROOT / "atom-learn" / "assets" / "benchmarks" / "calibration-open-v1.yaml"
    committed_expected = json.loads(
        (ROOT / "atom-learn" / "assets" / "benchmarks" / "calibration-open-v1.report.json").read_text(
            encoding="utf-8"
        )
    )
    committed_actual = output(invoke("measure", "calibrate", "--input", committed_input))["report"]
    assert committed_actual == committed_expected


def initialized_workspace(tmp_path: Path) -> tuple[Path, int]:
    workspace = tmp_path / "course"
    output(invoke("init", workspace, "--course-id", "measurement.test", "--title", "Measurement test"))
    imported = output(invoke("import-plan", workspace, "--input", PLAN, "--expected-revision", 0))
    activated = output(
        invoke("activate", workspace, "calculus.limit.approach", "--expected-revision", imported["revision"])
    )
    return workspace, activated["revision"]


def base_evidence() -> dict:
    return {
        "atom_id": "calculus.limit.approach",
        "kind": "mastery_check",
        "prompt": "Choose the valid distinction and explain it.",
        "response_summary": "The learner selected the correct distinction.",
        "feedback": "The deterministic item passed.",
        "rationale": "The response was checked without model self-scoring.",
    }


def test_persisted_v2_evidence_uses_core_scores_and_omits_raw_response(tmp_path: Path) -> None:
    workspace, revision = initialized_workspace(tmp_path)
    payload = {
        **base_evidence(),
        "measurement_kind": "immediate_mastery",
        "measurement_item_id": "a-claim-that-core-must-replace",
        "episode_id": "episode-deterministic-1",
        "assessment": {
            "method": "deterministic",
            "grader_id": "atomlearn/exact-choice-v1",
            "rubric_version": "exact-v1",
            "calibration_set_version": None,
            "independent": True,
            "answer_hash": None,
        },
        "grading_input": {
            "item": item("item.workspace-exact", "immediate_mastery"),
            "response": "B",
        },
    }
    mismatch = invoke(
        "record-evidence",
        workspace,
        "--input",
        write_yaml(tmp_path, "mismatch.yaml", payload),
        "--expected-revision",
        revision,
        check=False,
    )
    assert mismatch.returncode == 2
    assert "measurement_item_id disagree" in mismatch.stderr
    payload["measurement_item_id"] = "item.workspace-exact"
    recorded = output(
        invoke(
            "record-evidence",
            workspace,
            "--input",
            write_yaml(tmp_path, "evidence.yaml", payload),
            "--expected-revision",
            revision,
        )
    )
    stored_text = (workspace / ".atomlearn" / "evidence.yaml").read_text(encoding="utf-8")
    assert "response: B" not in stored_text
    stored = yaml.safe_load(stored_text)["items"][0]
    assert stored["quality_tier"] == "A"
    assert stored["required_dimension_scores"] == {"explain": 1.0, "discriminate": 1.0}
    assert stored["assessment"]["answer_hash"].startswith("sha256:")
    assessed = output(
        invoke(
            "assess",
            workspace,
            "calculus.limit.approach",
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    assert assessed["result"] == "mastered"


def test_free_model_scores_cannot_master_and_legacy_migration_preserves_results(tmp_path: Path) -> None:
    workspace, revision = initialized_workspace(tmp_path)
    unqualified = {
        **base_evidence(),
        "measurement_kind": "near_transfer",
        "measurement_item_id": "item.free-model",
        "episode_id": "episode-free-model",
        "assessment": {
            "method": "anchored_model",
            "grader_id": "atomlearn/unregistered-model-v1",
            "rubric_version": "unregistered-v1",
            "calibration_set_version": None,
            "independent": True,
            "answer_hash": "sha256:" + "9" * 64,
        },
        "scores": {"explain": 1.0, "discriminate": 1.0},
    }
    recorded = output(
        invoke(
            "record-evidence",
            workspace,
            "--input",
            write_yaml(tmp_path, "unqualified.yaml", unqualified),
            "--expected-revision",
            revision,
        )
    )
    assessed = output(
        invoke(
            "assess",
            workspace,
            "calculus.limit.approach",
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    assert assessed["result"] == "partial"
    state = yaml.safe_load((workspace / ".atomlearn" / "evidence.yaml").read_text(encoding="utf-8"))
    assert state["items"][0]["mastery_eligible"] is False
    assert state["items"][0]["eligibility_block"] == "scorer_not_qualified_for_mastery"

    legacy_workspace, legacy_revision = initialized_workspace(tmp_path / "legacy")
    evidence_path = legacy_workspace / ".atomlearn" / "evidence.yaml"
    evidence_state = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    legacy_item = {
        "id": "ev-legacy-1",
        "atom_id": "calculus.limit.approach",
        "kind": "mastery_check",
        "prompt": "Historical prompt.",
        "response_summary": "Historical summary.",
        "scores": {"explain": 0.7, "discriminate": 0.6},
        "feedback": "Historical feedback.",
        "rationale": "Historical rationale.",
        "result": "pending",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    evidence_state["items"].append(legacy_item)
    evidence_path.write_text(yaml.safe_dump(evidence_state, sort_keys=False), encoding="utf-8")
    atom_path = legacy_workspace / ".atomlearn" / "atoms" / "calculus.limit.approach.yaml"
    atom = yaml.safe_load(atom_path.read_text(encoding="utf-8"))
    atom["evidence_ids"].append("ev-legacy-1")
    atom_path.write_text(yaml.safe_dump(atom, allow_unicode=True, sort_keys=False), encoding="utf-8")
    before = {"scores": legacy_item["scores"], "result": legacy_item["result"]}
    migrated = output(
        invoke(
            "migrate-evidence",
            legacy_workspace,
            "--confirmed",
            "--expected-revision",
            legacy_revision,
        )
    )
    assert migrated["migrated_count"] == 1
    upgraded = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))["items"][0]
    assert {"scores": upgraded["scores"], "result": upgraded["result"]} == before
    assert upgraded["quality_tier"] == "legacy"
    assert upgraded["mastery_eligible"] is False
    assert upgraded["strategy_eligible"] is False
    assert output(invoke("validate", legacy_workspace))["ok"] is True
