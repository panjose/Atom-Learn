from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atom-learn" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from behavior_eval import BehaviorEvaluationError, evaluate, load_protocol, validate_report


def metrics_for(case: dict) -> dict:
    metrics = {
        "protocol_adherent": True,
        "atoms_added": None,
        "future_knowledge_leakage": None,
        "state_mutation_correct": None,
        "citation_supported": None,
        "resume_success": None,
        "grading_abstention_quality": None,
    }
    for metric in case["required_metrics"]:
        if metric == "atoms_added":
            metrics[metric] = 3 if case["category"] == "detailed_expansion" else 1
        elif metric == "future_knowledge_leakage":
            metrics[metric] = False
        elif metric == "grading_abstention_quality":
            metrics[metric] = "correct"
        else:
            metrics[metric] = True
    return metrics


def behavior_run(kind: str) -> dict:
    protocol = load_protocol()
    results = []
    for case in protocol["cases"]:
        metrics = metrics_for(case)
        annotations = [
            {
                "evaluator_id": "deterministic-v1" if kind == "engineering_smoke" else "reviewer-a",
                "evaluator_type": "deterministic" if kind == "engineering_smoke" else "human",
                "independent": True,
                "metrics": metrics,
            }
        ]
        if kind == "model_compatibility":
            annotations.append(
                {
                    "evaluator_id": "reviewer-b",
                    "evaluator_type": "human",
                    "independent": True,
                    "metrics": copy.deepcopy(metrics),
                }
            )
        results.append(
            {
                "case_id": case["id"],
                "trace_hash": "sha256:" + hashlib.sha256(case["id"].encode("utf-8")).hexdigest(),
                "annotations": annotations,
                "adjudication": None,
            }
        )
    return {
        "kind": "atomlearn.harness-behavior-run",
        "schema_version": 1,
        "run_id": f"behavior-run-{kind}-001",
        "run_kind": kind,
        "protocol_version": protocol["protocol_version"],
        "prompt_protocol_version": protocol["prompt_protocol_version"],
        "model": {"provider": "fixture", "name": "fixture-model", "version": "2026-08-18"},
        "harness": {"name": "fixture-harness", "version": "1.0.0"},
        "temperature": 0,
        "seed": 20260818,
        "started_at": "2026-08-18T00:00:00+00:00",
        "completed_at": "2026-08-18T00:10:00+00:00",
        "results": results,
    }


def test_engineering_smoke_cannot_be_misrepresented_as_model_or_learning_evidence() -> None:
    report = evaluate(behavior_run("engineering_smoke"))
    assert report["completeness"]["complete"] is True
    assert report["quality_gate"] == "engineering_smoke_only"
    assert report["compatibility_claim_allowed"] is False
    assert report["learning_effect_claims_allowed"] is False
    assert report["evidence_layer"] == "harness_model_behavior"
    assert report["metrics"]["human_review_exact_agreement"] is None
    assert "Learning gain" in report["cannot_establish"][0]
    assert validate_report(report)["ok"] is True


def test_complete_dual_reviewed_bilingual_run_can_pass_only_for_its_exact_configuration() -> None:
    report = evaluate(behavior_run("model_compatibility"))
    assert report["completeness"]["required_cases"] == 18
    assert report["environment"]["languages"] == ["en", "zh-CN"]
    assert report["metrics"]["protocol_adherence_rate"] == 1.0
    assert report["metrics"]["future_knowledge_leakage_rate"] == 0.0
    assert report["metrics"]["human_review_exact_agreement"] == 1.0
    assert all(report["threshold_checks"].values())
    assert report["quality_gate"] == "pass"
    assert report["compatibility_claim_allowed"] is True
    assert "untested model" in report["cannot_establish"][1]
    assert report["learning_effect_claims_allowed"] is False


def test_missing_second_reviewer_or_disagreement_never_passes() -> None:
    missing_review = behavior_run("model_compatibility")
    missing_review["results"][0]["annotations"] = missing_review["results"][0]["annotations"][:1]
    incomplete = evaluate(missing_review)
    assert incomplete["quality_gate"] == "incomplete"
    assert incomplete["compatibility_claim_allowed"] is False
    assert "two independent human annotations" in incomplete["completeness"]["annotation_errors"][0]

    disagreement = behavior_run("model_compatibility")
    disagreement["results"][0]["annotations"][1]["metrics"]["protocol_adherent"] = False
    unresolved = evaluate(disagreement)
    assert unresolved["quality_gate"] == "incomplete"
    assert any("disagreement requires adjudication" in item for item in unresolved["completeness"]["annotation_errors"])

    adjudicated = behavior_run("model_compatibility")
    adjudicated["results"][0]["annotations"][1]["metrics"]["protocol_adherent"] = False
    adjudicated["results"][0]["adjudication"] = {
        "adjudicator_id": "reviewer-c",
        "reason_code": "rubric_disagreement",
        "metrics": copy.deepcopy(adjudicated["results"][0]["annotations"][0]["metrics"]),
    }
    resolved = evaluate(adjudicated)
    assert resolved["completeness"]["complete"] is True
    assert resolved["quality_gate"] == "pass"
    assert resolved["metrics"]["human_review_exact_agreement"] < 1.0


def test_raw_output_or_unrecognized_fields_are_rejected() -> None:
    invalid = behavior_run("engineering_smoke")
    invalid["results"][0]["raw_output"] = "future knowledge dump"
    with pytest.raises(BehaviorEvaluationError, match="Additional properties"):
        evaluate(invalid)

    report = evaluate(behavior_run("model_compatibility"))
    report["learning_effect_claims_allowed"] = True
    with pytest.raises(BehaviorEvaluationError):
        validate_report(report)

    forged = evaluate(behavior_run("model_compatibility"))
    forged["metrics"]["protocol_adherence_rate"] = 0.0
    with pytest.raises(BehaviorEvaluationError, match="threshold flags"):
        validate_report(forged)
