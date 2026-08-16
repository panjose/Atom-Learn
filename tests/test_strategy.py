from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
RUN_ROOT = ROOT / ".test-workspaces"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"


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


def output(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def write_yaml(path: Path, name: str, value: dict) -> Path:
    target = path / name
    target.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def setup_workspace(tmp_path: Path) -> tuple[Path, Path]:
    data_root = (tmp_path / "user-data").resolve()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = RUN_ROOT / f"strategy-{uuid.uuid4().hex}"
    output(invoke(data_root, "init", workspace, "--course-id", "strategy.test", "--title", "Strategy test"))
    output(invoke(data_root, "import-plan", workspace, "--input", PLAN, "--expected-revision", 0))
    output(invoke(data_root, "profile", "enable", workspace))
    return data_root, workspace


def candidate(workspace: Path) -> Path:
    return write_yaml(
        workspace,
        "strategy-candidate.yaml",
        {
            "id": "exp-example-first-001",
            "dimension": "explanation.order",
            "baseline": "intuition_first",
            "candidate": "example_first",
            "hypothesis": "A concrete example improves transfer without weakening delayed recall.",
            "evidence_refs": ["evt-000001"],
            "contexts": ["teaching"],
            "strata": {"atom_types": ["concept", "procedure"], "episode_types": ["new_learning"]},
            "metrics": {
                "primary_learning": ["near_transfer_score", "delayed_retention_score"],
                "learning": ["near_transfer_score", "delayed_retention_score"],
                "process": ["mastery_attempts", "blocking_backtrack_rate"],
                "ux": ["override_rate"],
                "guardrails": ["misconception_recurrence", "mastery_failure_rate"],
            },
            "minimums": {
                "outcomes_per_arm": 10,
                "distinct_episodes": 20,
                "delayed_outcomes_per_arm": 5,
            },
            "thresholds": {
                "minimum_learning_effect": 0.02,
                "maximum_learning_degradation": 0.02,
                "maximum_guardrail_delta": 0.05,
            },
            "eligibility": {
                "measurement_kinds": ["near_transfer", "delayed_retention"],
                "quality_tiers": ["B"],
                "grader_ids": ["atomlearn/human-adjudication-v1"],
            },
            "analysis": {
                "version": "strategy-bootstrap-v1",
                "method": "stratified_bootstrap",
                "seed": 20260816,
                "bootstrap_resamples": 500,
                "confidence_level": 0.95,
                "max_outcomes_per_arm": 50,
                "stop_for_harm": True,
                "stop_at_max_without_effect": True,
            },
        },
    )


def start_experiment(data_root: Path, workspace: Path) -> int:
    enabled = output(invoke(data_root, "strategy", "enable-experiments"))
    proposed = output(
        invoke(
            data_root,
            "strategy",
            "propose",
            workspace,
            "--input",
            candidate(workspace),
            "--expected-strategy-revision",
            enabled["strategy_revision"],
        )
    )
    started = output(
        invoke(
            data_root,
            "strategy",
            "start",
            "exp-example-first-001",
            "--expected-strategy-revision",
            proposed["strategy_revision"],
        )
    )
    return started["strategy_revision"]


def test_opt_in_shadow_replay_override_and_real_outcome_linkage(tmp_path: Path) -> None:
    data_root, workspace = setup_workspace(tmp_path)
    no_state = output(invoke(data_root, "strategy", "status"))
    assert no_state == {"profile_id": "default", "initialized": False, "experiments_enabled": False}
    revision = start_experiment(data_root, workspace)
    output(invoke(data_root, "activate", workspace, "calculus.limit.approach", "--expected-revision", 1))

    shadow = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "shadow-1",
            "--expected-strategy-revision",
            revision,
        )
    )
    assert shadow["exposure"]["status"] == "shadow"
    replay = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "shadow-1",
            "--expected-strategy-revision",
            shadow["strategy_revision"],
        )
    )
    assert replay["replayed"] is True
    assert replay["exposure"] == shadow["exposure"]
    shadow_check = output(
        invoke(data_root, "strategy", "replay-shadow", "exp-example-first-001")
    )
    assert shadow_check["mismatch_count"] == 0
    assert shadow_check == output(
        invoke(data_root, "strategy", "replay-shadow", "exp-example-first-001")
    )

    live = output(
        invoke(
            data_root,
            "strategy",
            "set-live",
            "exp-example-first-001",
            "--expected-strategy-revision",
            shadow["strategy_revision"],
        )
    )
    overrides = write_yaml(workspace, "turn-overrides.yaml", {"explanation.order": "formal_first"})
    overridden = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "overridden-1",
            "--overrides",
            overrides,
            "--expected-strategy-revision",
            live["strategy_revision"],
        )
    )
    assert overridden["exposure"]["status"] == "overridden"
    assert overridden["exposure"]["chosen_value"] == "formal_first"

    first = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "live-1",
            "--expected-strategy-revision",
            overridden["strategy_revision"],
        )
    )
    second = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "live-2",
            "--expected-strategy-revision",
            first["strategy_revision"],
        )
    )
    assert first["exposure"]["status"] == second["exposure"]["status"] == "exposed"

    legacy_evidence = write_yaml(
        workspace,
        "legacy-strategy-evidence.yaml",
        {
            "atom_id": "calculus.limit.approach",
            "kind": "mastery_check",
            "prompt": "An unqualified model-scored response.",
            "response_summary": "A score was supplied without scorer provenance.",
            "scores": {"explain": 0.9, "discriminate": 0.9},
            "feedback": "Unqualified feedback.",
            "rationale": "Regression fixture for the strategy Evidence gate.",
        },
    )
    legacy_recorded = output(
        invoke(data_root, "record-evidence", workspace, "--input", legacy_evidence, "--expected-revision", 2)
    )
    output(
        invoke(
            data_root,
            "assess",
            workspace,
            "calculus.limit.approach",
            "--evidence-id",
            legacy_recorded["evidence_id"],
            "--expected-revision",
            legacy_recorded["revision"],
        )
    )
    rejected_legacy = invoke(
        data_root,
        "strategy",
        "record-outcome",
        workspace,
        first["exposure"]["id"],
        "--evidence-id",
        legacy_recorded["evidence_id"],
        "--expected-strategy-revision",
        second["strategy_revision"],
        check=False,
    )
    assert rejected_legacy.returncode == 2
    assert "qualified for strategy use" in rejected_legacy.stderr

    evidence = write_yaml(
        workspace,
        "strategy-evidence.yaml",
        {
            "atom_id": "calculus.limit.approach",
            "kind": "mastery_check",
            "measurement_kind": "near_transfer",
            "measurement_item_id": "calculus.limit.approach.transfer-v2",
            "episode_id": "live-1",
            "assessment": {
                "method": "human",
                "grader_id": "atomlearn/human-adjudication-v1",
                "rubric_version": "human-v1",
                "calibration_set_version": None,
                "independent": True,
                "answer_hash": "sha256:" + "f" * 64,
            },
            "prompt": "Explain and distinguish the limit idea.",
            "response_summary": "Correct transfer response.",
            "scores": {"explain": 0.9, "discriminate": 0.9, "presentation_fluency": 0.0},
            "feedback": "Correct.",
            "rationale": "Both required dimensions passed.",
        },
    )
    recorded = output(invoke(data_root, "record-evidence", workspace, "--input", evidence, "--expected-revision", 4))
    output(
        invoke(
            data_root,
            "assess",
            workspace,
            "calculus.limit.approach",
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-revision",
            recorded["revision"],
        )
    )
    linked = output(
        invoke(
            data_root,
            "strategy",
            "record-outcome",
            workspace,
            first["exposure"]["id"],
            "--evidence-id",
            recorded["evidence_id"],
            "--expected-strategy-revision",
            second["strategy_revision"],
        )
    )
    assert linked["outcome"]["score"] == 0.9
    duplicate = invoke(
        data_root,
        "strategy",
        "record-outcome",
        workspace,
        second["exposure"]["id"],
        "--evidence-id",
        recorded["evidence_id"],
        "--expected-strategy-revision",
        linked["strategy_revision"],
        check=False,
    )
    assert duplicate.returncode == 2
    assert "already linked" in duplicate.stderr
    monitoring = output(
        invoke(
            data_root,
            "strategy",
            "monitor",
            "exp-example-first-001",
            "--expected-strategy-revision",
            linked["strategy_revision"],
        )
    )
    assert monitoring["decision"] == "monitoring"
    assert "each arm still needs qualified delayed-retention outcomes" in monitoring["reasons"]
    repeated_monitoring = output(
        invoke(
            data_root,
            "strategy",
            "monitor",
            "exp-example-first-001",
            "--expected-strategy-revision",
            linked["strategy_revision"],
        )
    )
    assert repeated_monitoring == monitoring
    paused = output(
        invoke(
            data_root,
            "strategy",
            "pause",
            "exp-example-first-001",
            "--expected-strategy-revision",
            linked["strategy_revision"],
        )
    )
    assert paused["experiment"]["status"] == "paused"
    assert output(invoke(data_root, "strategy", "validate"))["ok"] is True


