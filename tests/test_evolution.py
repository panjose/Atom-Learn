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


def workspace(label: str) -> tuple[Path, int]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"evolution-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"evolution.{label}",
            "--title",
            f"Evolution {label}",
            "--goal",
            "Exercise bounded self-evolution",
        )
    )
    revision = output(
        invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0)
    )["revision"]
    return path, revision


def payload(path: Path, name: str, data: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def propose(path: Path, data: dict, evolution_revision: int = 0) -> dict:
    proposal_file = payload(path, f"proposal-{uuid.uuid4().hex}.yaml", data)
    return output(
        invoke(
            "evolve",
            "propose",
            path,
            "--input",
            proposal_file,
            "--expected-evolution-revision",
            evolution_revision,
        )
    )


def evidence(path: Path, atom_id: str, revision: int, score: float) -> tuple[int, str]:
    evidence_file = payload(
        path,
        f"evidence-{uuid.uuid4().hex}.yaml",
        {
            "atom_id": atom_id,
            "kind": "mastery_check",
            "measurement_kind": "immediate_mastery",
            "measurement_item_id": f"{atom_id}.fixture-v2",
            "episode_id": f"episode-{uuid.uuid4().hex}",
            "assessment": {
                "method": "human",
                "grader_id": "atomlearn/human-adjudication-v1",
                "rubric_version": "human-v1",
                "calibration_set_version": None,
                "independent": True,
                "answer_hash": "sha256:" + "d" * 64,
            },
            "prompt": "Explain and apply the current concept.",
            "response_summary": "The learner produced an observable response.",
            "scores": {"explain": score, "apply": score, "discriminate": score},
            "feedback": "Scored against the declared rubric.",
            "rationale": "Uses performance evidence rather than confidence.",
        },
    )
    recorded = output(
        invoke("record-evidence", path, "--input", evidence_file, "--expected-revision", revision)
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
            "--now",
            "2026-08-14T00:00:00+00:00",
        )
    )
    return assessed["revision"], assessed["result"]


def test_workspace_starts_in_proposal_only_mode() -> None:
    path, course_revision = workspace("initialization")
    state = output(invoke("evolve", "status", path, "--json"))
    assert state["valid"] is True
    assert state["mode"] == "proposal_only"
    assert state["evolution_revision"] == 0
    assert state["course_revision"] == course_revision
    assert (path / "EVOLUTION.md").is_file()
    assert (path / ".atomlearn" / "evolution" / "ledger.ndjson").is_file()
    assert output(invoke("evolve", "validate", path))["ok"] is True


def test_teaching_strategy_requires_approval_and_rolls_back_without_course_mutation() -> None:
    path, course_revision = workspace("strategy")
    created = propose(
        path,
        {
            "type": "teaching_strategy",
            "scope": "learner",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": [],
            "hypothesis": "A contrast-first remediation should reduce repeated misconception errors.",
            "change": {
                "atom_id": "calculus.limit.approach",
                "strategy": {"remediation": "contrast_first", "example_type": "minimal_counterexample"},
            },
            "evaluation": {"success_criteria": []},
        },
    )
    proposal = created["result"]
    assert proposal["risk"] == "low"
    assert proposal["ready_to_apply"] is True
    proposal_id = proposal["id"]

    blocked = invoke("evolve", "apply", path, proposal_id, check=False)
    assert blocked.returncode == 2
    assert "must be approved" in blocked.stderr

    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal_id,
            "--authority",
            "learner",
            "--actor",
            "test-learner",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    applied = output(
        invoke(
            "evolve",
            "apply",
            path,
            proposal_id,
            "--expected-evolution-revision",
            approved["evolution_revision"],
        )
    )
    assert applied["course_revision"] == course_revision
    policy = output(invoke("evolve", "policy", path, "--json"))
    assert policy["learner_strategy"]["atoms"]["calculus.limit.approach"]["remediation"] == "contrast_first"

    monitored = output(
        invoke(
            "evolve",
            "monitor",
            path,
            proposal_id,
            "--expected-evolution-revision",
            applied["evolution_revision"],
        )
    )
    assert monitored["result"]["outcome"] == "insufficient"

    rolled_back = output(
        invoke(
            "evolve",
            "rollback",
            path,
            proposal_id,
            "--reason",
            "The candidate did not improve learner performance.",
            "--expected-evolution-revision",
            monitored["evolution_revision"],
        )
    )
    assert rolled_back["result"]["status"] == "rolled_back"
    policy = output(invoke("evolve", "policy", path, "--json"))
    assert "calculus.limit.approach" not in policy["learner_strategy"]["atoms"]


