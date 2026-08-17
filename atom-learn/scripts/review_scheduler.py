#!/usr/bin/env python3
"""Qualified review normalization, per-Atom memory, and capacity-aware queues."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from core_paths import CORE_ROOT

BENCHMARK_PATH = CORE_ROOT / "assets" / "benchmarks" / "memory-core-v1.yaml"
MODEL_VERSION = "atomlearn-memory-v1"
POLICY_MODES = {"fixed", "adaptive-shadow", "adaptive-active"}
OBJECTIVES = {"long_term", "exam"}
RETRIEVAL_MODES = {"active_recall", "recognition", "passive_review"}
DEFAULT_INTERVALS = [1, 3, 7, 30]


class ReviewSchedulerError(RuntimeError):
    """Review configuration, memory state, or queue input is invalid."""


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc).replace(microsecond=0)).isoformat()


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReviewSchedulerError(f"{label} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewSchedulerError(f"{label} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReviewSchedulerError(f"{label} must include a timezone offset")
    return parsed


def _read(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReviewSchedulerError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewSchedulerError(f"Expected a mapping in {path}")
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def default_policy() -> dict[str, Any]:
    return {
        "mode": "fixed",
        "desired_retention": 0.9,
        "objective": "long_term",
        "exam_target_date": None,
        "final_review_days": 7,
        "active_opt_in": False,
        "model_version": MODEL_VERSION,
        "updated_at": None,
    }


def default_benchmark() -> dict[str, Any]:
    return {
        "profile_id": "memory-core-v1",
        "profile_sha256": None,
        "model_version": MODEL_VERSION,
        "status": "not_run",
        "metrics": None,
        "run_at": None,
    }


def initialize_review_state(reviews: dict[str, Any]) -> dict[str, Any]:
    reviews.setdefault("policy", default_policy())
    reviews.setdefault("memory", {})
    reviews.setdefault("events", [])
    reviews.setdefault("benchmark", default_benchmark())
    return reviews


def validate_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewSchedulerError("review_observation must be a mapping")
    required = {"retrieval_mode", "hint_count", "delayed", "response_time_seconds"}
    if set(value) != required:
        raise ReviewSchedulerError("review_observation requires exactly retrieval_mode, hint_count, delayed, and response_time_seconds")
    mode = value.get("retrieval_mode")
    if mode not in RETRIEVAL_MODES:
        raise ReviewSchedulerError(f"review_observation.retrieval_mode must be one of {sorted(RETRIEVAL_MODES)}")
    hints = value.get("hint_count")
    if isinstance(hints, bool) or not isinstance(hints, int) or not 0 <= hints <= 20:
        raise ReviewSchedulerError("review_observation.hint_count must be an integer from 0 to 20")
    delayed = value.get("delayed")
    if not isinstance(delayed, bool):
        raise ReviewSchedulerError("review_observation.delayed must be boolean")
    seconds = value.get("response_time_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(float(seconds)):
        raise ReviewSchedulerError("review_observation.response_time_seconds must be finite")
    if not 0 <= float(seconds) <= 7200:
        raise ReviewSchedulerError("review_observation.response_time_seconds must be between 0 and 7200")
    return {
        "retrieval_mode": mode,
        "hint_count": hints,
        "delayed": delayed,
        "response_time_seconds": round(float(seconds), 3),
    }


def _response_bucket(seconds: float) -> str:
    if seconds < 30:
        return "under_30s"
    if seconds <= 120:
        return "30_to_120s"
    if seconds <= 300:
        return "121_to_300s"
    return "over_300s"


def _event_basis(evidence: dict[str, Any]) -> dict[str, Any]:
    """Derive every Evidence-owned normalized field and qualification reason."""

    reasons: list[str] = []
    observation = evidence.get("review_observation")
    normalized: dict[str, Any] | None = None
    if observation is None:
        reasons.append("missing_review_observation")
    else:
        try:
            normalized = validate_observation(observation)
        except ReviewSchedulerError:
            reasons.append("invalid_review_observation")
        else:
            if normalized["retrieval_mode"] != "active_recall":
                reasons.append("not_active_recall")
            if normalized["delayed"] is not True:
                reasons.append("not_delayed")
    if evidence.get("measurement_kind") != "delayed_retention":
        reasons.append("not_delayed_retention_measurement")
    if evidence.get("mastery_eligible") is not True:
        reasons.append("scorer_not_mastery_eligible")
    if evidence.get("quality_tier") not in {"A", "B"}:
        reasons.append("scorer_quality_not_qualified")
    scores = evidence.get("required_dimension_scores", {})
    valid_scores = (
        isinstance(scores, dict)
        and bool(scores)
        and all(_number_between(value, 0, 1) for value in scores.values())
    )
    values = [float(value) for value in scores.values()] if valid_scores else []
    if not values:
        reasons.append("missing_required_dimension_scores")
    return {
        "correctness": round(sum(values) / len(values), 6) if values else 0.0,
        "required_dimension_minimum": round(min(values), 6) if values else 0.0,
        "hint_count": normalized["hint_count"] if normalized else None,
        "delayed": normalized["delayed"] if normalized else None,
        "retrieval_mode": normalized["retrieval_mode"] if normalized else None,
        "response_time_bucket": _response_bucket(normalized["response_time_seconds"]) if normalized else None,
        "scorer_quality": evidence.get("quality_tier"),
        "qualified": not reasons,
        "ineligibility_reasons": reasons,
    }


def _bounded(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _number_between(value: Any, lower: float, upper: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and lower <= float(value) <= upper
    )


def retrievability(state: dict[str, Any], at: datetime) -> float:
    last = _parse_time(state["last_qualified_review_at"], "memory.last_qualified_review_at")
    elapsed = max(0.0, (at - last).total_seconds() / 86400)
    stability = max(0.05, float(state["stability_days"]))
    return round(_bounded(0.9 ** (elapsed / stability), 0.0, 1.0), 6)


def _interval_for(stability: float, desired_retention: float) -> int:
    interval = stability * math.log(desired_retention) / math.log(0.9)
    return max(1, min(3650, int(round(interval))))


def apply_qualified_event(
    previous: dict[str, Any] | None,
    event: dict[str, Any],
    desired_retention: float,
) -> tuple[dict[str, Any], float | None]:
    """Apply a deterministic DSR/FSRS-like adapter to one qualified Atom event."""
    score = float(event["correctness"])
    hints = int(event["hint_count"])
    at = _parse_time(event["assessed_at"], "review event assessed_at")
    prediction_before: float | None = None
    if previous is None:
        difficulty = _bounded(7.0 - 4.0 * score + 0.35 * hints, 1.0, 10.0)
        stability = _bounded(0.5 + 3.0 * score / (1.0 + 0.25 * hints), 0.25, 3650.0)
        qualified_count = 1
    else:
        prediction_before = retrievability(previous, at)
        old_difficulty = float(previous["difficulty"])
        difficulty = old_difficulty + 1.8 * (0.75 - score) + 0.25 * hints
        difficulty = _bounded(0.9 * difficulty + 0.1 * 5.0, 1.0, 10.0)
        old_stability = max(0.25, float(previous["stability_days"]))
        if event["recalled"]:
            difficulty_factor = 0.7 + 0.12 * (10.0 - difficulty)
            retrieval_factor = max(0.02, (1.0 - prediction_before) ** 0.65)
            quality_factor = (0.75 + 0.5 * score) / (1.0 + 0.25 * hints)
            stability = old_stability * (1.0 + difficulty_factor * retrieval_factor * quality_factor)
        else:
            stability = old_stability * max(0.2, 0.2 + 0.55 * score) / (1.0 + 0.1 * hints)
        stability = _bounded(stability, 0.25, 3650.0)
        qualified_count = int(previous.get("qualified_event_count", 0)) + 1
    interval = _interval_for(stability, desired_retention)
    return (
        {
            "atom_id": event["atom_id"],
            "scheduler": "adaptive",
            "stability_days": round(stability, 6),
            "retrievability": 1.0,
            "difficulty": round(difficulty, 6),
            "desired_retention": round(desired_retention, 3),
            "last_qualified_review_at": _iso(at),
            "model_version": MODEL_VERSION,
            "qualified_event_count": qualified_count,
            "suggested_interval_days": interval,
            "suggested_due_at": _iso(at + timedelta(days=interval)),
        },
        prediction_before,
    )


def record_review_event(
    reviews: dict[str, Any], evidence: dict[str, Any], atom: dict[str, Any], at: datetime
) -> dict[str, Any] | None:
    if evidence.get("kind") != "review":
        return None
    initialize_review_state(reviews)
    if any(item.get("evidence_id") == evidence.get("id") for item in reviews["events"]):
        raise ReviewSchedulerError(f"Review Evidence {evidence.get('id')} already has a normalized event")
    basis = _event_basis(evidence)
    event_id = f"rve-{len(reviews['events']) + 1:06d}"
    event = {
        "id": event_id,
        "atom_id": atom["id"],
        "evidence_id": evidence["id"],
        "assessed_at": _iso(at),
        "recalled": evidence.get("result") == "mastered",
        **basis,
        "prediction_before": None,
        "fixed_interval_days": None,
        "adaptive_interval_days": None,
        "scheduled_review_id": None,
    }
    if basis["qualified"]:
        policy = reviews["policy"]
        previous = reviews["memory"].get(atom["id"])
        memory, prediction_before = apply_qualified_event(previous, event, float(policy["desired_retention"]))
        memory["scheduler"] = "fixed" if policy["mode"] == "fixed" else "adaptive"
        reviews["memory"][atom["id"]] = memory
        event["prediction_before"] = prediction_before
        event["adaptive_interval_days"] = memory["suggested_interval_days"]
    reviews["events"].append(event)
    evidence["review_event_id"] = event_id
    return event


def _profile() -> tuple[dict[str, Any], str]:
    profile = _read(BENCHMARK_PATH)
    return profile, _digest(profile)


def benchmark_current(reviews: dict[str, Any]) -> bool:
    initialize_review_state(reviews)
    _, fingerprint = _profile()
    benchmark = reviews["benchmark"]
    identity_matches = (
        benchmark.get("status") == "passed"
        and benchmark.get("profile_sha256") == fingerprint
        and benchmark.get("model_version") == MODEL_VERSION
    )
    if not identity_matches:
        return False
    expected = run_benchmark(
        {"items": []}, datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    return benchmark.get("metrics") == expected.get("metrics")


def run_benchmark(reviews: dict[str, Any], at: datetime) -> dict[str, Any]:
    initialize_review_state(reviews)
    profile, fingerprint = _profile()
    errors: list[float] = []
    interval_checks: list[bool] = []
    failure_checks: list[bool] = []
    for case in profile.get("cases", []):
        state: dict[str, Any] | None = None
        prior_interval: int | None = None
        for index, raw in enumerate(case.get("events", [])):
            event = {
                "atom_id": case["atom_id"],
                "assessed_at": raw["at"],
                "correctness": float(raw["correctness"]),
                "hint_count": int(raw.get("hint_count", 0)),
                "recalled": bool(raw["recalled"]),
            }
            before_stability = float(state["stability_days"]) if state else None
            state, prediction = apply_qualified_event(state, event, float(profile["desired_retention"]))
            if prediction is not None:
                errors.append((prediction - float(event["recalled"])) ** 2)
            interval = int(state["suggested_interval_days"])
            if prior_interval is not None and event["recalled"]:
                interval_checks.append(interval >= prior_interval)
            if before_stability is not None and not event["recalled"]:
                failure_checks.append(float(state["stability_days"]) < before_stability)
            prior_interval = interval
    probe = {
        "atom_id": "benchmark.response-time",
        "assessed_at": "2026-01-01T00:00:00+00:00",
        "correctness": 0.9,
        "hint_count": 0,
        "recalled": True,
    }
    fast, _ = apply_qualified_event(None, {**probe, "response_time_bucket": "under_30s"}, 0.9)
    slow, _ = apply_qualified_event(None, {**probe, "response_time_bucket": "over_300s"}, 0.9)
    brier = round(sum(errors) / len(errors), 6) if errors else None
    metrics = {
        "evaluated_predictions": len(errors),
        "brier_score": brier,
        "successful_interval_monotonicity": round(sum(interval_checks) / len(interval_checks), 6) if interval_checks else None,
        "failure_shortening_rate": round(sum(failure_checks) / len(failure_checks), 6) if failure_checks else None,
        "response_time_invariant": fast == slow,
        "bounded_state": all(
            1 <= float(value["difficulty"]) <= 10 and 0.25 <= float(value["stability_days"]) <= 3650
            for value in [fast, slow]
        ),
    }
    thresholds = profile["thresholds"]
    passed = bool(
        brier is not None
        and brier <= float(thresholds["brier_score_max"])
        and metrics["successful_interval_monotonicity"] >= float(thresholds["successful_interval_monotonicity_min"])
        and metrics["failure_shortening_rate"] >= float(thresholds["failure_shortening_rate_min"])
        and metrics["response_time_invariant"]
        and metrics["bounded_state"]
    )
    report = {
        "profile_id": profile["profile_id"],
        "profile_sha256": fingerprint,
        "model_version": MODEL_VERSION,
        "status": "passed" if passed else "failed",
        "metrics": metrics,
        "run_at": _iso(at),
    }
    reviews["benchmark"] = report
    return report


def configure(reviews: dict[str, Any], payload: dict[str, Any], at: datetime) -> dict[str, Any]:
    initialize_review_state(reviews)
    allowed = {"mode", "desired_retention", "objective", "exam_target_date", "final_review_days", "active_opt_in"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ReviewSchedulerError(f"review policy supports only {sorted(allowed)}")
    current = dict(reviews["policy"])
    current.update(payload)
    mode = current.get("mode")
    if mode not in POLICY_MODES:
        raise ReviewSchedulerError(f"mode must be one of {sorted(POLICY_MODES)}")
    retention = current.get("desired_retention")
    if isinstance(retention, bool) or not isinstance(retention, (int, float)) or not 0.7 <= float(retention) <= 0.97:
        raise ReviewSchedulerError("desired_retention must be between 0.70 and 0.97")
    objective = current.get("objective")
    if objective not in OBJECTIVES:
        raise ReviewSchedulerError(f"objective must be one of {sorted(OBJECTIVES)}")
    target = current.get("exam_target_date")
    if objective == "exam":
        try:
            target_date = date.fromisoformat(str(target))
        except ValueError as exc:
            raise ReviewSchedulerError("exam objective requires exam_target_date in YYYY-MM-DD format") from exc
        if target_date < at.date():
            raise ReviewSchedulerError("exam_target_date cannot be earlier than the policy update date")
        target = target_date.isoformat()
    elif target is not None:
        raise ReviewSchedulerError("exam_target_date must be null for the long_term objective")
    final_days = current.get("final_review_days")
    if isinstance(final_days, bool) or not isinstance(final_days, int) or not 1 <= final_days <= 60:
        raise ReviewSchedulerError("final_review_days must be an integer from 1 to 60")
    opt_in = current.get("active_opt_in")
    if not isinstance(opt_in, bool):
        raise ReviewSchedulerError("active_opt_in must be boolean")
    if mode == "adaptive-active" and not opt_in:
        raise ReviewSchedulerError("adaptive-active requires active_opt_in: true")
    if mode == "adaptive-active" and not benchmark_current(reviews):
        raise ReviewSchedulerError("adaptive-active requires a current passing `review benchmark` report")
    if mode != "adaptive-active":
        opt_in = False
    current.update(
        {
            "desired_retention": round(float(retention), 3),
            "exam_target_date": target,
            "active_opt_in": opt_in,
            "model_version": MODEL_VERSION,
            "updated_at": _iso(at),
        }
    )
    reviews["policy"] = current
    for atom_id, state in reviews["memory"].items():
        state["scheduler"] = "fixed" if mode == "fixed" else "adaptive"
        state["desired_retention"] = current["desired_retention"]
        interval = _interval_for(float(state["stability_days"]), current["desired_retention"])
        state["suggested_interval_days"] = interval
        last = _parse_time(state["last_qualified_review_at"], f"memory.{atom_id}.last_qualified_review_at")
        state["suggested_due_at"] = _iso(last + timedelta(days=interval))
    return {"policy": current, "benchmark_current": benchmark_current(reviews)}


def schedule_choice(
    reviews: dict[str, Any], atom_id: str, base: datetime, fixed_interval_days: int | None
) -> dict[str, Any] | None:
    initialize_review_state(reviews)
    policy = reviews["policy"]
    mode = policy["mode"]
    state = reviews["memory"].get(atom_id)
    adaptive_days = int(state["suggested_interval_days"]) if state else None
    fixed_due = base + timedelta(days=fixed_interval_days) if fixed_interval_days is not None else None
    adaptive_due = base + timedelta(days=adaptive_days) if adaptive_days is not None else None
    if adaptive_due is not None and policy["objective"] == "exam":
        target = datetime.combine(date.fromisoformat(policy["exam_target_date"]), time.min, timezone.utc)
        if target <= base:
            adaptive_due = None
            adaptive_days = None
        else:
            final_start = target - timedelta(days=int(policy["final_review_days"]))
            boundary = final_start if base < final_start else target
            adaptive_due = min(adaptive_due, boundary)
            adaptive_days = max(1, math.ceil((adaptive_due - base).total_seconds() / 86400))
    if mode == "fixed" or mode == "adaptive-shadow" or state is None:
        if fixed_due is None:
            return None
        actual_due = fixed_due
        actual_days = fixed_interval_days
        effective = "fixed"
    else:
        if not policy.get("active_opt_in") or not benchmark_current(reviews):
            raise ReviewSchedulerError("adaptive-active scheduling gate is no longer valid")
        if adaptive_due is None:
            return None
        actual_due = adaptive_due
        actual_days = adaptive_days
        effective = "adaptive"
    return {
        "scheduler_mode": mode,
        "effective_scheduler": effective,
        "interval_days": int(actual_days),
        "due_at": _iso(actual_due),
        "fixed_interval_days": fixed_interval_days,
        "fixed_due_at": _iso(fixed_due) if fixed_due else None,
        "adaptive_interval_days": adaptive_days,
        "adaptive_due_at": _iso(adaptive_due) if adaptive_due else None,
        "model_version": MODEL_VERSION if state else None,
    }


def link_scheduled_event(reviews: dict[str, Any], atom_id: str, review_id: str, choice: dict[str, Any]) -> None:
    for event in reversed(reviews.get("events", [])):
        if event.get("atom_id") == atom_id and event.get("qualified") and event.get("scheduled_review_id") is None:
            event["fixed_interval_days"] = choice.get("fixed_interval_days")
            event["adaptive_interval_days"] = choice.get("adaptive_interval_days")
            event["scheduled_review_id"] = review_id
            return


def validate_state(
    reviews: dict[str, Any], atom_ids: set[str], evidence_by_id: dict[str, dict[str, Any]]
) -> list[str]:
    if not any(key in reviews for key in ["policy", "memory", "events", "benchmark"]):
        return []
    errors: list[str] = []
    policy = reviews.get("policy")
    if not isinstance(policy, dict) or set(policy) != set(default_policy()):
        errors.append("reviews.policy fields are invalid")
    else:
        if policy.get("mode") not in POLICY_MODES:
            errors.append("reviews.policy.mode is invalid")
        retention = policy.get("desired_retention")
        if isinstance(retention, bool) or not isinstance(retention, (int, float)) or not 0.7 <= float(retention) <= 0.97:
            errors.append("reviews.policy.desired_retention is invalid")
        if policy.get("objective") not in OBJECTIVES:
            errors.append("reviews.policy.objective is invalid")
        if not isinstance(policy.get("active_opt_in"), bool):
            errors.append("reviews.policy.active_opt_in must be boolean")
        if policy.get("model_version") != MODEL_VERSION:
            errors.append("reviews.policy.model_version is invalid")
        if policy.get("objective") == "exam":
            try:
                date.fromisoformat(str(policy.get("exam_target_date")))
            except ValueError:
                errors.append("reviews.policy.exam_target_date is invalid")
        elif policy.get("exam_target_date") is not None:
            errors.append("reviews.policy long_term objective cannot retain an exam target")
        if policy.get("mode") == "adaptive-active" and (not policy.get("active_opt_in") or not benchmark_current(reviews)):
            errors.append("reviews.policy adaptive-active gate is invalid")
    memory = reviews.get("memory")
    if not isinstance(memory, dict):
        errors.append("reviews.memory must be a mapping")
        memory = {}
    required_memory = {
        "atom_id", "scheduler", "stability_days", "retrievability", "difficulty", "desired_retention",
        "last_qualified_review_at", "model_version", "qualified_event_count", "suggested_interval_days", "suggested_due_at",
    }
    for atom_id, state in memory.items():
        if atom_id not in atom_ids or not isinstance(state, dict) or set(state) != required_memory:
            errors.append(f"reviews.memory.{atom_id} fields or Atom reference are invalid")
            continue
        try:
            _parse_time(state["last_qualified_review_at"], f"reviews.memory.{atom_id}.last_qualified_review_at")
            _parse_time(state["suggested_due_at"], f"reviews.memory.{atom_id}.suggested_due_at")
        except ReviewSchedulerError as exc:
            errors.append(str(exc))
        if not _number_between(state.get("difficulty"), 1, 10) or not _number_between(state.get("stability_days"), 0.25, 3650):
            errors.append(f"reviews.memory.{atom_id} has out-of-bounds D/S state")
        if state.get("model_version") != MODEL_VERSION or state.get("scheduler") not in {"fixed", "adaptive"}:
            errors.append(f"reviews.memory.{atom_id} has an invalid scheduler or model version")
        if not _number_between(state.get("retrievability"), 0, 1):
            errors.append(f"reviews.memory.{atom_id}.retrievability is invalid")
        if not _number_between(state.get("desired_retention"), 0.7, 0.97):
            errors.append(f"reviews.memory.{atom_id}.desired_retention is invalid")
        interval = state.get("suggested_interval_days")
        if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 3650:
            errors.append(f"reviews.memory.{atom_id}.suggested_interval_days is invalid")
        if not isinstance(state.get("qualified_event_count"), int) or state.get("qualified_event_count", 0) < 1:
            errors.append(f"reviews.memory.{atom_id}.qualified_event_count is invalid")
    events = reviews.get("events")
    if not isinstance(events, list):
        errors.append("reviews.events must be a list")
        events = []
    seen_ids: set[str] = set()
    seen_evidence: set[str] = set()
    seen_scheduled: set[str] = set()
    review_by_id = {
        item.get("id"): item for item in reviews.get("items", []) if isinstance(item, dict)
    }
    event_keys = {
        "id", "atom_id", "evidence_id", "assessed_at", "correctness", "recalled",
        "required_dimension_minimum", "hint_count", "delayed", "retrieval_mode",
        "response_time_bucket", "scorer_quality", "qualified", "ineligibility_reasons",
        "prediction_before", "fixed_interval_days", "adaptive_interval_days", "scheduled_review_id",
    }
    for event in events:
        if not isinstance(event, dict):
            errors.append("reviews.events contains a non-mapping")
            continue
        if set(event) != event_keys:
            errors.append(f"{event.get('id')} normalized review event fields are invalid")
            continue
        if event.get("id") in seen_ids or event.get("evidence_id") in seen_evidence:
            errors.append("reviews.events contains a duplicate ID or Evidence link")
        seen_ids.add(event.get("id"))
        seen_evidence.add(event.get("evidence_id"))
        evidence = evidence_by_id.get(event.get("evidence_id"))
        if event.get("atom_id") not in atom_ids or evidence is None:
            errors.append(f"{event.get('id')} references a missing Atom or Evidence")
            continue
        if evidence.get("atom_id") != event.get("atom_id") or evidence.get("kind") != "review" or evidence.get("result") == "pending":
            errors.append(f"{event.get('id')} does not match assessed review Evidence")
        if evidence.get("review_event_id") is not None and evidence.get("review_event_id") != event.get("id"):
            errors.append(f"{event.get('id')} disagrees with its Evidence review_event_id")
        try:
            _parse_time(event.get("assessed_at"), f"{event.get('id')}.assessed_at")
        except ReviewSchedulerError as exc:
            errors.append(str(exc))
        if event.get("assessed_at") != evidence.get("assessed_at"):
            errors.append(f"{event.get('id')}.assessed_at disagrees with Evidence")
        for field in ["correctness", "required_dimension_minimum"]:
            value = event.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                errors.append(f"{event.get('id')}.{field} is invalid")
        if not isinstance(event.get("recalled"), bool) or event.get("recalled") != (evidence.get("result") == "mastered"):
            errors.append(f"{event.get('id')}.recalled disagrees with Evidence")
        basis = _event_basis(evidence)
        for field in [
            "correctness", "required_dimension_minimum", "hint_count", "delayed",
            "retrieval_mode", "response_time_bucket", "scorer_quality", "qualified",
            "ineligibility_reasons",
        ]:
            if event.get(field) != basis[field]:
                errors.append(f"{event.get('id')}.{field} disagrees with Evidence normalization")
        prediction = event.get("prediction_before")
        if prediction is not None and (
            isinstance(prediction, bool) or not isinstance(prediction, (int, float)) or not 0 <= float(prediction) <= 1
        ):
            errors.append(f"{event.get('id')}.prediction_before is invalid")
        reasons = event.get("ineligibility_reasons")
        if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
            errors.append(f"{event.get('id')}.ineligibility_reasons is invalid")
        scheduled_id = event.get("scheduled_review_id")
        if scheduled_id is not None and (
            scheduled_id not in review_by_id or review_by_id[scheduled_id].get("atom_id") != event.get("atom_id")
        ):
            errors.append(f"{event.get('id')} references an invalid scheduled review")
        elif scheduled_id is not None:
            scheduled = review_by_id[scheduled_id]
            if scheduled_id in seen_scheduled:
                errors.append(f"{scheduled_id} is linked from more than one review event")
            seen_scheduled.add(scheduled_id)
            if (
                event.get("fixed_interval_days") != scheduled.get("fixed_interval_days")
                or event.get("adaptive_interval_days") != scheduled.get("adaptive_interval_days")
            ):
                errors.append(f"{event.get('id')} interval audit disagrees with its scheduled review")
        if not event.get("qualified") and any(
            event.get(field) is not None
            for field in ["prediction_before", "fixed_interval_days", "adaptive_interval_days", "scheduled_review_id"]
        ):
            errors.append(f"{event.get('id')} ineligible event contains adaptive scheduling state")
        for field in ["fixed_interval_days", "adaptive_interval_days"]:
            value = event.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                errors.append(f"{event.get('id')}.{field} is invalid")
    for evidence_id, evidence in evidence_by_id.items():
        linked = evidence.get("review_event_id")
        if linked is not None and (evidence.get("kind") != "review" or linked not in seen_ids or evidence_id not in seen_evidence):
            errors.append(f"Evidence {evidence_id} has an invalid review_event_id")
    qualified_by_atom: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if isinstance(event, dict) and event.get("qualified"):
            qualified_by_atom[str(event.get("atom_id"))].append(event)
    for atom_id, state in memory.items():
        qualified = qualified_by_atom.get(atom_id, [])
        stored_count = state.get("qualified_event_count")
        if not isinstance(stored_count, int) or isinstance(stored_count, bool) or stored_count != len(qualified):
            errors.append(f"reviews.memory.{atom_id} qualified event count disagrees with history")
        def replay_time(item: dict[str, Any]) -> datetime:
            try:
                return _parse_time(item.get("assessed_at"), f"{item.get('id')}.assessed_at")
            except ReviewSchedulerError:
                return datetime.min.replace(tzinfo=timezone.utc)

        valid_events = [item for item in qualified if replay_time(item) != datetime.min.replace(tzinfo=timezone.utc)]
        if valid_events:
            latest = max(valid_events, key=replay_time)
            if state.get("last_qualified_review_at") != latest.get("assessed_at"):
                errors.append(f"reviews.memory.{atom_id} last qualified review disagrees with history")

        replayed: dict[str, Any] | None = None
        for event in sorted(qualified, key=replay_time):
            if (
                not _number_between(event.get("correctness"), 0, 1)
                or not isinstance(event.get("recalled"), bool)
                or not isinstance(event.get("hint_count"), int)
                or isinstance(event.get("hint_count"), bool)
                or replay_time(event) == datetime.min.replace(tzinfo=timezone.utc)
            ):
                continue
            replayed, expected_prediction = apply_qualified_event(
                replayed, event, float(reviews["policy"]["desired_retention"])
            )
            if event.get("prediction_before") != expected_prediction:
                errors.append(f"{event.get('id')}.prediction_before disagrees with replay")
        if replayed is not None:
            replayed["scheduler"] = "fixed" if reviews["policy"]["mode"] == "fixed" else "adaptive"
            if state != replayed:
                errors.append(f"reviews.memory.{atom_id} disagrees with deterministic event replay")
    for atom_id in qualified_by_atom:
        if atom_id not in memory:
            errors.append(f"qualified review history for {atom_id} has no memory state")
    benchmark = reviews.get("benchmark")
    if not isinstance(benchmark, dict) or set(benchmark) != set(default_benchmark()):
        errors.append("reviews.benchmark fields are invalid")
    elif benchmark.get("status") not in {"not_run", "passed", "failed"}:
        errors.append("reviews.benchmark.status is invalid")
    elif benchmark.get("profile_id") != "memory-core-v1" or benchmark.get("model_version") != MODEL_VERSION:
        errors.append("reviews.benchmark identity is invalid")
    elif benchmark.get("status") == "passed" and not benchmark_current(reviews):
        errors.append("reviews.benchmark passing result is stale or inconsistent")
    return errors


def status(reviews: dict[str, Any], at: datetime) -> dict[str, Any]:
    initialize_review_state(reviews)
    states = []
    for atom_id, state in sorted(reviews["memory"].items()):
        states.append({**state, "retrievability_now": retrievability(state, at)})
    qualified = [event for event in reviews["events"] if event.get("qualified")]
    return {
        "policy": reviews["policy"],
        "benchmark": reviews["benchmark"],
        "benchmark_current": benchmark_current(reviews),
        "memory_atom_count": len(states),
        "qualified_event_count": len(qualified),
        "ineligible_event_count": len(reviews["events"]) - len(qualified),
        "memory": states,
        "as_of": _iso(at),
    }


def _latest_result(workspace: Any, atom_id: str) -> str | None:
    assessed = [
        item for item in workspace.evidence.get("items", [])
        if item.get("atom_id") == atom_id and item.get("result") in {"mastered", "partial", "not_mastered"}
    ]
    return assessed[-1].get("result") if assessed else None


def daily_queue(
    workspace: Any,
    day: date,
    available_minutes: int,
    max_new_atoms: int,
    cognitive_load_limit: int,
    exam_practice_limit: int,
) -> dict[str, Any]:
    if not 5 <= available_minutes <= 1440:
        raise ReviewSchedulerError("available_minutes must be between 5 and 1440")
    if not 0 <= max_new_atoms <= 20 or not 1 <= cognitive_load_limit <= 100 or not 0 <= exam_practice_limit <= 20:
        raise ReviewSchedulerError("queue limits are out of range")
    end = datetime.combine(day, time.max, timezone.utc)
    candidates: list[dict[str, Any]] = []
    active_id = workspace.current.get("active_atom_id")
    if active_id:
        latest = _latest_result(workspace, active_id)
        candidates.append(
            {
                "category": "failure_remediation",
                "atom_id": active_id,
                "title": workspace.atoms[active_id]["title"],
                "minutes": int(workspace.atoms[active_id].get("estimated_minutes", 20)),
                "cognitive_load": 3,
                "priority": 120 if latest in {"partial", "not_mastered"} else 105,
                "reason": latest or "resume_active_atom",
            }
        )
    due_atom_ids: set[str] = set()
    for item in workspace.reviews.get("items", []):
        if item.get("status") != "pending":
            continue
        due = _parse_time(item["due_at"], f"review {item.get('id')} due_at")
        if due > end:
            continue
        atom_id = item["atom_id"]
        due_atom_ids.add(atom_id)
        delay = max(0, (end.date() - due.date()).days)
        candidates.append(
            {
                "category": "due_review",
                "atom_id": atom_id,
                "review_id": item["id"],
                "title": workspace.atoms[atom_id]["title"],
                "minutes": int(workspace.atoms[atom_id].get("review_minutes", 12)),
                "cognitive_load": 2,
                "priority": 110 + min(delay, 30),
                "delay_days": delay,
                "reason": "overdue_review" if delay else "review_due_today",
            }
        )
    blocking: dict[str, int] = defaultdict(int)
    for atom in workspace.atoms.values():
        if atom.get("status") != "locked" or atom.get("optional"):
            continue
        for prerequisite in atom.get("prerequisites", []):
            if workspace.atoms.get(prerequisite, {}).get("status") == "available":
                blocking[prerequisite] += 1
    for atom_id, downstream_count in blocking.items():
        if atom_id in due_atom_ids or atom_id == active_id:
            continue
        candidates.append(
            {
                "category": "blocking_prerequisite",
                "atom_id": atom_id,
                "title": workspace.atoms[atom_id]["title"],
                "minutes": int(workspace.atoms[atom_id].get("estimated_minutes", 25)),
                "cognitive_load": 3,
                "priority": 95 + min(downstream_count, 5),
                "blocked_atom_count": downstream_count,
                "reason": "unblocks_required_path",
            }
        )
    available = [
        atom for atom in workspace.atoms.values()
        if atom.get("status") == "available" and atom["id"] not in blocking and not atom.get("optional")
    ]
    available.sort(key=lambda item: (len(item.get("prerequisites", [])), item["id"]))
    for atom in available[:max_new_atoms]:
        candidates.append(
            {
                "category": "new_atom",
                "atom_id": atom["id"],
                "title": atom["title"],
                "minutes": int(atom.get("estimated_minutes", 25)),
                "cognitive_load": 3,
                "priority": 75,
                "reason": "eligible_new_atom",
            }
        )
    exam_warnings: list[str] = []
    exam_root = workspace.meta / "exam"
    if exam_practice_limit and exam_root.is_dir():
        try:
            from exam import ExamEngine

            plan = ExamEngine.load(str(workspace.root)).plan("mixed", exam_practice_limit)
            exam_warnings = plan.get("warnings", [])
            for item in plan.get("queue", []):
                if not item.get("representative_question_ids"):
                    continue
                candidates.append(
                    {
                        "category": "exam_practice",
                        "atom_id": item["atom_id"],
                        "title": item["title"],
                        "question_ids": item["representative_question_ids"],
                        "minutes": 18,
                        "cognitive_load": 3,
                        "priority": 70 + round(10 * float(item["priority_score"])),
                        "reason": "exam_weighted_practice",
                    }
                )
        except Exception as exc:  # Keep the course queue usable while reporting the isolated exam issue.
            exam_warnings.append(f"Exam practice was not merged: {exc}")
    candidates.sort(key=lambda item: (-item["priority"], item["category"], item.get("atom_id", "")))
    scheduled: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    used_minutes = 0
    used_load = 0
    for item in candidates:
        if used_minutes + item["minutes"] <= available_minutes and used_load + item["cognitive_load"] <= cognitive_load_limit:
            scheduled.append(item)
            used_minutes += item["minutes"]
            used_load += item["cognitive_load"]
        else:
            unscheduled.append({**item, "unscheduled_reason": "time_or_cognitive_capacity"})
    overdue = sum(item.get("delay_days", 0) > 0 for item in candidates if item["category"] == "due_review")
    return {
        "date": day.isoformat(),
        "available_minutes": available_minutes,
        "planned_minutes": used_minutes,
        "remaining_minutes": available_minutes - used_minutes,
        "cognitive_load_limit": cognitive_load_limit,
        "planned_cognitive_load": used_load,
        "behind_schedule": bool(overdue or unscheduled),
        "overdue_review_count": overdue,
        "tasks": scheduled,
        "unscheduled_tasks": unscheduled,
        "category_counts": dict(sorted(Counter(item["category"] for item in scheduled).items())),
        "exam_warnings": exam_warnings,
        "invariants": [
            "This queue is read-only: it does not mark Evidence, mastery, or reviews complete.",
            "Overdue history is retained even when capacity forces a task into the backlog.",
            "Blocking prerequisites and failure remediation outrank new material.",
        ],
    }


def pilot_report(reviews: dict[str, Any], min_events: int) -> dict[str, Any]:
    initialize_review_state(reviews)
    if not 2 <= min_events <= 10000:
        raise ReviewSchedulerError("min_events must be between 2 and 10000")
    events = [event for event in reviews["events"] if event.get("qualified")]
    atoms = {event["atom_id"] for event in events}
    scored = [event for event in events if event.get("prediction_before") is not None]
    brier = (
        round(statistics.fmean((float(event["prediction_before"]) - float(event["recalled"])) ** 2 for event in scored), 6)
        if scored else None
    )
    fixed = [int(event["fixed_interval_days"]) for event in events if event.get("fixed_interval_days")]
    adaptive = [int(event["adaptive_interval_days"]) for event in events if event.get("adaptive_interval_days")]
    sufficient = len(events) >= min_events and len(atoms) >= 2 and len(scored) >= max(1, min_events // 2)
    return {
        "status": "reportable" if sufficient else "insufficient",
        "design": "workspace_observational_shadow_replay",
        "qualified_event_count": len(events),
        "prediction_count": len(scored),
        "atom_count": len(atoms),
        "minimum_event_count": min_events,
        "candidate_brier_score": brier,
        "fixed_baseline": {
            "policy": "1/3/7/30_days",
            "scheduled_event_count": len(fixed),
            "mean_interval_days": round(statistics.fmean(fixed), 3) if fixed else None,
        },
        "adaptive_shadow": {
            "model_version": MODEL_VERSION,
            "suggested_event_count": len(adaptive),
            "mean_interval_days": round(statistics.fmean(adaptive), 3) if adaptive else None,
        },
        "limitations": [
            "This replay is observational and does not establish a causal learning benefit.",
            "Only qualified active-retrieval Evidence is included; passive review and legacy Evidence are excluded.",
            "Run a consented, pre-registered learning study before claiming superiority over the fixed baseline.",
        ],
        "promotion_allowed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage qualified Atom memory and a unified daily review queue")
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status", help="Show policy, benchmark, and current per-Atom memory state")
    status_parser.add_argument("workspace")
    status_parser.add_argument("--now")
    configure_parser = sub.add_parser("configure", help="Set fixed, adaptive-shadow, or gated adaptive-active scheduling")
    configure_parser.add_argument("workspace")
    configure_parser.add_argument("--input", required=True)
    configure_parser.add_argument("--expected-revision", type=int)
    benchmark_parser = sub.add_parser("benchmark", help="Run the bundled deterministic memory-adapter gate")
    benchmark_parser.add_argument("workspace")
    benchmark_parser.add_argument("--now")
    benchmark_parser.add_argument("--expected-revision", type=int)
    queue_parser = sub.add_parser("queue", help="Build one read-only capacity-aware daily learning queue")
    queue_parser.add_argument("workspace")
    queue_parser.add_argument("--date")
    queue_parser.add_argument("--minutes", type=int, default=60)
    queue_parser.add_argument("--max-new-atoms", type=int, default=1)
    queue_parser.add_argument("--cognitive-load", type=int, default=10)
    queue_parser.add_argument("--exam-practice-limit", type=int, default=2)
    pilot_parser = sub.add_parser("pilot", help="Compare fixed and adaptive-shadow recommendations on qualified workspace history")
    pilot_parser.add_argument("workspace")
    pilot_parser.add_argument("--min-events", type=int, default=12)
    return parser


def _command_time(value: str | None) -> datetime:
    return _parse_time(value, "--now") if value else datetime.now(timezone.utc).replace(microsecond=0)


def run(argv: list[str] | None = None) -> None:
    from atomlearn import load_workspace

    args = build_parser().parse_args(argv)
    workspace = load_workspace(args.workspace)
    errors = workspace.validate()
    if errors:
        raise ReviewSchedulerError("Cannot use review scheduling in an invalid workspace:\n- " + "\n- ".join(errors))
    initialize_review_state(workspace.reviews)
    if args.command == "status":
        result = status(workspace.reviews, _command_time(args.now))
    elif args.command == "queue":
        try:
            queue_date = date.fromisoformat(args.date) if args.date else date.today()
        except ValueError as exc:
            raise ReviewSchedulerError("--date must use YYYY-MM-DD") from exc
        result = daily_queue(
            workspace, queue_date, args.minutes, args.max_new_atoms, args.cognitive_load, args.exam_practice_limit
        )
    elif args.command == "pilot":
        result = pilot_report(workspace.reviews, args.min_events)
    else:
        workspace.expect_revision(args.expected_revision)
        if args.command == "benchmark":
            at = _command_time(args.now)
            report = run_benchmark(workspace.reviews, at)
            workspace.commit("reviews.benchmarked", "Ran the versioned memory-adapter benchmark gate", report, at)
            result = {"ok": True, "revision": workspace.revision, **report}
        elif args.command == "configure":
            at = datetime.now(timezone.utc).replace(microsecond=0)
            configured = configure(workspace.reviews, _read(Path(args.input)), at)
            workspace.commit("reviews.configured", "Configured bounded per-Atom review scheduling", configured, at)
            result = {"ok": True, "revision": workspace.revision, **configured}
        else:  # pragma: no cover
            raise ReviewSchedulerError(f"Unhandled review command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        run()
        return 0
    except ReviewSchedulerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