def test_independent_evaluator_promotes_quality_then_pause_removes_overlay(tmp_path: Path) -> None:
    data_root, workspace = setup_workspace(tmp_path)
    revision = start_experiment(data_root, workspace)
    output(invoke(data_root, "activate", workspace, "calculus.limit.approach", "--expected-revision", 1))
    shadow = output(
        invoke(
            data_root,
            "strategy",
            "exposure",
            workspace,
            "calculus.limit.approach",
            "--episode-key",
            "required-shadow",
            "--expected-strategy-revision",
            revision,
        )
    )
    live = output(
        invoke(
            data_root,
            "strategy",
            "set-live",
            "exp-example-first-001",
            "--expected-strategy-revision",
            shadow["strategy_revision"],
        )
    )

    sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))
    from platform_state import FileLock
    from strategy import StrategyEngine

    engine = StrategyEngine(data_root, "default")
    experiment_id = "exp-example-first-001"
    exposures = engine.exposures()
    outcomes = engine.outcomes()
    arms = ["baseline" if index % 2 else "candidate" for index in range(1, 21)]
    delayed = [index <= 10 for index in range(1, 21)]
    scores = [0.62 if arm == "baseline" else 0.91 for arm in arms]
    for index, (arm, is_delayed, score) in enumerate(zip(arms, delayed, scores), start=1):
        suffix = f"{index:024x}"
        atom_ref = f"atom-{suffix}"
        exposure_id = f"xps-{suffix}"
        exposures.append(
            {
                "kind": "atomlearn.strategy-exposure",
                "schema_version": 1,
                "id": exposure_id,
                "experiment_id": experiment_id,
                "profile_id": "default",
                "workspace_ref": "ws-aaaaaaaaaaaaaaaaaaaaaaaa",
                "workspace_revision": 2,
                "atom_ref": atom_ref,
                "episode_ref": f"episode-{suffix}",
                "arm": arm,
                "assigned_arm": arm,
                "assigned_value": "intuition_first" if arm == "baseline" else "example_first",
                "chosen_value": "intuition_first" if arm == "baseline" else "example_first",
                "context": "teaching",
                "atom_type": "concept",
                "difficulty_bucket": "introductory",
                "prior_diagnostic_bucket": "unassessed",
                "episode_type": "new_learning",
                "stratum": "teaching|concept|introductory|unassessed|new_learning",
                "policy_fingerprint": "sha256:" + (f"{index:x}" * 64)[:64],
                "status": "exposed",
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
        outcomes.append(
            {
                "kind": "atomlearn.strategy-outcome",
                "schema_version": 1,
                "id": f"out-{suffix}",
                "exposure_id": exposure_id,
                "experiment_id": experiment_id,
                "evidence_ref": f"ev-{index:06d}",
                "atom_ref": atom_ref,
                "episode_ref": f"episode-{suffix}",
                "evidence_kind": "review" if is_delayed else "mastery_check",
                "measurement_kind": "delayed_retention" if is_delayed else "near_transfer",
                "quality_tier": "B",
                "grader_id": "atomlearn/human-adjudication-v1",
                "result": "mastered",
                "score": score,
                "attempts": 1,
                "delayed": is_delayed,
                "retention_delay_days": 7 if is_delayed else None,
                "outcome_eligible": True,
                "blocking_backtrack": False,
                "workspace_valid": True,
                "recorded_at": "2026-01-02T00:00:00Z",
            }
        )
    # An unmatched procedure stratum must not enter the concept comparison.
    exposures.append(
        {
            **exposures[-1],
            "id": "xps-ffffffffffffffffffffffff",
            "atom_ref": "atom-ffffffffffffffffffffffff",
            "episode_ref": "episode-ffffffffffffffffffffffff",
            "atom_type": "procedure",
            "stratum": "teaching|procedure|introductory|unassessed|new_learning",
        }
    )
    outcomes.append(
        {
            **outcomes[-1],
            "id": "out-ffffffffffffffffffffffff",
            "exposure_id": "xps-ffffffffffffffffffffffff",
            "evidence_ref": "ev-999999",
            "atom_ref": "atom-ffffffffffffffffffffffff",
            "episode_ref": "episode-ffffffffffffffffffffffff",
        }
    )
    with FileLock(engine.lock_path):
        committed = engine._commit_locked(
            engine.state(),
            "strategy.evaluator_fixture_loaded",
            {"experiment_id": experiment_id, "fixture": "independent-quality-win"},
            exposures=exposures,
            outcomes=outcomes,
        )
    report = output(
        invoke(
            data_root,
            "strategy",
            "monitor",
            experiment_id,
            "--expected-strategy-revision",
            committed["revision"],
        )
    )
    assert report["decision"] == "active"
    assert report["samples"]["distinct_episodes"] == 20
    assert report["metric_layers"]["learning"]["near_transfer_score"]["interval"]["lower"] > 0.02
    policy = output(invoke(data_root, "policy", "effective", workspace, "--context", "teaching"))
    assert policy["effective"]["explanation.order"] == {
        "value": "example_first",
        "source": "user_strategy",
        "source_revision": report["strategy_revision"],
    }
    # Add independent adverse outcomes; guardrail and quality degradation must
    # automatically pause without rewriting any Evidence.
    exposures = engine.exposures()
    outcomes = engine.outcomes()
    for index in range(21, 29):
        arm = "baseline" if index % 2 else "candidate"
        suffix = f"{index:024x}"
        exposure_id = f"xps-{suffix}"
        atom_ref = f"atom-{suffix}"
        exposures.append(
            {
                **exposures[1],
                "id": exposure_id,
                "atom_ref": atom_ref,
                "episode_ref": f"episode-{suffix}",
                "arm": arm,
                "assigned_arm": arm,
                "assigned_value": "intuition_first" if arm == "baseline" else "example_first",
                "chosen_value": "intuition_first" if arm == "baseline" else "example_first",
            }
        )
        outcomes.append(
            {
                **outcomes[0],
                "id": f"out-{suffix}",
                "exposure_id": exposure_id,
                "evidence_ref": f"ev-{index:06d}",
                "atom_ref": atom_ref,
                "episode_ref": f"episode-{suffix}",
                "result": "mastered" if arm == "baseline" else "not_mastered",
                "score": 0.95 if arm == "baseline" else 0.1,
            }
        )
    with FileLock(engine.lock_path):
        adverse = engine._commit_locked(
            engine.state(),
            "strategy.adverse_fixture_loaded",
            {"experiment_id": experiment_id, "fixture": "independent-guardrail-regression"},
            exposures=exposures,
            outcomes=outcomes,
        )
    paused = output(
        invoke(
            data_root,
            "strategy",
            "monitor",
            experiment_id,
            "--expected-strategy-revision",
            adverse["revision"],
        )
    )
    assert paused["decision"] == "paused"
    assert any("guardrail adverse interval crossed" in reason for reason in paused["reasons"])
    policy = output(invoke(data_root, "policy", "effective", workspace, "--context", "teaching"))
    assert policy["effective"]["explanation.order"]["source"] == "core_default"

    import strategy as strategy_module

    original_values = strategy_module.POLICY_VALUES["explanation.order"]
    strategy_module.POLICY_VALUES["explanation.order"] = original_values - {"example_first"}
    try:
        needs_review = engine.monitor(experiment_id, paused["strategy_revision"])
    finally:
        strategy_module.POLICY_VALUES["explanation.order"] = original_values
    assert needs_review["decision"] == "needs_review"


def test_explicit_profile_blocks_start_and_speed_only_candidate_is_rejected(tmp_path: Path) -> None:
    data_root, workspace = setup_workspace(tmp_path)
    signal = write_yaml(
        workspace,
        "explicit-strategy-conflict.yaml",
        {
            "session_id": "strategy-conflict",
            "context": "teaching",
            "scope": "user",
            "signals": [
                {
                    "dimension": "explanation.order",
                    "value": "formal_first",
                    "direction": "prefer",
                    "evidence": "explicit",
                    "reason_code": "explicit_request",
                    "confidence": 0.9,
                    "turn_refs": ["turn-1"],
                }
            ],
        },
    )
    output(
        invoke(
            data_root,
            "adapt",
            "observe-session",
            workspace,
            "--input",
            signal,
            "--expected-profile-revision",
            1,
        )
    )
    enabled = output(invoke(data_root, "strategy", "enable-experiments"))
    proposed = output(
        invoke(
            data_root,
            "strategy",
            "propose",
            workspace,
            "--input",
            candidate(workspace),
            "--expected-strategy-revision",
            enabled["strategy_revision"],
        )
    )
    blocked = invoke(
        data_root,
        "strategy",
        "start",
        "exp-example-first-001",
        "--expected-strategy-revision",
        proposed["strategy_revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "explicit global user preference" in blocked.stderr

    speed_only = yaml.safe_load(candidate(workspace).read_text(encoding="utf-8"))
    speed_only["id"] = "exp-speed-only-001"
    speed_only["metrics"]["primary_learning"] = []
    speed_only["metrics"]["learning"] = []
    speed_path = write_yaml(workspace, "speed-only.yaml", speed_only)
    rejected = invoke(
        data_root,
        "strategy",
        "propose",
        workspace,
        "--input",
        speed_path,
        "--expected-strategy-revision",
        proposed["strategy_revision"],
        check=False,
    )
    assert rejected.returncode == 2
    assert "primary_learning" in rejected.stderr

    immediate_only = yaml.safe_load(candidate(workspace).read_text(encoding="utf-8"))
    immediate_only["id"] = "exp-immediate-only-001"
    immediate_only["metrics"]["primary_learning"] = ["immediate_mastery_score"]
    immediate_only["metrics"]["learning"] = ["immediate_mastery_score"]
    immediate_only["eligibility"]["measurement_kinds"] = ["immediate_mastery"]
    immediate_path = write_yaml(workspace, "immediate-only.yaml", immediate_only)
    rejected_immediate = invoke(
        data_root,
        "strategy",
        "propose",
        workspace,
        "--input",
        immediate_path,
        "--expected-strategy-revision",
        proposed["strategy_revision"],
        check=False,
    )
    assert rejected_immediate.returncode == 2
    assert "delayed-retention or transfer" in rejected_immediate.stderr


def test_v1_migration_excludes_legacy_outcomes_and_clears_overlay(tmp_path: Path) -> None:
    data_root, workspace = setup_workspace(tmp_path)
    enabled = output(invoke(data_root, "strategy", "enable-experiments"))
    strategy_root = data_root / "strategies" / "default"
    fixture_root = ROOT / "tests" / "fixtures"
    legacy_experiment = json.loads((fixture_root / "migrations" / "strategy-v1-experiment.json").read_text(encoding="utf-8"))
    legacy_exposure = json.loads((fixture_root / "self_evolution_v2" / "strategy-exposure.json").read_text(encoding="utf-8"))
    legacy_exposure.pop("assigned_arm")
    legacy_outcome = json.loads((fixture_root / "migrations" / "strategy-v1-outcome.json").read_text(encoding="utf-8"))
    (strategy_root / "experiments" / "exp-example-first-001.yaml").write_text(
        yaml.safe_dump(legacy_experiment, sort_keys=False), encoding="utf-8"
    )
    (strategy_root / "exposures.ndjson").write_text(json.dumps(legacy_exposure) + "\n", encoding="utf-8")
    (strategy_root / "outcomes.ndjson").write_text(json.dumps(legacy_outcome) + "\n", encoding="utf-8")
    state_path = strategy_root / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["active"] = {
        "explanation.order": {
            "value": "example_first",
            "experiment_id": "exp-example-first-001",
            "activated_revision": 1,
        }
    }
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    migrated = output(
        invoke(
            data_root,
            "strategy",
            "migrate-v2",
            "--confirmed",
            "--expected-strategy-revision",
            enabled["strategy_revision"],
        )
    )
    assert migrated["migrated_experiments"] == 1
    assert migrated["excluded_outcomes"] == 1
    assert output(invoke(data_root, "strategy", "validate"))["ok"] is True
    migrated_outcome = json.loads((strategy_root / "outcomes.ndjson").read_text(encoding="utf-8"))
    assert migrated_outcome["outcome_eligible"] is False
    assert migrated_outcome["quality_tier"] == "legacy"
    assert yaml.safe_load(state_path.read_text(encoding="utf-8"))["active"] == {}
    second = output(invoke(data_root, "strategy", "migrate-v2", "--confirmed"))
    assert second["already_current"] is True


def test_fixed_window_with_no_learning_effect_is_rejected(tmp_path: Path) -> None:
    data_root, workspace = setup_workspace(tmp_path)
    candidate_payload = yaml.safe_load(candidate(workspace).read_text(encoding="utf-8"))
    candidate_payload["analysis"]["max_outcomes_per_arm"] = 10
    candidate_path = write_yaml(workspace, "fixed-window-candidate.yaml", candidate_payload)
    enabled = output(invoke(data_root, "strategy", "enable-experiments"))
    proposed = output(invoke(
        data_root,
        "strategy", "propose", workspace, "--input", candidate_path,
        "--expected-strategy-revision", enabled["strategy_revision"],
    ))
    started = output(invoke(
        data_root,
        "strategy", "start", "exp-example-first-001",
        "--expected-strategy-revision", proposed["strategy_revision"],
    ))
    output(invoke(data_root, "activate", workspace, "calculus.limit.approach", "--expected-revision", 1))
    shadow = output(invoke(
        data_root,
        "strategy", "exposure", workspace, "calculus.limit.approach", "--episode-key", "shadow-fixed",
        "--expected-strategy-revision", started["strategy_revision"],
    ))
    live = output(invoke(
        data_root,
        "strategy", "set-live", "exp-example-first-001",
        "--expected-strategy-revision", shadow["strategy_revision"],
    ))

    sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))
    from platform_state import FileLock
    from strategy import StrategyEngine

    engine = StrategyEngine(data_root, "default")
    exposures = engine.exposures()
    outcomes = engine.outcomes()
    for index in range(1, 21):
        arm = "baseline" if index % 2 else "candidate"
        suffix = f"{index:024x}"
        exposure_id = f"xps-{suffix}"
        atom_ref = f"atom-{suffix}"
        is_delayed = index <= 10
        exposures.append({
            "kind": "atomlearn.strategy-exposure",
            "schema_version": 1,
            "id": exposure_id,
            "experiment_id": "exp-example-first-001",
            "profile_id": "default",
            "workspace_ref": "ws-aaaaaaaaaaaaaaaaaaaaaaaa",
            "workspace_revision": 2,
            "atom_ref": atom_ref,
            "episode_ref": f"episode-{suffix}",
            "arm": arm,
            "assigned_arm": arm,
            "assigned_value": "intuition_first" if arm == "baseline" else "example_first",
            "chosen_value": "intuition_first" if arm == "baseline" else "example_first",
            "context": "teaching",
            "atom_type": "concept",
            "difficulty_bucket": "introductory",
            "prior_diagnostic_bucket": "unassessed",
            "episode_type": "new_learning",
            "stratum": "teaching|concept|introductory|unassessed|new_learning",
            "policy_fingerprint": "sha256:" + (f"{index:x}" * 64)[:64],
            "status": "exposed",
            "created_at": "2026-01-01T00:00:00Z",
        })
        outcomes.append({
            "kind": "atomlearn.strategy-outcome",
            "schema_version": 1,
            "id": f"out-{suffix}",
            "exposure_id": exposure_id,
            "experiment_id": "exp-example-first-001",
            "evidence_ref": f"ev-{index:06d}",
            "atom_ref": atom_ref,
            "episode_ref": f"episode-{suffix}",
            "evidence_kind": "review" if is_delayed else "mastery_check",
            "measurement_kind": "delayed_retention" if is_delayed else "near_transfer",
            "quality_tier": "B",
            "grader_id": "atomlearn/human-adjudication-v1",
            "result": "mastered",
            "score": 0.75,
            "attempts": 1,
            "delayed": is_delayed,
            "retention_delay_days": 7 if is_delayed else None,
            "outcome_eligible": True,
            "blocking_backtrack": False,
            "workspace_valid": True,
            "recorded_at": "2026-01-02T00:00:00Z",
        })
    with FileLock(engine.lock_path):
        committed = engine._commit_locked(
            engine.state(),
            "strategy.no_effect_fixture_loaded",
            {"experiment_id": "exp-example-first-001"},
            exposures=exposures,
            outcomes=outcomes,
        )
    report = output(invoke(
        data_root,
        "strategy", "monitor", "exp-example-first-001",
        "--expected-strategy-revision", committed["revision"],
    ))
    assert report["samples"]["window_reached"] is True
    assert report["decision"] == "rejected"
    assert "fixed analysis window ended without a qualifying learning effect" in report["reasons"]
    assert report["metric_layers"]["ux"]["override_rate"]["promotion_eligible"] is False