def test_mastery_evolution_changes_course_and_can_safely_rollback() -> None:
    path, course_revision = workspace("mastery")
    created = propose(
        path,
        {
            "type": "adjust_mastery",
            "scope": "learner",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": [],
            "hypothesis": "Delayed retention needs an explicit transfer dimension.",
            "change": {
                "atom_id": "calculus.limit.approach",
                "required_dimensions": ["explain", "discriminate", "transfer"],
            },
            "evaluation": {"success_criteria": []},
        },
    )
    proposal_id = created["result"]["id"]
    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal_id,
            "--authority",
            "learner",
            "--actor",
            "test-learner",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    applied = output(
        invoke(
            "evolve",
            "apply",
            path,
            proposal_id,
            "--expected-evolution-revision",
            approved["evolution_revision"],
        )
    )
    assert applied["course_revision"] == course_revision + 1
    atom_path = path / ".atomlearn" / "atoms" / "calculus.limit.approach.yaml"
    changed = yaml.safe_load(atom_path.read_text(encoding="utf-8"))
    assert "transfer" in changed["mastery"]["required_dimensions"]

    rolled_back = output(
        invoke(
            "evolve",
            "rollback",
            path,
            proposal_id,
            "--reason",
            "Restore the previous rubric.",
            "--expected-evolution-revision",
            applied["evolution_revision"],
        )
    )
    assert rolled_back["course_revision"] == course_revision + 2
    restored = yaml.safe_load(atom_path.read_text(encoding="utf-8"))
    assert restored["mastery"]["required_dimensions"] == ["explain", "discriminate"]
    assert output(invoke("validate", path))["ok"] is True


def test_stale_proposal_cannot_overwrite_new_learning_state() -> None:
    path, course_revision = workspace("stale")
    created = propose(
        path,
        {
            "type": "teaching_strategy",
            "scope": "learner",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": [],
            "hypothesis": "Use a different example strategy.",
            "change": {
                "atom_id": "calculus.limit.approach",
                "strategy": {"example_type": "counterexample"},
            },
            "evaluation": {"success_criteria": []},
        },
    )
    proposal_id = created["result"]["id"]
    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal_id,
            "--authority",
            "learner",
            "--actor",
            "test-learner",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    activated = output(
        invoke(
            "activate",
            path,
            "calculus.limit.approach",
            "--expected-revision",
            course_revision,
        )
    )
    assert activated["revision"] == course_revision + 1
    stale = invoke(
        "evolve",
        "apply",
        path,
        proposal_id,
        "--expected-evolution-revision",
        approved["evolution_revision"],
        check=False,
    )
    assert stale.returncode == 2
    assert "Proposal is stale" in stale.stderr


