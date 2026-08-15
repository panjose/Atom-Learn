#!/usr/bin/env python3
"""Conservative, opt-in teaching-strategy experiments for AtomLearn."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator

from atomlearn import iso, load_workspace
from effective_policy import POLICY_DIMENSION_CONTEXTS, POLICY_GUIDANCE, POLICY_VALUES, effective_for_workspace
from platform_state import CORE_ROOT, FileLock, PlatformStateError, atomic_text, atomic_yaml, core_version, resolve_user_data_root
from user_profile import UserProfileEngine, UserProfileError, json_lines, load_yaml, serialize_json_lines


STATE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "user-strategy.schema.json"
EXPERIMENT_SCHEMA = CORE_ROOT / "assets" / "schemas" / "strategy-experiment.schema.json"
EXPOSURE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "strategy-exposure.schema.json"
OUTCOME_SCHEMA = CORE_ROOT / "assets" / "schemas" / "strategy-outcome.schema.json"
EXPERIMENT_DIMENSIONS = {
    "explanation.order",
    "example.mode",
    "teaching.mode",
    "feedback.style",
    "check.style",
    "review.presentation",
}
EXPERIMENT_CONTEXTS = {"orientation", "teaching", "review", "exam"}
EPISODE_TYPES = {"new_learning", "remediation", "review"}
QUALITY_METRICS = {"first_transfer_score", "delayed_review_score"}
PRIMARY_METRICS = QUALITY_METRICS | {"mastery_attempts"}
GUARDRAIL_METRICS = {"misconception_recurrence", "blocking_backtrack_rate", "mastery_failure_rate"}
EXPLICIT_SOURCES = {"current_turn", "workspace_explicit", "user_global_explicit"}


class StrategyError(RuntimeError):
    """A user-correctable strategy experiment error."""


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise StrategyError(f"Schema is not an object: {path}")
    return value


def _schema_errors(value: dict[str, Any], path: Path) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(_schema(path)).iter_errors(value), key=lambda item: list(item.path))
    ]


def _digest(prefix: str, *parts: str, length: int = 24) -> str:
    canonical = "\x1f".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(canonical).hexdigest()[:length]


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else None


def _events(path: Path) -> list[dict[str, Any]]:
    return json_lines(path)


def _difficulty_bucket(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    if value <= 1:
        return "introductory"
    if value <= 3:
        return "intermediate"
    return "advanced"


def _diagnostic_bucket(atom: dict[str, Any]) -> str:
    attempts = atom.get("attempts", 0)
    confidence = atom.get("confidence")
    if not isinstance(attempts, int) or attempts <= 0 or not isinstance(confidence, (int, float)):
        return "unassessed"
    if confidence < 0.5:
        return "struggling"
    if confidence < 0.8:
        return "developing"
    return "secure"


class StrategyEngine:
    """User-level strategy state separated from preferences and course truth."""

    def __init__(self, data_root: Path, profile_id: str):
        self.data_root = data_root.resolve(strict=False)
        self.profile_id = profile_id
        self.profile = UserProfileEngine(self.data_root, profile_id)
        self.root = self.data_root / "strategies" / profile_id
        self.state_path = self.root / "state.yaml"
        self.experiments_dir = self.root / "experiments"
        self.exposures_path = self.root / "exposures.ndjson"
        self.outcomes_path = self.root / "outcomes.ndjson"
        self.ledger_path = self.root / "ledger.ndjson"
        self.lock_path = self.root / ".strategy.lock"

    @classmethod
    def at_default_root(
        cls, profile_id: str = "default", data_dir: str | Path | None = None
    ) -> "StrategyEngine":
        return cls(resolve_user_data_root(data_dir, create=False), profile_id)

    @classmethod
    def for_workspace(
        cls, workspace: str | Path, data_dir: str | Path | None = None
    ) -> tuple["StrategyEngine", Any]:
        profile, _ = UserProfileEngine.for_workspace(workspace, data_dir)
        return cls(profile.data_root, profile.profile_id), load_workspace(str(workspace))

    def exists(self) -> bool:
        return self.state_path.is_file()

    def state(self) -> dict[str, Any]:
        if not self.exists():
            raise StrategyError(
                f"Strategy state does not exist for profile {self.profile_id!r}; run strategy enable-experiments first"
            )
        return load_yaml(self.state_path)

    @property
    def revision(self) -> int:
        revision = self.state().get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise StrategyError("strategy revision must be a positive integer")
        return revision

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise StrategyError(
                f"Stale strategy revision: expected {expected}, current is {self.revision}. Reload strategy status."
            )

    def experiments(self) -> list[dict[str, Any]]:
        if not self.experiments_dir.is_dir():
            return []
        return [load_yaml(path) for path in sorted(self.experiments_dir.glob("*.yaml"))]

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        path = self.experiments_dir / f"{experiment_id}.yaml"
        if not path.is_file():
            raise StrategyError(f"Unknown strategy experiment: {experiment_id}")
        return load_yaml(path)

    def exposures(self) -> list[dict[str, Any]]:
        return json_lines(self.exposures_path)

    def outcomes(self) -> list[dict[str, Any]]:
        return json_lines(self.outcomes_path)

    def ledger(self) -> list[dict[str, Any]]:
        return json_lines(self.ledger_path)

    def initialize(self, enabled: bool = True) -> dict[str, Any]:
        if not self.profile.exists() or not self.profile.state().get("global_enabled"):
            raise StrategyError("A globally enabled user profile is required before strategy experiments can be enabled")
        with FileLock(self.lock_path):
            if self.exists():
                state = self.state()
                if enabled and not state["experiments_enabled"]:
                    return self._set_enabled_locked(True, None)
                return state
            version = core_version()
            state = {
                "kind": "atomlearn.user-strategy",
                "schema_version": 1,
                "created_by_core_version": version,
                "last_written_by_core_version": version,
                "min_reader_core_version": version,
                "revision": 1,
                "profile_id": self.profile_id,
                "experiments_enabled": enabled,
                "active": {},
            }
            errors = _schema_errors(state, STATE_SCHEMA)
            if errors:  # pragma: no cover - construction guard
                raise StrategyError("Cannot initialize invalid strategy state:\n- " + "\n- ".join(errors))
            event = self._event(1, "strategy.created", {"experiments_enabled": enabled})
            atomic_text(self.exposures_path, "")
            atomic_text(self.outcomes_path, "")
            self.experiments_dir.mkdir(parents=True, exist_ok=True)
            atomic_text(self.ledger_path, serialize_json_lines([event]))
            atomic_yaml(self.state_path, state)
            return state

    def _event(self, revision: int, event_type: str, details: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": f"strategy-event-{revision:06d}",
            "revision": revision,
            "type": event_type,
            "details": details,
            "at": iso(),
        }

    def _commit_locked(
        self,
        state: dict[str, Any],
        event_type: str,
        details: dict[str, Any],
        *,
        experiment: dict[str, Any] | None = None,
        exposures: list[dict[str, Any]] | None = None,
        outcomes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        current = self.state()
        value = dict(state)
        value["revision"] = current["revision"] + 1
        value["created_by_core_version"] = current["created_by_core_version"]
        value["last_written_by_core_version"] = core_version()
        value["min_reader_core_version"] = current["min_reader_core_version"]
        errors = _schema_errors(value, STATE_SCHEMA)
        if errors:
            raise StrategyError("Refusing to write invalid strategy state:\n- " + "\n- ".join(errors))
        if experiment is not None:
            experiment_errors = _schema_errors(experiment, EXPERIMENT_SCHEMA)
            if experiment_errors:
                raise StrategyError("Refusing to write invalid experiment:\n- " + "\n- ".join(experiment_errors))
            atomic_yaml(self.experiments_dir / f"{experiment['id']}.yaml", experiment)
        if exposures is not None:
            atomic_text(self.exposures_path, serialize_json_lines(exposures))
        if outcomes is not None:
            atomic_text(self.outcomes_path, serialize_json_lines(outcomes))
        ledger = self.ledger()
        ledger.append(self._event(value["revision"], event_type, details))
        atomic_text(self.ledger_path, serialize_json_lines(ledger))
        # State is the commit marker and is intentionally written last.
        atomic_yaml(self.state_path, value)
        return value

    def _set_enabled_locked(self, enabled: bool, expected: int | None) -> dict[str, Any]:
        self.expect_revision(expected)
        state = self.state()
        if state["experiments_enabled"] == enabled:
            return state
        value = dict(state)
        value["experiments_enabled"] = enabled
        return self._commit_locked(
            value,
            "strategy.enabled" if enabled else "strategy.disabled",
            {"experiments_enabled": enabled},
        )

    def set_enabled(self, enabled: bool, expected: int | None = None) -> dict[str, Any]:
        if not self.exists():
            if not enabled:
                raise StrategyError("Strategy state is not initialized")
            return self.initialize(True)
        with FileLock(self.lock_path):
            return self._set_enabled_locked(enabled, expected)

    def _validate_candidate_values(self, experiment: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        dimension = experiment.get("dimension")
        if dimension not in EXPERIMENT_DIMENSIONS:
            errors.append("dimension is not a low-risk strategy dimension")
        allowed = POLICY_VALUES.get(str(dimension), set())
        for arm in ["baseline", "candidate"]:
            if experiment.get(arm) not in allowed:
                errors.append(f"{arm} must be one of: {', '.join(sorted(allowed))}")
        if experiment.get("baseline") == experiment.get("candidate"):
            errors.append("baseline and candidate must differ")
        contexts = experiment.get("contexts", [])
        for context in contexts if isinstance(contexts, list) else []:
            if context not in POLICY_DIMENSION_CONTEXTS.get(str(dimension), set()):
                errors.append(f"dimension {dimension!r} is not permitted in context {context!r}")
        primary = experiment.get("metrics", {}).get("primary", []) if isinstance(experiment.get("metrics"), dict) else []
        if not QUALITY_METRICS.intersection(primary):
            errors.append("at least one quality metric is required; effort or speed alone cannot justify promotion")
        return errors

    def _workspace_reference_ids(self, workspace: Any) -> set[str]:
        result = {str(item.get("id")) for item in workspace.evidence.get("items", [])}
        result.update(str(item.get("id")) for item in workspace.reviews.get("items", []))
        result.update(str(item.get("event_id")) for item in _events(workspace.meta / "events.ndjson"))
        return result

    def propose(self, workspace: Any, payload: dict[str, Any], expected: int | None = None) -> dict[str, Any]:
        if not self.exists():
            raise StrategyError("Enable strategy experiments before proposing a candidate")
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            experiment = dict(payload)
            experiment.update(
                {
                    "kind": "atomlearn.strategy-experiment",
                    "schema_version": 1,
                    "revision": 1,
                    "scope": "user",
                    "status": "candidate",
                    "shadow_mode": True,
                }
            )
            errors = _schema_errors(experiment, EXPERIMENT_SCHEMA) + self._validate_candidate_values(experiment)
            if errors:
                raise StrategyError("Strategy candidate is invalid:\n- " + "\n- ".join(errors))
            if (self.experiments_dir / f"{experiment['id']}.yaml").exists():
                raise StrategyError(f"Strategy experiment already exists: {experiment['id']}")
            known_refs = self._workspace_reference_ids(workspace)
            missing = sorted(set(experiment["evidence_refs"]) - known_refs)
            if missing:
                raise StrategyError("Candidate evidence_refs are not present in the supplied workspace: " + ", ".join(missing))
            state = self._commit_locked(
                self.state(),
                "strategy.candidate_proposed",
                {"experiment_id": experiment["id"], "evidence_ref_count": len(experiment["evidence_refs"])},
                experiment=experiment,
            )
            return {"strategy_revision": state["revision"], "experiment": experiment}

    def _explicit_global_conflict(self, dimension: str) -> bool:
        profile = self.profile.state()
        item = profile.get("preferences", {}).get(dimension)
        return bool(
            isinstance(item, dict)
            and item.get("status") == "active"
            and item.get("source") == "explicit"
        )

    def start(self, experiment_id: str, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            state = self.state()
            if not state["experiments_enabled"]:
                raise StrategyError("Strategy experiments are disabled")
            experiment = self.experiment(experiment_id)
            if experiment["status"] not in {"candidate", "eligible", "paused"}:
                raise StrategyError(f"Experiment {experiment_id} cannot start from status {experiment['status']}")
            errors = self._validate_candidate_values(experiment)
            if self._explicit_global_conflict(experiment["dimension"]):
                errors.append("experiment conflicts with an explicit global user preference")
            conflicts = [
                item["id"]
                for item in self.experiments()
                if item["id"] != experiment_id
                and item.get("dimension") == experiment["dimension"]
                and item.get("status") in {"monitoring", "active"}
            ]
            if conflicts:
                errors.append("another experiment owns this dimension: " + ", ".join(conflicts))
            if errors:
                raise StrategyError("Experiment is not eligible:\n- " + "\n- ".join(errors))
            experiment["status"] = "monitoring"
            experiment["shadow_mode"] = True
            experiment["revision"] += 1
            committed = self._commit_locked(
                state,
                "strategy.monitoring_started",
                {"experiment_id": experiment_id, "shadow_mode": True},
                experiment=experiment,
            )
            return {"strategy_revision": committed["revision"], "experiment": experiment}

    def set_live(self, experiment_id: str, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            experiment = self.experiment(experiment_id)
            if experiment["status"] != "monitoring" or not experiment["shadow_mode"]:
                raise StrategyError("Only a shadow-mode monitoring experiment can be switched live")
            shadows = [item for item in self.exposures() if item["experiment_id"] == experiment_id and item["status"] == "shadow"]
            if not shadows:
                raise StrategyError("At least one replayable shadow exposure is required before live assignment")
            experiment["shadow_mode"] = False
            experiment["revision"] += 1
            committed = self._commit_locked(
                self.state(),
                "strategy.live_assignment_enabled",
                {"experiment_id": experiment_id, "shadow_exposures": len(shadows)},
                experiment=experiment,
            )
            return {"strategy_revision": committed["revision"], "experiment": experiment}

    def _refs(self, workspace: Any, atom_id: str, episode_key: str) -> tuple[str, str, str]:
        workspace_ref = _digest("ws-", self.profile_id, str(workspace.root))
        atom_ref = _digest("atom-", self.profile_id, str(workspace.root), atom_id)
        episode_ref = _digest("episode-", workspace_ref, atom_id, episode_key)
        return workspace_ref, atom_ref, episode_ref

    def exposure(
        self,
        workspace: Any,
        atom_id: str,
        *,
        context: str,
        episode_type: str,
        episode_key: str,
        atom_type: str | None = None,
        current_turn: dict[str, str] | None = None,
        expected: int | None = None,
    ) -> dict[str, Any]:
        if context not in EXPERIMENT_CONTEXTS:
            raise StrategyError(f"strategy context must be one of: {', '.join(sorted(EXPERIMENT_CONTEXTS))}")
        if episode_type not in EPISODE_TYPES:
            raise StrategyError(f"episode_type must be one of: {', '.join(sorted(EPISODE_TYPES))}")
        if atom_id not in workspace.atoms:
            raise StrategyError(f"Unknown Atom: {atom_id}")
        atom = workspace.atoms[atom_id]
        if context != "orientation" and (
            workspace.current.get("active_atom_id") != atom_id or atom.get("status") != "active"
        ):
            raise StrategyError("A teaching, review, or exam exposure can be recorded only for the Active Atom")
        resolved_type = atom_type or atom.get("type") or "concept"
        if not isinstance(resolved_type, str) or not resolved_type or len(resolved_type) > 32:
            raise StrategyError("atom_type must be a non-empty string of at most 32 characters")
        workspace_errors = workspace.validate()
        if workspace_errors:
            raise StrategyError("Cannot assign an exposure in an invalid workspace:\n- " + "\n- ".join(workspace_errors))
        policy = effective_for_workspace(workspace.root, context, current_turn=current_turn, data_dir=self.data_root)
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            state = self.state()
            if not state["experiments_enabled"]:
                return {"strategy_revision": state["revision"], "assigned": False, "reason": "experiments_disabled"}
            eligible = [
                item
                for item in self.experiments()
                if item["status"] == "monitoring"
                and context in item["contexts"]
                and resolved_type in item["strata"]["atom_types"]
                and episode_type in item["strata"]["episode_types"]
            ]
            if not eligible:
                return {"strategy_revision": state["revision"], "assigned": False, "reason": "no_eligible_experiment"}
            experiment = sorted(eligible, key=lambda item: item["id"])[0]
            workspace_ref, atom_ref, episode_ref = self._refs(workspace, atom_id, episode_key)
            exposure_id = _digest(
                "xps-", experiment["id"], workspace_ref, atom_ref, episode_ref, context, episode_type
            )
            existing = next((item for item in self.exposures() if item["id"] == exposure_id), None)
            if existing:
                return {
                    "strategy_revision": state["revision"],
                    "assigned": True,
                    "replayed": True,
                    "exposure": existing,
                    "instruction": POLICY_GUIDANCE.get((experiment["dimension"], existing["chosen_value"])),
                }
            stratum = "|".join(
                [
                    context,
                    resolved_type,
                    _difficulty_bucket(atom.get("difficulty")),
                    _diagnostic_bucket(atom),
                    episode_type,
                ]
            )
            assignment_hash = hashlib.sha256(f"{experiment['id']}|{atom_ref}|{stratum}".encode("utf-8")).digest()
            assigned_arm = "candidate" if assignment_hash[0] % 2 else "baseline"
            assigned_value = experiment[assigned_arm]
            decision = policy["effective"].get(experiment["dimension"])
            explicitly_overridden = bool(decision and decision.get("source") in EXPLICIT_SOURCES)
            if explicitly_overridden:
                arm = "overridden"
                status = "overridden"
                chosen_value = decision["value"]
            elif experiment["shadow_mode"]:
                arm = assigned_arm
                status = "shadow"
                chosen_value = decision["value"] if decision else experiment["baseline"]
            else:
                arm = assigned_arm
                status = "exposed"
                chosen_value = assigned_value
            policy_fingerprint = "sha256:" + hashlib.sha256(
                f"{policy['policy_fingerprint']}|{experiment['id']}|{assigned_arm}|{chosen_value}|{stratum}".encode("utf-8")
            ).hexdigest()
            exposure = {
                "kind": "atomlearn.strategy-exposure",
                "schema_version": 1,
                "id": exposure_id,
                "experiment_id": experiment["id"],
                "profile_id": self.profile_id,
                "workspace_ref": workspace_ref,
                "workspace_revision": workspace.revision,
                "atom_ref": atom_ref,
                "episode_ref": episode_ref,
                "arm": arm,
                "assigned_value": assigned_value,
                "chosen_value": chosen_value,
                "context": context,
                "atom_type": resolved_type,
                "difficulty_bucket": _difficulty_bucket(atom.get("difficulty")),
                "prior_diagnostic_bucket": _diagnostic_bucket(atom),
                "episode_type": episode_type,
                "stratum": stratum,
                "policy_fingerprint": policy_fingerprint,
                "status": status,
                "created_at": iso(),
            }
            errors = _schema_errors(exposure, EXPOSURE_SCHEMA)
            if errors:  # pragma: no cover - construction guard
                raise StrategyError("Exposure is invalid:\n- " + "\n- ".join(errors))
            records = self.exposures()
            records.append(exposure)
            committed = self._commit_locked(
                state,
                "strategy.exposure_recorded",
                {"experiment_id": experiment["id"], "exposure_id": exposure_id, "status": status},
                exposures=records,
            )
            return {
                "strategy_revision": committed["revision"],
                "assigned": True,
                "replayed": False,
                "exposure": exposure,
                "instruction": POLICY_GUIDANCE.get((experiment["dimension"], chosen_value)),
            }

    def record_outcome(
        self, workspace: Any, exposure_id: str, evidence_id: str, expected: int | None = None
    ) -> dict[str, Any]:
        workspace_errors = workspace.validate()
        if workspace_errors:
            raise StrategyError("Cannot record an outcome from an invalid workspace:\n- " + "\n- ".join(workspace_errors))
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            exposure = next((item for item in self.exposures() if item["id"] == exposure_id), None)
            if not exposure:
                raise StrategyError(f"Unknown exposure: {exposure_id}")
            if exposure["status"] != "exposed":
                raise StrategyError(f"Only a live exposed episode can receive an outcome; status is {exposure['status']}")
            workspace_ref, _, _ = self._refs(workspace, "placeholder", "placeholder")
            if exposure["workspace_ref"] != workspace_ref:
                raise StrategyError("Exposure belongs to a different workspace")
            evidence = next((item for item in workspace.evidence.get("items", []) if item.get("id") == evidence_id), None)
            if not evidence:
                raise StrategyError(f"Unknown Evidence: {evidence_id}")
            if evidence.get("result") not in {"mastered", "partial", "not_mastered"}:
                raise StrategyError("Outcome requires assessed Evidence")
            workspace_events = _events(workspace.meta / "events.ndjson")
            evidence_event = next(
                (
                    event
                    for event in workspace_events
                    if event.get("type") == "evidence.recorded"
                    and event.get("details", {}).get("evidence_id") == evidence_id
                ),
                None,
            )
            if not evidence_event or int(evidence_event.get("revision", -1)) <= int(exposure["workspace_revision"]):
                raise StrategyError("Historical Evidence created before this exposure cannot be backfilled as an outcome")
            atom_id = evidence.get("atom_id")
            if atom_id not in workspace.atoms:
                raise StrategyError("Evidence references an unknown Atom")
            _, atom_ref, _ = self._refs(workspace, atom_id, "placeholder")
            if atom_ref != exposure["atom_ref"]:
                raise StrategyError("Evidence does not belong to the Atom exposed by this episode")
            outcomes = self.outcomes()
            if any(item["evidence_ref"] == evidence_id for item in outcomes):
                raise StrategyError(f"Evidence {evidence_id} is already linked to a strategy exposure")
            if any(item["exposure_id"] == exposure_id for item in outcomes):
                raise StrategyError(f"Exposure {exposure_id} already has an outcome")
            scores = [float(value) for value in evidence.get("scores", {}).values() if isinstance(value, (int, float))]
            if not scores:
                raise StrategyError("Evidence has no numeric scores")
            assessed_at = str(evidence.get("assessed_at") or evidence.get("created_at") or "")
            backtracked = False
            for event in workspace_events:
                if event.get("type") != "session.backtracked" or str(event.get("at", "")) < exposure["created_at"]:
                    continue
                if assessed_at and str(event.get("at", "")) > assessed_at:
                    continue
                if event.get("details", {}).get("target_atom_id") == atom_id:
                    backtracked = True
                    break
            outcome_id = _digest("out-", exposure_id, evidence_id)
            outcome = {
                "kind": "atomlearn.strategy-outcome",
                "schema_version": 1,
                "id": outcome_id,
                "exposure_id": exposure_id,
                "experiment_id": exposure["experiment_id"],
                "evidence_ref": evidence_id,
                "atom_ref": atom_ref,
                "evidence_kind": str(evidence.get("kind")),
                "result": evidence["result"],
                "score": round(sum(scores) / len(scores), 6),
                "attempts": int(workspace.atoms[atom_id].get("attempts", 1)),
                "delayed": evidence.get("kind") == "review",
                "blocking_backtrack": backtracked,
                "workspace_valid": True,
                "recorded_at": iso(),
            }
            errors = _schema_errors(outcome, OUTCOME_SCHEMA)
            if errors:  # pragma: no cover - construction guard
                raise StrategyError("Outcome is invalid:\n- " + "\n- ".join(errors))
            outcomes.append(outcome)
            committed = self._commit_locked(
                self.state(),
                "strategy.outcome_recorded",
                {"experiment_id": exposure["experiment_id"], "exposure_id": exposure_id, "outcome_id": outcome_id},
                outcomes=outcomes,
            )
            return {"strategy_revision": committed["revision"], "outcome": outcome}

    def _comparison(self, experiment: dict[str, Any]) -> dict[str, Any]:
        exposure_by_id = {
            item["id"]: item
            for item in self.exposures()
            if item["experiment_id"] == experiment["id"] and item["status"] == "exposed"
        }
        all_outcomes = [item for item in self.outcomes() if item["experiment_id"] == experiment["id"]]
        outcomes = [
            item
            for item in all_outcomes
            if item["exposure_id"] in exposure_by_id
        ]
        strata_arms: dict[str, set[str]] = defaultdict(set)
        for item in outcomes:
            exposure = exposure_by_id[item["exposure_id"]]
            strata_arms[exposure["stratum"]].add(exposure["arm"])
        comparable_strata = sorted(key for key, arms in strata_arms.items() if arms == {"baseline", "candidate"})
        comparable = [item for item in outcomes if exposure_by_id[item["exposure_id"]]["stratum"] in comparable_strata]
        by_arm = {
            arm: [item for item in comparable if exposure_by_id[item["exposure_id"]]["arm"] == arm]
            for arm in ["baseline", "candidate"]
        }
        distinct_atoms = len({item["atom_ref"] for item in comparable})
        delayed_outcomes = sum(bool(item["delayed"]) for item in comparable)
        metrics: dict[str, Any] = {}
        for metric in experiment["metrics"]["primary"]:
            values: dict[str, float | None] = {}
            for arm, items in by_arm.items():
                if metric == "first_transfer_score":
                    values[arm] = _mean(item["score"] for item in items if not item["delayed"])
                elif metric == "delayed_review_score":
                    values[arm] = _mean(item["score"] for item in items if item["delayed"])
                elif metric == "mastery_attempts":
                    values[arm] = _mean(float(item["attempts"]) for item in items)
            baseline, candidate = values.get("baseline"), values.get("candidate")
            improvement = None
            if baseline is not None and candidate is not None:
                improvement = round((baseline - candidate) if metric == "mastery_attempts" else (candidate - baseline), 6)
            metrics[metric] = {**values, "improvement": improvement}
        guardrails: dict[str, Any] = {}
        for metric in experiment["metrics"]["guardrails"]:
            values = {}
            for arm, items in by_arm.items():
                if metric == "misconception_recurrence":
                    values[arm] = _mean(float(item["result"] != "mastered") for item in items if item["delayed"])
                elif metric == "blocking_backtrack_rate":
                    values[arm] = _mean(float(item["blocking_backtrack"]) for item in items)
                elif metric == "mastery_failure_rate":
                    values[arm] = _mean(float(item["result"] != "mastered") for item in items)
            baseline, candidate = values.get("baseline"), values.get("candidate")
            delta = round(candidate - baseline, 6) if baseline is not None and candidate is not None else None
            guardrails[metric] = {**values, "candidate_minus_baseline": delta}
        return {
            "comparable_strata": comparable_strata,
            "samples": {
                "baseline": len(by_arm["baseline"]),
                "candidate": len(by_arm["candidate"]),
                "distinct_atoms": distinct_atoms,
                "delayed_outcomes": delayed_outcomes,
                "pending_delayed": max(0, experiment["minimums"]["delayed_outcomes"] - delayed_outcomes),
            },
            "metrics": metrics,
            "guardrails": guardrails,
            "hard_gates": {
                "invalid_workspace_outcomes": sum(not item["workspace_valid"] for item in all_outcomes),
                "unlinked_or_nonlive_outcomes": sum(
                    item["exposure_id"] not in exposure_by_id for item in all_outcomes
                ),
                "linkage_mismatches": sum(
                    item["exposure_id"] in exposure_by_id
                    and (
                        item["atom_ref"] != exposure_by_id[item["exposure_id"]]["atom_ref"]
                        or item["experiment_id"] != exposure_by_id[item["exposure_id"]]["experiment_id"]
                    )
                    for item in all_outcomes
                ),
                "duplicate_evidence_links": len(all_outcomes)
                - len({item["evidence_ref"] for item in all_outcomes}),
            },
        }

    def monitor(self, experiment_id: str, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            experiment = self.experiment(experiment_id)
            if experiment["status"] not in {"monitoring", "active", "paused", "needs_review"}:
                raise StrategyError(f"Experiment {experiment_id} cannot be monitored from status {experiment['status']}")
            enum_errors = self._validate_candidate_values(experiment)
            report = self._comparison(experiment)
            reasons: list[str] = []
            decision = "monitoring"
            if enum_errors:
                decision = "needs_review"
                reasons.extend(enum_errors)
            elif experiment["shadow_mode"]:
                reasons.append("experiment is still in shadow mode; shadow exposures never count as outcomes")
            samples = report["samples"]
            if samples["distinct_atoms"] < experiment["minimums"]["distinct_atoms"]:
                reasons.append("insufficient distinct comparable Atoms")
            if samples["delayed_outcomes"] < experiment["minimums"]["delayed_outcomes"]:
                reasons.append("delayed outcomes are still pending")
            if min(samples["baseline"], samples["candidate"]) < 2:
                reasons.append("each arm needs at least two outcomes in comparable strata")
            missing_metrics = [name for name, value in report["metrics"].items() if value["improvement"] is None]
            missing_guards = [name for name, value in report["guardrails"].items() if value["candidate_minus_baseline"] is None]
            if missing_metrics:
                reasons.append("primary metrics are incomplete: " + ", ".join(missing_metrics))
            if missing_guards:
                reasons.append("guardrail metrics are incomplete: " + ", ".join(missing_guards))
            hard_gate_failed = any(report["hard_gates"].values())
            if hard_gate_failed:
                reasons.append("a hard integrity gate failed")
            thresholds = experiment["thresholds"]
            degraded = any(
                value["improvement"] is not None
                and value["improvement"] < -float(thresholds["maximum_quality_degradation"])
                for value in report["metrics"].values()
            )
            guardrail_degraded = any(
                value["candidate_minus_baseline"] is not None
                and value["candidate_minus_baseline"] > float(thresholds["maximum_guardrail_delta"])
                for value in report["guardrails"].values()
            )
            quality_improved = any(
                name in QUALITY_METRICS
                and value["improvement"] is not None
                and value["improvement"] >= float(thresholds["minimum_quality_delta"])
                for name, value in report["metrics"].items()
            )
            sufficient = not reasons
            if decision != "needs_review" and (degraded or guardrail_degraded or hard_gate_failed):
                decision = "paused"
                if degraded:
                    reasons.append("a primary quality metric degraded beyond its preregistered threshold")
                if guardrail_degraded:
                    reasons.append("a guardrail degraded beyond its preregistered threshold")
            elif decision != "needs_review" and sufficient and quality_improved:
                decision = "active"
            elif decision != "needs_review" and sufficient and not quality_improved:
                reasons.append("no preregistered quality metric improved enough; effort alone cannot promote")
            state = self.state()
            changed = experiment["status"] != decision
            if changed:
                experiment["status"] = decision
                experiment["revision"] += 1
                active = dict(state["active"])
                if decision == "active":
                    active[experiment["dimension"]] = {
                        "value": experiment["candidate"],
                        "experiment_id": experiment_id,
                        "activated_revision": state["revision"] + 1,
                    }
                elif active.get(experiment["dimension"], {}).get("experiment_id") == experiment_id:
                    active.pop(experiment["dimension"], None)
                state = dict(state)
                state["active"] = active
                state = self._commit_locked(
                    state,
                    "strategy.monitor_decision",
                    {"experiment_id": experiment_id, "decision": decision, "reason_count": len(reasons)},
                    experiment=experiment,
                )
            report.update(
                {
                    "strategy_revision": state["revision"],
                    "experiment_id": experiment_id,
                    "decision": decision,
                    "changed": changed,
                    "reasons": reasons,
                    "limitations": [
                        "This is a within-user operational comparison, not a universal causal claim.",
                        "Only outcomes in strata containing both arms are compared.",
                        "Explicit learner preferences always override an active strategy.",
                    ],
                }
            )
            return report

    def pause(self, experiment_id: str | None = None, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected)
            state = self.state()
            if experiment_id is None:
                value = dict(state)
                value["experiments_enabled"] = False
                active = {}
                changed_experiments = []
                for experiment in self.experiments():
                    if experiment["status"] in {"monitoring", "active"}:
                        experiment["status"] = "paused"
                        experiment["revision"] += 1
                        atomic_yaml(self.experiments_dir / f"{experiment['id']}.yaml", experiment)
                        changed_experiments.append(experiment["id"])
                value["active"] = active
                committed = self._commit_locked(
                    value,
                    "strategy.all_paused",
                    {"experiment_ids": changed_experiments},
                )
                return {"strategy_revision": committed["revision"], "paused": changed_experiments, "enabled": False}
            experiment = self.experiment(experiment_id)
            if experiment["status"] in {"retired", "rejected"}:
                raise StrategyError(f"Experiment {experiment_id} cannot be paused from status {experiment['status']}")
            experiment["status"] = "paused"
            experiment["revision"] += 1
            value = dict(state)
            active = dict(state["active"])
            if active.get(experiment["dimension"], {}).get("experiment_id") == experiment_id:
                active.pop(experiment["dimension"], None)
            value["active"] = active
            committed = self._commit_locked(
                value,
                "strategy.experiment_paused",
                {"experiment_id": experiment_id},
                experiment=experiment,
            )
            return {"strategy_revision": committed["revision"], "experiment": experiment}

    def validate(self) -> list[str]:
        if not self.exists():
            return [f"Strategy state does not exist for profile {self.profile_id}"]
        errors = [f"state.{item}" for item in _schema_errors(self.state(), STATE_SCHEMA)]
        experiments: dict[str, dict[str, Any]] = {}
        for experiment in self.experiments():
            experiment_id = str(experiment.get("id"))
            if experiment_id in experiments:
                errors.append(f"duplicate experiment ID: {experiment_id}")
            experiments[experiment_id] = experiment
            errors.extend(f"experiment {experiment_id}: {item}" for item in _schema_errors(experiment, EXPERIMENT_SCHEMA))
        exposures = self.exposures()
        exposure_ids: set[str] = set()
        episode_keys: set[tuple[str, str]] = set()
        for exposure in exposures:
            exposure_id = str(exposure.get("id"))
            errors.extend(f"exposure {exposure_id}: {item}" for item in _schema_errors(exposure, EXPOSURE_SCHEMA))
            if exposure_id in exposure_ids:
                errors.append(f"duplicate exposure ID: {exposure_id}")
            exposure_ids.add(exposure_id)
            key = (str(exposure.get("experiment_id")), str(exposure.get("episode_ref")))
            if key in episode_keys:
                errors.append(f"episode was exposed more than once: {key[0]} {key[1]}")
            episode_keys.add(key)
            experiment = experiments.get(str(exposure.get("experiment_id")))
            if not experiment:
                errors.append(f"exposure {exposure_id} references a missing experiment")
            else:
                if exposure.get("context") not in experiment.get("contexts", []):
                    errors.append(f"exposure {exposure_id} uses an ineligible context")
                if exposure.get("atom_type") not in experiment.get("strata", {}).get("atom_types", []):
                    errors.append(f"exposure {exposure_id} uses an ineligible Atom type")
                if exposure.get("episode_type") not in experiment.get("strata", {}).get("episode_types", []):
                    errors.append(f"exposure {exposure_id} uses an ineligible episode type")
                arm = exposure.get("arm")
                if arm in {"baseline", "candidate"} and exposure.get("assigned_value") != experiment.get(arm):
                    errors.append(f"exposure {exposure_id} assigned value disagrees with its arm")
        evidence_refs: set[str] = set()
        outcome_exposures: set[str] = set()
        for outcome in self.outcomes():
            outcome_id = str(outcome.get("id"))
            errors.extend(f"outcome {outcome_id}: {item}" for item in _schema_errors(outcome, OUTCOME_SCHEMA))
            if outcome.get("exposure_id") not in exposure_ids:
                errors.append(f"outcome {outcome_id} references a missing exposure")
            else:
                exposure = next(item for item in exposures if item.get("id") == outcome.get("exposure_id"))
                if exposure.get("status") != "exposed":
                    errors.append(f"outcome {outcome_id} is linked to a non-live exposure")
                if outcome.get("experiment_id") != exposure.get("experiment_id"):
                    errors.append(f"outcome {outcome_id} disagrees with its exposure experiment")
                if outcome.get("atom_ref") != exposure.get("atom_ref"):
                    errors.append(f"outcome {outcome_id} disagrees with its exposure Atom")
            if outcome.get("evidence_ref") in evidence_refs:
                errors.append(f"Evidence is linked more than once: {outcome.get('evidence_ref')}")
            evidence_refs.add(str(outcome.get("evidence_ref")))
            if outcome.get("exposure_id") in outcome_exposures:
                errors.append(f"exposure has more than one outcome: {outcome.get('exposure_id')}")
            outcome_exposures.add(str(outcome.get("exposure_id")))
        state = self.state()
        for dimension, active in state.get("active", {}).items():
            experiment = experiments.get(active.get("experiment_id"))
            if not experiment or experiment.get("status") != "active":
                errors.append(f"active strategy {dimension} does not point to an active experiment")
            elif active.get("value") != experiment.get("candidate"):
                errors.append(f"active strategy {dimension} disagrees with its experiment candidate")
        ledger = self.ledger()
        if len(ledger) != state.get("revision"):
            errors.append("strategy ledger length does not match state revision")
        for index, event in enumerate(ledger, start=1):
            if event.get("revision") != index or event.get("id") != f"strategy-event-{index:06d}":
                errors.append(f"strategy ledger event {index} has an invalid revision or ID")
        return errors

    def require_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise StrategyError("Strategy validation failed:\n- " + "\n- ".join(errors))

    def status(self) -> dict[str, Any]:
        if not self.exists():
            return {"profile_id": self.profile_id, "initialized": False, "experiments_enabled": False}
        self.require_valid()
        state = self.state()
        counts: dict[str, int] = defaultdict(int)
        for experiment in self.experiments():
            counts[experiment["status"]] += 1
        return {
            "profile_id": self.profile_id,
            "initialized": True,
            "strategy_revision": state["revision"],
            "experiments_enabled": state["experiments_enabled"],
            "active": state["active"],
            "experiment_counts": dict(sorted(counts.items())),
            "exposures": len(self.exposures()),
            "outcomes": len(self.outcomes()),
        }

    def preview(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.experiment(experiment_id)
        errors = self._validate_candidate_values(experiment)
        if self._explicit_global_conflict(experiment["dimension"]):
            errors.append("explicit global preference conflicts with this dimension")
        return {
            "experiment": experiment,
            "eligible": not errors,
            "eligibility_errors": errors,
            "comparison": self._comparison(experiment),
            "would_change_course_truth": False,
            "requires_shadow_first": True,
        }

    def explain(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.experiment(experiment_id)
        return {
            **self.preview(experiment_id),
            "ledger": [
                event for event in self.ledger() if event.get("details", {}).get("experiment_id") == experiment_id
            ],
            "precedence": "Current-turn and stored explicit preferences override active strategy values.",
            "instruction": POLICY_GUIDANCE.get((experiment["dimension"], experiment["candidate"])),
        }


def _engine_for_profile(profile: str, data_dir: str | None) -> StrategyEngine:
    return StrategyEngine.at_default_root(profile, data_dir)


def _engine_for_workspace(workspace: str, data_dir: str | None) -> tuple[StrategyEngine, Any]:
    return StrategyEngine.for_workspace(workspace, data_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded, replayable AtomLearn teaching-strategy experiments")
    parser.add_argument("--data-dir", help="Absolute AtomLearn user-data root (or use ATOMLEARN_DATA_DIR)")
    sub = parser.add_subparsers(dest="action", required=True)
    status = sub.add_parser("status", help="Show opt-in state, active overlays, and sample counts")
    status.add_argument("--profile", default="default")
    listing = sub.add_parser("list", help="List strategy experiments without learner content")
    listing.add_argument("--profile", default="default")
    listing.add_argument("--status", dest="status_filter")
    preview = sub.add_parser("preview", help="Check experiment eligibility and current comparable samples")
    preview.add_argument("experiment_id")
    preview.add_argument("--profile", default="default")
    enable = sub.add_parser("enable-experiments", help="Explicitly opt in to user-level strategy experiments")
    enable.add_argument("--profile", default="default")
    enable.add_argument("--expected-strategy-revision", type=int)
    propose = sub.add_parser("propose", help="Create a low-risk candidate grounded in existing workspace records")
    propose.add_argument("workspace")
    propose.add_argument("--input", required=True)
    propose.add_argument("--expected-strategy-revision", type=int)
    start = sub.add_parser("start", help="Start monitoring in mandatory shadow mode")
    start.add_argument("experiment_id")
    start.add_argument("--profile", default="default")
    start.add_argument("--expected-strategy-revision", type=int)
    live = sub.add_parser("set-live", help="Enable live assignment after at least one shadow exposure")
    live.add_argument("experiment_id")
    live.add_argument("--profile", default="default")
    live.add_argument("--expected-strategy-revision", type=int)
    exposure = sub.add_parser("exposure", help="Record or replay an immutable Atom-episode assignment")
    exposure.add_argument("workspace")
    exposure.add_argument("atom_id")
    exposure.add_argument("--context", choices=sorted(EXPERIMENT_CONTEXTS), default="teaching")
    exposure.add_argument("--episode-type", choices=sorted(EPISODE_TYPES), default="new_learning")
    exposure.add_argument("--episode-key", default="default")
    exposure.add_argument("--atom-type")
    exposure.add_argument("--overrides", help="YAML mapping of current-turn enum overrides")
    exposure.add_argument("--expected-strategy-revision", type=int)
    outcome = sub.add_parser("record-outcome", help="Bind assessed Evidence to one live exposure")
    outcome.add_argument("workspace")
    outcome.add_argument("exposure_id")
    outcome.add_argument("--evidence-id", required=True)
    outcome.add_argument("--expected-strategy-revision", type=int)
    monitor = sub.add_parser("monitor", help="Evaluate preregistered metrics and conservative promotion gates")
    monitor.add_argument("experiment_id")
    monitor.add_argument("--profile", default="default")
    monitor.add_argument("--expected-strategy-revision", type=int)
    pause = sub.add_parser("pause", help="Pause one experiment or disable and pause all experiments")
    pause.add_argument("experiment_id", nargs="?")
    pause.add_argument("--profile", default="default")
    pause.add_argument("--expected-strategy-revision", type=int)
    explain = sub.add_parser("explain", help="Explain eligibility, samples, metrics, history, and precedence")
    explain.add_argument("experiment_id")
    explain.add_argument("--profile", default="default")
    validate = sub.add_parser("validate", help="Validate state, experiments, immutable ledgers, and links")
    validate.add_argument("--profile", default="default")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action in {"status", "list", "preview", "enable-experiments", "start", "set-live", "monitor", "pause", "explain", "validate"}:
        engine = _engine_for_profile(args.profile, args.data_dir)
    if args.action == "status":
        result = engine.status()
    elif args.action == "list":
        experiments = engine.experiments() if engine.exists() else []
        if args.status_filter:
            experiments = [item for item in experiments if item.get("status") == args.status_filter]
        result = {"profile_id": args.profile, "experiments": experiments}
    elif args.action == "preview":
        result = engine.preview(args.experiment_id)
    elif args.action == "enable-experiments":
        if engine.exists():
            state = engine.set_enabled(True, args.expected_strategy_revision)
        else:
            if args.expected_strategy_revision not in {None, 0}:
                raise StrategyError("A new strategy state can only expect revision 0")
            state = engine.initialize(True)
        result = {"ok": True, "profile_id": args.profile, "strategy_revision": state["revision"], "enabled": True}
    elif args.action == "propose":
        engine, workspace = _engine_for_workspace(args.workspace, args.data_dir)
        payload = load_yaml(Path(args.input))
        result = engine.propose(workspace, payload, args.expected_strategy_revision)
    elif args.action == "start":
        result = engine.start(args.experiment_id, args.expected_strategy_revision)
    elif args.action == "set-live":
        result = engine.set_live(args.experiment_id, args.expected_strategy_revision)
    elif args.action == "exposure":
        engine, workspace = _engine_for_workspace(args.workspace, args.data_dir)
        overrides = load_yaml(Path(args.overrides)) if args.overrides else {}
        result = engine.exposure(
            workspace,
            args.atom_id,
            context=args.context,
            episode_type=args.episode_type,
            episode_key=args.episode_key,
            atom_type=args.atom_type,
            current_turn=overrides,
            expected=args.expected_strategy_revision,
        )
    elif args.action == "record-outcome":
        engine, workspace = _engine_for_workspace(args.workspace, args.data_dir)
        result = engine.record_outcome(workspace, args.exposure_id, args.evidence_id, args.expected_strategy_revision)
    elif args.action == "monitor":
        result = engine.monitor(args.experiment_id, args.expected_strategy_revision)
    elif args.action == "pause":
        result = engine.pause(args.experiment_id, args.expected_strategy_revision)
    elif args.action == "explain":
        result = engine.explain(args.experiment_id)
    elif args.action == "validate":
        engine.require_valid()
        result = {"ok": True, "profile_id": args.profile, "strategy_revision": engine.revision}
    else:  # pragma: no cover
        raise StrategyError(f"Unhandled strategy action: {args.action}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (StrategyError, UserProfileError, PlatformStateError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
