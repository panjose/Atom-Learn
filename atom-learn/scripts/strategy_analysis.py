#!/usr/bin/env python3
"""Deterministic stratified analysis for conservative teaching-strategy experiments."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any


ANALYSIS_VERSION = "strategy-bootstrap-v1"
LEARNING_METRICS = {
    "immediate_mastery_score": "immediate_mastery",
    "delayed_retention_score": "delayed_retention",
    "near_transfer_score": "near_transfer",
    "far_transfer_score": "far_transfer",
}
PROCESS_METRICS = {"mastery_attempts", "blocking_backtrack_rate"}
UX_METRICS = {"override_rate"}
GUARDRAIL_METRICS = {"misconception_recurrence", "mastery_failure_rate"}


class StrategyAnalysisError(RuntimeError):
    """An experiment cannot be analyzed under its preregistered contract."""


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _metric_value(metric: str, outcome: dict[str, Any]) -> float | None:
    if metric in LEARNING_METRICS:
        return float(outcome["score"]) if outcome.get("measurement_kind") == LEARNING_METRICS[metric] else None
    if metric == "mastery_attempts":
        return float(outcome["attempts"])
    if metric == "blocking_backtrack_rate":
        return float(bool(outcome["blocking_backtrack"]))
    if metric == "misconception_recurrence":
        if outcome.get("measurement_kind") != "delayed_retention":
            return None
        return float(outcome["result"] != "mastered")
    if metric == "mastery_failure_rate":
        return float(outcome["result"] != "mastered")
    raise StrategyAnalysisError(f"Unsupported outcome metric: {metric}")


def _bootstrap_difference(
    samples: dict[str, dict[str, list[float]]],
    *,
    beneficial_direction: int,
    seed: int,
    resamples: int,
    confidence_level: float,
) -> dict[str, Any]:
    values_by_arm = {
        arm: [value for stratum in sorted(samples) for value in samples[stratum].get(arm, [])]
        for arm in ["baseline", "candidate"]
    }
    baseline = _mean(values_by_arm["baseline"])
    candidate = _mean(values_by_arm["candidate"])
    effect = None
    if baseline is not None and candidate is not None:
        effect = beneficial_direction * (candidate - baseline)
    complete_strata = [
        stratum
        for stratum, arms in samples.items()
        if arms.get("baseline") and arms.get("candidate")
    ]
    distribution: list[float] = []
    if complete_strata:
        generator = random.Random(seed)
        for _ in range(resamples):
            drawn: dict[str, list[float]] = {"baseline": [], "candidate": []}
            for stratum in sorted(complete_strata):
                for arm in ["baseline", "candidate"]:
                    source = samples[stratum][arm]
                    drawn[arm].extend(generator.choice(source) for _ in range(len(source)))
            base_mean = _mean(drawn["baseline"])
            candidate_mean = _mean(drawn["candidate"])
            if base_mean is not None and candidate_mean is not None:
                distribution.append(beneficial_direction * (candidate_mean - base_mean))
    tail = (1 - confidence_level) / 2
    lower = _percentile(distribution, tail)
    upper = _percentile(distribution, 1 - tail)
    return {
        "baseline": None if baseline is None else round(baseline, 6),
        "candidate": None if candidate is None else round(candidate, 6),
        "effect": None if effect is None else round(effect, 6),
        "interval": {
            "level": confidence_level,
            "lower": None if lower is None else round(lower, 6),
            "upper": None if upper is None else round(upper, 6),
        },
        "samples": {arm: len(values_by_arm[arm]) for arm in ["baseline", "candidate"]},
        "strata": len(complete_strata),
    }


def _outcome_metric(
    metric: str,
    by_stratum_arm: dict[str, dict[str, list[dict[str, Any]]]],
    analysis: dict[str, Any],
    seed_offset: int,
) -> dict[str, Any]:
    samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for stratum, arms in by_stratum_arm.items():
        for arm, outcomes in arms.items():
            for outcome in outcomes:
                value = _metric_value(metric, outcome)
                if value is not None:
                    samples[stratum][arm].append(value)
    beneficial_direction = -1 if metric == "mastery_attempts" else 1
    return _bootstrap_difference(
        samples,
        beneficial_direction=beneficial_direction,
        seed=int(analysis["seed"]) + seed_offset,
        resamples=int(analysis["bootstrap_resamples"]),
        confidence_level=float(analysis["confidence_level"]),
    )


def _override_metric(exposures: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, list[float]] = {"baseline": [], "candidate": []}
    for exposure in exposures:
        assigned = exposure.get("assigned_arm")
        if assigned not in values:
            arm = exposure.get("arm")
            assigned = arm if arm in values else None
        if assigned in values:
            values[assigned].append(float(exposure.get("status") == "overridden"))
    baseline = _mean(values["baseline"])
    candidate = _mean(values["candidate"])
    delta = None if baseline is None or candidate is None else candidate - baseline
    return {
        "baseline": None if baseline is None else round(baseline, 6),
        "candidate": None if candidate is None else round(candidate, 6),
        "candidate_minus_baseline": None if delta is None else round(delta, 6),
        "samples": {arm: len(values[arm]) for arm in ["baseline", "candidate"]},
        "promotion_eligible": False,
    }


def analyze(
    experiment: dict[str, Any],
    exposures: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a deterministic preregistered analysis; never mutate input records."""
    live = {
        item["id"]: item
        for item in exposures
        if item.get("experiment_id") == experiment["id"] and item.get("status") == "exposed"
    }
    experiment_outcomes = [item for item in outcomes if item.get("experiment_id") == experiment["id"]]
    eligible = [
        item
        for item in experiment_outcomes
        if item.get("outcome_eligible") is True and item.get("exposure_id") in live
    ]
    strata_arms: dict[str, set[str]] = defaultdict(set)
    for item in eligible:
        exposure = live[item["exposure_id"]]
        if exposure.get("arm") in {"baseline", "candidate"}:
            strata_arms[exposure["stratum"]].add(exposure["arm"])
    comparable_strata = sorted(stratum for stratum, arms in strata_arms.items() if arms == {"baseline", "candidate"})
    comparable = [item for item in eligible if live[item["exposure_id"]]["stratum"] in comparable_strata]
    maximum = int(experiment["analysis"]["max_outcomes_per_arm"])
    by_arm = {
        arm: sorted(
            [item for item in comparable if live[item["exposure_id"]]["arm"] == arm],
            key=lambda item: (item.get("recorded_at", ""), item["id"]),
        )[:maximum]
        for arm in ["baseline", "candidate"]
    }
    window_ids = {item["id"] for items in by_arm.values() for item in items}
    windowed = [item for item in comparable if item["id"] in window_ids]
    by_stratum_arm: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in windowed:
        exposure = live[item["exposure_id"]]
        by_stratum_arm[exposure["stratum"]][exposure["arm"]].append(item)
    metric_layers: dict[str, Any] = {"learning": {}, "process": {}, "ux": {}, "guardrails": {}}
    offset = 0
    for layer in ["learning", "process", "guardrails"]:
        for metric in experiment["metrics"][layer]:
            metric_layers[layer][metric] = _outcome_metric(
                metric, by_stratum_arm, experiment["analysis"], offset
            )
            offset += 100003
    for metric in experiment["metrics"]["ux"]:
        if metric != "override_rate":
            raise StrategyAnalysisError(f"Unsupported UX metric: {metric}")
        metric_layers["ux"][metric] = _override_metric(
            [item for item in exposures if item.get("experiment_id") == experiment["id"]]
        )
    samples = {
        "baseline": len(by_arm["baseline"]),
        "candidate": len(by_arm["candidate"]),
        "distinct_episodes": len({live[item["exposure_id"]]["episode_ref"] for item in windowed}),
        "delayed_by_arm": {
            arm: sum(item.get("measurement_kind") == "delayed_retention" for item in items)
            for arm, items in by_arm.items()
        },
        "transfer_or_delayed_by_arm": {
            arm: sum(
                item.get("measurement_kind") in {"delayed_retention", "near_transfer", "far_transfer"}
                for item in items
            )
            for arm, items in by_arm.items()
        },
        "excluded_ineligible": len(experiment_outcomes) - len(eligible),
        "excluded_unmatched_strata": len(eligible) - len(comparable),
        "window_limit_per_arm": maximum,
        "window_reached": all(len(by_arm[arm]) >= maximum for arm in ["baseline", "candidate"]),
    }
    hard_gates = {
        "invalid_workspace_outcomes": sum(not item.get("workspace_valid", False) for item in experiment_outcomes),
        "unlinked_or_nonlive_outcomes": sum(item.get("exposure_id") not in live for item in experiment_outcomes),
        "linkage_mismatches": sum(
            item.get("exposure_id") in live
            and (
                item.get("atom_ref") != live[item["exposure_id"]].get("atom_ref")
                or item.get("episode_ref") != live[item["exposure_id"]].get("episode_ref")
            )
            for item in experiment_outcomes
        ),
        "duplicate_evidence_links": len(experiment_outcomes)
        - len({item.get("evidence_ref") for item in experiment_outcomes}),
    }
    return {
        "analysis_version": experiment["analysis"]["version"],
        "method": experiment["analysis"]["method"],
        "seed": experiment["analysis"]["seed"],
        "bootstrap_resamples": experiment["analysis"]["bootstrap_resamples"],
        "confidence_level": experiment["analysis"]["confidence_level"],
        "comparable_strata": comparable_strata,
        "samples": samples,
        "metric_layers": metric_layers,
        "hard_gates": hard_gates,
    }