def test_runtime_skill_patch_is_never_applied() -> None:
    path, _ = workspace("skill-patch")
    created = propose(
        path,
        {
            "type": "patch_skill",
            "scope": "skill",
            "target_atom_ids": [],
            "observations": [],
            "hypothesis": "A candidate protocol change may improve routing.",
            "change": {"summary": "Candidate change to QUESTION_ROUTING.md for repository review."},
            "evaluation": {"success_criteria": []},
        },
    )
    proposal = created["result"]
    assert proposal["risk"] == "high"
    assert proposal["ready_to_apply"] is False
    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal["id"],
            "--authority",
            "maintainer",
            "--actor",
            "test-maintainer",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    blocked = invoke(
        "evolve",
        "apply",
        path,
        proposal["id"],
        "--expected-evolution-revision",
        approved["evolution_revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "Runtime Skill patches are forbidden" in blocked.stderr


def test_analysis_generates_evidence_grounded_hypothesis_and_proposal() -> None:
    path, revision = workspace("analysis")
    activated = output(
        invoke(
            "activate",
            path,
            "calculus.limit.approach",
            "--expected-revision",
            revision,
        )
    )
    revision, first = evidence(path, "calculus.limit.approach", activated["revision"], 0.55)
    assert first == "partial"
    revision, second = evidence(path, "calculus.limit.approach", revision, 0.55)
    assert second == "partial"
    analyzed = output(
        invoke(
            "evolve",
            "analyze",
            path,
            "--propose",
            "--expected-evolution-revision",
            0,
        )
    )
    result = analyzed["result"]
    assert any(signal["type"] == "teaching_strategy" for signal in result["signals"])
    assert result["created_hypothesis_ids"]
    assert result["created_proposal_ids"]
    proposal_id = result["created_proposal_ids"][0]
    preview = output(invoke("evolve", "preview", path, proposal_id))
    assert preview["ready_to_apply"] is True
    assert preview["stale"] is False
    metrics = yaml.safe_load(
        (path / ".atomlearn" / "evolution" / "metrics.yaml").read_text(encoding="utf-8")
    )
    atom_metrics = metrics["atoms"]["calculus.limit.approach"]
    assert atom_metrics["mastery_failures"] == 2
    assert atom_metrics["evidence_ids"]
    assert metrics["system"]["raw_messages_stored"] is False


def test_structural_evolution_rolls_back_without_deleting_new_atom_history() -> None:
    path, course_revision = workspace("structural")
    created = propose(
        path,
        {
            "type": "split_atom",
            "scope": "course",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": [],
            "hypothesis": "The Atom should separate approach intuition from value discrimination.",
            "change": {
                "proposal": {
                    "action": "split",
                    "source_atom_id": "calculus.limit.approach",
                    "downstream_replacement_id": "calculus.limit.distinguish",
                    "new_atoms": [
                        {
                            "id": "calculus.limit.sequence-intuition",
                            "title": "Sequence approach intuition",
                            "module": "Limits",
                            "objective": "Explain how a sequence approaches a value.",
                            "prerequisites": [],
                            "mastery": {
                                "required_dimensions": ["explain"],
                                "pass_threshold": 0.8,
                                "minimum_dimension_score": 0.6,
                            },
                        },
                        {
                            "id": "calculus.limit.distinguish",
                            "title": "Approach versus equality",
                            "module": "Limits",
                            "objective": "Discriminate approaching a value from taking that value.",
                            "prerequisites": ["calculus.limit.sequence-intuition"],
                            "mastery": {
                                "required_dimensions": ["explain", "discriminate"],
                                "pass_threshold": 0.8,
                                "minimum_dimension_score": 0.6,
                            },
                        },
                    ],
                }
            },
            "evaluation": {"success_criteria": []},
        },
    )
    proposal_id = created["result"]["id"]
    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal_id,
            "--authority",
            "learner",
            "--actor",
            "test-learner",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    applied = output(
        invoke(
            "evolve",
            "apply",
            path,
            proposal_id,
            "--expected-evolution-revision",
            approved["evolution_revision"],
        )
    )
    assert applied["course_revision"] == course_revision + 1
    source_path = path / ".atomlearn" / "atoms" / "calculus.limit.approach.yaml"
    assert yaml.safe_load(source_path.read_text(encoding="utf-8"))["status"] == "archived"

    rolled_back = output(
        invoke(
            "evolve",
            "rollback",
            path,
            proposal_id,
            "--reason",
            "The split did not improve the learning path.",
            "--expected-evolution-revision",
            applied["evolution_revision"],
        )
    )
    assert rolled_back["course_revision"] == course_revision + 2
    assert yaml.safe_load(source_path.read_text(encoding="utf-8"))["status"] == "available"
    generated_path = path / ".atomlearn" / "atoms" / "calculus.limit.sequence-intuition.yaml"
    generated = yaml.safe_load(generated_path.read_text(encoding="utf-8"))
    assert generated["status"] == "archived"
    assert generated["archived_reason"].startswith("rollback")
    assert output(invoke("validate", path))["ok"] is True


def test_automatic_rollback_is_blocked_after_new_learning_activity() -> None:
    path, course_revision = workspace("rollback-guard")
    created = propose(
        path,
        {
            "type": "adjust_review_intervals",
            "scope": "learner",
            "target_atom_ids": [],
            "observations": [],
            "hypothesis": "Shorter early intervals may improve initial retention.",
            "change": {"intervals_days": [1, 2, 5, 14]},
            "evaluation": {"success_criteria": []},
        },
    )
    proposal_id = created["result"]["id"]
    approved = output(
        invoke(
            "evolve",
            "approve",
            path,
            proposal_id,
            "--authority",
            "learner",
            "--actor",
            "test-learner",
            "--expected-evolution-revision",
            created["evolution_revision"],
        )
    )
    applied = output(
        invoke(
            "evolve",
            "apply",
            path,
            proposal_id,
            "--expected-evolution-revision",
            approved["evolution_revision"],
        )
    )
    assert applied["course_revision"] == course_revision + 1
    activity = output(
        invoke(
            "activate",
            path,
            "calculus.limit.approach",
            "--expected-revision",
            applied["course_revision"],
        )
    )
    assert activity["revision"] == applied["course_revision"] + 1
    blocked = invoke(
        "evolve",
        "rollback",
        path,
        proposal_id,
        "--reason",
        "Attempt rollback after additional learning.",
        "--expected-evolution-revision",
        applied["evolution_revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "learning state changed after application" in blocked.stderr


def test_untrusted_proposal_ids_observations_and_criteria_are_rejected() -> None:
    path, _ = workspace("input-guards")
    traversal = invoke("evolve", "preview", path, "..\\policy", check=False)
    assert traversal.returncode == 2
    assert "Invalid evolution proposal ID" in traversal.stderr

    fabricated = propose(
        path,
        {
            "origin": "manual",
            "type": "teaching_strategy",
            "scope": "learner",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": ["ev-999999"],
            "hypothesis": "A fabricated observation must never authorize evolution.",
            "change": {
                "atom_id": "calculus.limit.approach",
                "strategy": {"remediation": "contrast_first"},
            },
            "evaluation": {"success_criteria": []},
        },
    )
    assert fabricated["result"]["ready_to_apply"] is False
    assert "unknown observation IDs: ev-999999" in fabricated["result"]["validation_errors"]
    blocked = invoke(
        "evolve",
        "approve",
        path,
        fabricated["result"]["id"],
        "--authority",
        "learner",
        "--actor",
        "test-learner",
        "--expected-evolution-revision",
        fabricated["evolution_revision"],
        check=False,
    )
    assert blocked.returncode == 2
    assert "unknown observation IDs" in blocked.stderr

    invalid_criterion = propose(
        path,
        {
            "type": "teaching_strategy",
            "scope": "learner",
            "target_atom_ids": ["calculus.limit.approach"],
            "observations": [],
            "hypothesis": "Invalid metrics must be rejected before approval.",
            "change": {
                "atom_id": "calculus.limit.approach",
                "strategy": {"remediation": "contrast_first"},
            },
            "evaluation": {
                "success_criteria": [
                    {"metric": "atom.unknown", "operator": "gte", "value": "high"}
                ]
            },
        },
        evolution_revision=fabricated["evolution_revision"],
    )
    assert invalid_criterion["result"]["ready_to_apply"] is False
    errors = "\n".join(invalid_criterion["result"]["validation_errors"])
    assert "metric is unsupported" in errors
    assert ".value must be numeric" in errors
