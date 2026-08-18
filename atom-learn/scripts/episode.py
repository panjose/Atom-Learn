#!/usr/bin/env python3
"""Privacy-bounded incremental episode checkpoints for AtomLearn harnesses."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import iso, load_workspace
from core_paths import CORE_ROOT
from platform_state import FileLock, PlatformStateError, atomic_yaml


STATE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "episode-checkpoint-state.schema.json"
OPAQUE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EPISODE_CONTEXTS = {"orientation", "teaching", "review", "exam", "research"}
EPISODE_TYPES = {"new_learning", "remediation", "review", "exam", "research"}
CHECKPOINT_EVENTS = {
    "activated",
    "exposure_recorded",
    "strategy_applied",
    "teaching_step",
    "evidence_attempted",
    "outcome_recorded",
    "review_event",
    "resumed",
    "finalized",
}
TEACHING_MODES = {"direct", "socratic", "guided_discovery", "mixed", "not_observed"}
INTERACTION_PATTERNS = {
    "explanation",
    "example",
    "question",
    "feedback",
    "integration",
    "retrieval_practice",
    "not_observed",
}
ATTEMPT_STATUSES = {"started", "submitted", "assessed", "abstained", "not_observed"}
FINALIZATION_MODES = {"strategy_outcome_recorded", "assessment_only", "no_outcome"}
RETIREMENT_REASONS = {"user_request", "privacy_request", "abandoned", "superseded"}


class EpisodeError(RuntimeError):
    """A user-correctable episode observability error."""


def _schema() -> dict[str, Any]:
    value = json.loads(STATE_SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise EpisodeError("Episode checkpoint schema must be an object")
    return value


def _schema_errors(value: dict[str, Any]) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(_schema()).iter_errors(value), key=lambda item: list(item.path))
    ]


def _digest(prefix: str, *parts: str, length: int = 24) -> str:
    canonical = "\x1f".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(canonical).hexdigest()[:length]


def opaque_refs(workspace: Any, atom_id: str, episode_key: str) -> tuple[str, str, str]:
    """Return stable workspace-local identities without persisting the episode key."""

    workspace_ref = _digest("ws-", str(workspace.root))
    atom_ref = _digest("atom-", workspace_ref, atom_id)
    episode_ref = _digest("episode-", workspace_ref, atom_ref, episode_key)
    return workspace_ref, atom_ref, episode_ref


def _opaque(value: str, label: str) -> str:
    if not OPAQUE_KEY.fullmatch(value):
        raise EpisodeError(f"{label} must be an opaque ID of at most 128 safe characters")
    return value


def _checkpoint_payload(
    *,
    episode_id: str,
    request_key: str,
    event: str,
    workspace_revision: int,
    exposure_ref: str | None = None,
    strategy_outcome_ref: str | None = None,
    evidence_ref: str | None = None,
    review_ref: str | None = None,
    teaching_mode: str | None = None,
    interaction_pattern: str | None = None,
    attempt_status: str | None = None,
) -> dict[str, Any]:
    payload = {
        "id": _digest("cp-", episode_id, request_key),
        "idempotency_key": request_key,
        "event": event,
        "workspace_revision": workspace_revision,
        "exposure_ref": exposure_ref,
        "strategy_outcome_ref": strategy_outcome_ref,
        "evidence_ref": evidence_ref,
        "review_ref": review_ref,
        "teaching_mode": teaching_mode,
        "interaction_pattern": interaction_pattern,
        "attempt_status": attempt_status,
        "at": iso(),
    }
    return payload


def _same_request(left: dict[str, Any], right: dict[str, Any]) -> bool:
    ignored = {"id", "at"}
    return {key: value for key, value in left.items() if key not in ignored} == {
        key: value for key, value in right.items() if key not in ignored
    }


class EpisodeEngine:
    """Atomic workspace-local state for incomplete, resumable teaching episodes."""

    def __init__(self, workspace: Any):
        self.workspace = workspace
        self.root = workspace.meta / "episodes"
        self.state_path = self.root / "state.yaml"
        self.lock_path = self.root / ".episodes.lock"

    @classmethod
    def load(cls, workspace_path: str) -> "EpisodeEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise EpisodeError("Cannot observe an invalid workspace:\n- " + "\n- ".join(errors))
        return cls(workspace)

    def exists(self) -> bool:
        return self.state_path.is_file()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "kind": "atomlearn.episode-checkpoint-state",
            "schema_version": 1,
            "revision": 0,
            "enabled": False,
            "coverage_started_at": None,
            "coverage_start_workspace_revision": None,
            "updated_at": iso(),
            "episodes": [],
        }

    def state(self) -> dict[str, Any]:
        if not self.exists():
            return self._empty_state()
        try:
            value = yaml.safe_load(self.state_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise EpisodeError(f"Cannot parse episode state: {exc}") from exc
        if not isinstance(value, dict):
            raise EpisodeError("Episode state must be a mapping")
        errors = _schema_errors(value)
        if errors:
            raise EpisodeError("Episode state is invalid:\n- " + "\n- ".join(errors))
        return value

    @property
    def revision(self) -> int:
        return int(self.state()["revision"])

    @staticmethod
    def _expect_revision(state: dict[str, Any], expected: int | None) -> None:
        if expected is not None and expected != state["revision"]:
            raise EpisodeError(
                f"Stale observability revision: expected {expected}, current is {state['revision']}. Reload episode status."
            )

    def _expect_workspace_revision(self, expected: int | None) -> None:
        if expected is None:
            raise EpisodeError("A checkpoint mutation requires --expected-workspace-revision")
        if expected != self.workspace.revision:
            raise EpisodeError(
                f"Stale workspace revision: expected {expected}, current is {self.workspace.revision}. Reload status."
            )

    def _write(self, state: dict[str, Any]) -> dict[str, Any]:
        value = dict(state)
        value["revision"] = int(state["revision"]) + 1
        value["updated_at"] = iso()
        errors = self.validate(value)
        if errors:  # pragma: no cover - construction guard
            raise EpisodeError("Episode mutation is invalid:\n- " + "\n- ".join(errors))
        atomic_yaml(self.state_path, value)
        return value

    @staticmethod
    def _find(state: dict[str, Any], episode_id: str) -> dict[str, Any]:
        episode = next((item for item in state["episodes"] if item["id"] == episode_id), None)
        if episode is None:
            raise EpisodeError(f"Unknown episode: {episode_id}")
        return episode

    def _current_atom_ref(self) -> str | None:
        atom_id = self.workspace.current.get("active_atom_id")
        if not isinstance(atom_id, str) or atom_id not in self.workspace.atoms:
            return None
        return opaque_refs(self.workspace, atom_id, "placeholder")[1]

    def _matching_evidence(self, episode: dict[str, Any], evidence_ref: str) -> dict[str, Any]:
        evidence = next(
            (item for item in self.workspace.evidence.get("items", []) if item.get("id") == evidence_ref), None
        )
        if evidence is None:
            raise EpisodeError(f"Unknown Evidence: {evidence_ref}")
        atom_id = evidence.get("atom_id")
        episode_key = evidence.get("episode_id")
        if not isinstance(atom_id, str) or not isinstance(episode_key, str):
            raise EpisodeError("Evidence lacks v3 Atom and episode provenance")
        _, atom_ref, episode_ref = opaque_refs(self.workspace, atom_id, episode_key)
        if atom_ref != episode["atom_ref"] or episode_ref != episode["id"]:
            raise EpisodeError("Evidence does not belong to this Atom episode")
        return evidence

    def enable(self, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            state = self.state()
            if state["enabled"]:
                return {"replayed": True, **self.status(state)}
            self._expect_revision(state, expected)
            state["enabled"] = True
            if state["coverage_started_at"] is None:
                state["coverage_started_at"] = iso()
                state["coverage_start_workspace_revision"] = self.workspace.revision
            committed = self._write(state)
            return {"replayed": False, **self.status(committed)}

    def disable(self, expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            state = self.state()
            if not state["enabled"]:
                return {"replayed": True, **self.status(state)}
            self._expect_revision(state, expected)
            state["enabled"] = False
            committed = self._write(state)
            return {"replayed": False, **self.status(committed)}

    def begin(
        self,
        atom_id: str,
        episode_key: str,
        request_key: str,
        context: str,
        episode_type: str,
        expected_observability_revision: int | None,
        expected_workspace_revision: int | None,
    ) -> dict[str, Any]:
        _opaque(episode_key, "episode key")
        _opaque(request_key, "request key")
        if expected_workspace_revision is None:
            raise EpisodeError("A checkpoint mutation requires --expected-workspace-revision")
        if context not in EPISODE_CONTEXTS:
            raise EpisodeError(f"context must be one of: {', '.join(sorted(EPISODE_CONTEXTS))}")
        if episode_type not in EPISODE_TYPES:
            raise EpisodeError(f"episode type must be one of: {', '.join(sorted(EPISODE_TYPES))}")
        workspace_ref, atom_ref, episode_id = opaque_refs(self.workspace, atom_id, episode_key)
        with FileLock(self.lock_path):
            state = self.state()
            existing = next((item for item in state["episodes"] if item["id"] == episode_id), None)
            if existing is not None:
                if existing["status"] == "retired":
                    raise EpisodeError("This episode identity was retired and cannot be reused")
                if (
                    existing["context"] != context
                    or existing["episode_type"] != episode_type
                    or existing["checkpoints"][0]["idempotency_key"] != request_key
                    or existing["checkpoints"][0]["workspace_revision"] != expected_workspace_revision
                ):
                    raise EpisodeError("Episode identity was already used by a different begin request")
                return {
                    "replayed": True,
                    "observability_revision": state["revision"],
                    "episode": existing,
                }
            if not state["enabled"]:
                raise EpisodeError("Episode observability is disabled; run episode enable after learner opt-in")
            if atom_id not in self.workspace.atoms:
                raise EpisodeError(f"Unknown Atom: {atom_id}")
            if (
                self.workspace.current.get("active_atom_id") != atom_id
                or self.workspace.atoms[atom_id].get("status") != "active"
            ):
                raise EpisodeError("An episode can begin only for the Active Atom")
            self._expect_workspace_revision(expected_workspace_revision)
            other = next((item for item in state["episodes"] if item["status"] == "incomplete"), None)
            if other is not None:
                raise EpisodeError(
                    f"Episode {other['id']} is still incomplete; resume, finalize, or retire it before beginning another"
                )
            self._expect_revision(state, expected_observability_revision)
            checkpoint = _checkpoint_payload(
                episode_id=episode_id,
                request_key=request_key,
                event="activated",
                workspace_revision=self.workspace.revision,
            )
            timestamp = checkpoint["at"]
            episode = {
                "id": episode_id,
                "workspace_ref": workspace_ref,
                "atom_ref": atom_ref,
                "context": context,
                "episode_type": episode_type,
                "status": "incomplete",
                "started_workspace_revision": self.workspace.revision,
                "last_workspace_revision": self.workspace.revision,
                "started_at": timestamp,
                "updated_at": timestamp,
                "finalized_at": None,
                "retired_at": None,
                "retirement_reason": None,
                "finalization_mode": None,
                "checkpoints": [checkpoint],
            }
            state["episodes"].append(episode)
            committed = self._write(state)
            return {
                "replayed": False,
                "observability_revision": committed["revision"],
                "episode": episode,
            }

    def checkpoint(
        self,
        episode_id: str,
        request_key: str,
        event: str,
        expected_observability_revision: int | None,
        expected_workspace_revision: int | None,
        **fields: Any,
    ) -> dict[str, Any]:
        _opaque(request_key, "request key")
        if event not in CHECKPOINT_EVENTS - {"activated", "resumed", "finalized"}:
            raise EpisodeError("Use begin, resume, or finalize for this checkpoint event")
        if expected_workspace_revision is None:
            raise EpisodeError("A checkpoint mutation requires --expected-workspace-revision")
        with FileLock(self.lock_path):
            state = self.state()
            episode = self._find(state, episode_id)
            proposed = _checkpoint_payload(
                episode_id=episode_id,
                request_key=request_key,
                event=event,
                workspace_revision=expected_workspace_revision,
                **fields,
            )
            previous = next(
                (item for item in episode["checkpoints"] if item["idempotency_key"] == request_key), None
            )
            if previous is not None:
                if not _same_request(previous, proposed):
                    raise EpisodeError("Idempotency key was already used with a different checkpoint payload")
                return {
                    "replayed": True,
                    "observability_revision": state["revision"],
                    "episode": episode,
                    "checkpoint": previous,
                }
            if not state["enabled"]:
                raise EpisodeError("Episode observability is disabled")
            if episode["status"] != "incomplete":
                raise EpisodeError(f"Only an incomplete episode can accept checkpoints; status is {episode['status']}")
            self._expect_workspace_revision(expected_workspace_revision)
            self._expect_revision(state, expected_observability_revision)
            if self.workspace.revision < episode["last_workspace_revision"]:
                raise EpisodeError("Workspace revision moved behind the episode checkpoint")
            current_atom_ref = self._current_atom_ref()
            evidence_ref = fields.get("evidence_ref")
            if evidence_ref is not None:
                self._matching_evidence(episode, evidence_ref)
            if event in {"exposure_recorded", "strategy_applied", "teaching_step", "evidence_attempted"}:
                if current_atom_ref != episode["atom_ref"]:
                    raise EpisodeError("This teaching checkpoint does not belong to the current Active Atom")
            if event == "exposure_recorded" and not fields.get("exposure_ref"):
                raise EpisodeError("exposure_recorded requires --exposure-ref")
            if event == "strategy_applied" and not fields.get("teaching_mode"):
                raise EpisodeError("strategy_applied requires --teaching-mode")
            if event == "teaching_step" and not fields.get("interaction_pattern"):
                raise EpisodeError("teaching_step requires --interaction-pattern")
            if event == "evidence_attempted" and not fields.get("attempt_status"):
                raise EpisodeError("evidence_attempted requires --attempt-status")
            if event == "outcome_recorded":
                if not fields.get("strategy_outcome_ref") or not evidence_ref:
                    raise EpisodeError("outcome_recorded requires strategy outcome and Evidence references")
                evidence = self._matching_evidence(episode, evidence_ref)
                if evidence.get("result") not in {"mastered", "partial", "not_mastered"}:
                    raise EpisodeError("Outcome checkpoint requires assessed Evidence")
                if evidence.get("strategy_eligible") is not True:
                    raise EpisodeError("Outcome checkpoint requires strategy-qualified Evidence")
            if event == "review_event" and not fields.get("review_ref"):
                raise EpisodeError("review_event requires --review-ref")
            episode["checkpoints"].append(proposed)
            episode["last_workspace_revision"] = self.workspace.revision
            episode["updated_at"] = proposed["at"]
            committed = self._write(state)
            return {
                "replayed": False,
                "observability_revision": committed["revision"],
                "episode": episode,
                "checkpoint": proposed,
            }

    def resume(
        self,
        episode_id: str,
        request_key: str,
        expected_observability_revision: int | None,
        expected_workspace_revision: int | None,
    ) -> dict[str, Any]:
        _opaque(request_key, "request key")
        if expected_workspace_revision is None:
            raise EpisodeError("A checkpoint mutation requires --expected-workspace-revision")
        with FileLock(self.lock_path):
            state = self.state()
            episode = self._find(state, episode_id)
            proposed = _checkpoint_payload(
                episode_id=episode_id,
                request_key=request_key,
                event="resumed",
                workspace_revision=expected_workspace_revision,
            )
            previous = next(
                (item for item in episode["checkpoints"] if item["idempotency_key"] == request_key), None
            )
            if previous is not None:
                if not _same_request(previous, proposed):
                    raise EpisodeError("Idempotency key was already used with a different resume payload")
                return self._resume_result(state, episode, previous, replayed=True)
            if not state["enabled"]:
                raise EpisodeError("Episode observability is disabled")
            if episode["status"] != "incomplete":
                raise EpisodeError(f"Only an incomplete episode can resume; status is {episode['status']}")
            self._expect_workspace_revision(expected_workspace_revision)
            self._expect_revision(state, expected_observability_revision)
            if self.workspace.revision != episode["last_workspace_revision"]:
                raise EpisodeError(
                    "Cannot resume because workspace state changed after the last checkpoint; inspect and retire or reconcile it"
                )
            if self._current_atom_ref() != episode["atom_ref"]:
                raise EpisodeError("Cannot resume because the episode Atom is no longer Active")
            episode["checkpoints"].append(proposed)
            episode["updated_at"] = proposed["at"]
            committed = self._write(state)
            return self._resume_result(committed, episode, proposed, replayed=False)

    @staticmethod
    def _resume_result(
        state: dict[str, Any], episode: dict[str, Any], checkpoint: dict[str, Any], *, replayed: bool
    ) -> dict[str, Any]:
        observed = {item["event"] for item in episode["checkpoints"]}
        if "outcome_recorded" in observed:
            next_checkpoint = "finalized"
        elif "evidence_attempted" in observed:
            next_checkpoint = "outcome_recorded_or_finalize_without_outcome"
        elif "teaching_step" in observed:
            next_checkpoint = "evidence_attempted"
        elif "exposure_recorded" in observed or "strategy_applied" in observed:
            next_checkpoint = "teaching_step"
        else:
            next_checkpoint = "exposure_recorded_or_teaching_step"
        return {
            "replayed": replayed,
            "observability_revision": state["revision"],
            "episode": episode,
            "checkpoint": checkpoint,
            "next_checkpoint": next_checkpoint,
        }

    def finalize(
        self,
        episode_id: str,
        request_key: str,
        mode: str,
        expected_observability_revision: int | None,
        expected_workspace_revision: int | None,
        evidence_ref: str | None = None,
    ) -> dict[str, Any]:
        _opaque(request_key, "request key")
        if mode not in FINALIZATION_MODES:
            raise EpisodeError(f"finalization mode must be one of: {', '.join(sorted(FINALIZATION_MODES))}")
        if expected_workspace_revision is None:
            raise EpisodeError("A checkpoint mutation requires --expected-workspace-revision")
        with FileLock(self.lock_path):
            state = self.state()
            episode = self._find(state, episode_id)
            proposed = _checkpoint_payload(
                episode_id=episode_id,
                request_key=request_key,
                event="finalized",
                workspace_revision=expected_workspace_revision,
                evidence_ref=evidence_ref,
            )
            previous = next(
                (item for item in episode["checkpoints"] if item["idempotency_key"] == request_key), None
            )
            if previous is not None and episode["status"] == "finalized":
                if not _same_request(previous, proposed) or episode["finalization_mode"] != mode:
                    raise EpisodeError("Idempotency key was already used with a different finalization payload")
                return {
                    "replayed": True,
                    "observability_revision": state["revision"],
                    "episode": episode,
                    "strategy_promotion_input": False,
                }
            if not state["enabled"]:
                raise EpisodeError("Episode observability is disabled")
            if episode["status"] != "incomplete":
                raise EpisodeError(f"Only an incomplete episode can finalize; status is {episode['status']}")
            self._expect_workspace_revision(expected_workspace_revision)
            self._expect_revision(state, expected_observability_revision)
            events = {item["event"] for item in episode["checkpoints"]}
            if mode == "strategy_outcome_recorded" and "outcome_recorded" not in events:
                raise EpisodeError("strategy_outcome_recorded finalization requires an outcome checkpoint")
            if mode == "assessment_only":
                if not evidence_ref:
                    raise EpisodeError("assessment_only finalization requires --evidence-ref")
                evidence = self._matching_evidence(episode, evidence_ref)
                if evidence.get("result") not in {"mastered", "partial", "not_mastered"}:
                    raise EpisodeError("assessment_only finalization requires assessed Evidence")
            if mode == "no_outcome" and evidence_ref is not None:
                raise EpisodeError("no_outcome finalization cannot cite Evidence")
            timestamp = proposed["at"]
            episode["checkpoints"].append(proposed)
            episode["status"] = "finalized"
            episode["last_workspace_revision"] = self.workspace.revision
            episode["updated_at"] = timestamp
            episode["finalized_at"] = timestamp
            episode["finalization_mode"] = mode
            committed = self._write(state)
            return {
                "replayed": False,
                "observability_revision": committed["revision"],
                "episode": episode,
                "strategy_promotion_input": False,
            }

    def retire(
        self,
        episode_id: str,
        reason: str,
        expected_observability_revision: int | None,
    ) -> dict[str, Any]:
        if reason not in RETIREMENT_REASONS:
            raise EpisodeError(f"retirement reason must be one of: {', '.join(sorted(RETIREMENT_REASONS))}")
        with FileLock(self.lock_path):
            state = self.state()
            episode = self._find(state, episode_id)
            if episode["status"] == "retired":
                if episode["retirement_reason"] != reason:
                    raise EpisodeError("Episode was already retired for a different reason")
                return {"replayed": True, "observability_revision": state["revision"], "episode": episode}
            self._expect_revision(state, expected_observability_revision)
            timestamp = iso()
            episode["status"] = "retired"
            episode["retired_at"] = timestamp
            episode["retirement_reason"] = reason
            episode["updated_at"] = timestamp
            committed = self._write(state)
            return {"replayed": False, "observability_revision": committed["revision"], "episode": episode}

    def inspect(self, episode_id: str) -> dict[str, Any]:
        state = self.state()
        return {"observability_revision": state["revision"], "episode": self._find(state, episode_id)}

    def validate(self, state: dict[str, Any] | None = None) -> list[str]:
        state = state or self.state()
        errors = _schema_errors(state)
        if errors:
            return errors
        if (state["coverage_started_at"] is None) != (state["coverage_start_workspace_revision"] is None):
            errors.append("coverage start timestamp and workspace revision must be present together")
        if state["enabled"] and state["coverage_started_at"] is None:
            errors.append("enabled observability requires an explicit coverage boundary")
        episode_ids: set[str] = set()
        incomplete = 0
        workspace_ref = _digest("ws-", str(self.workspace.root))
        evidence_ids = {item.get("id") for item in self.workspace.evidence.get("items", [])}
        for episode in state["episodes"]:
            episode_id = episode["id"]
            if episode_id in episode_ids:
                errors.append(f"duplicate episode ID: {episode_id}")
            episode_ids.add(episode_id)
            if episode["workspace_ref"] != workspace_ref:
                errors.append(f"{episode_id}: workspace reference does not match this workspace")
            if episode["status"] == "incomplete":
                incomplete += 1
            checkpoints = episode["checkpoints"]
            if not checkpoints or checkpoints[0]["event"] != "activated":
                errors.append(f"{episode_id}: first checkpoint must be activated")
                continue
            keys = [item["idempotency_key"] for item in checkpoints]
            if len(keys) != len(set(keys)):
                errors.append(f"{episode_id}: checkpoint idempotency keys are not unique")
            revisions = [item["workspace_revision"] for item in checkpoints]
            if revisions != sorted(revisions):
                errors.append(f"{episode_id}: checkpoint workspace revisions moved backward")
            if episode["last_workspace_revision"] != revisions[-1]:
                errors.append(f"{episode_id}: last workspace revision does not match its final checkpoint")
            finalized = [item for item in checkpoints if item["event"] == "finalized"]
            if episode["status"] == "finalized" and len(finalized) != 1:
                errors.append(f"{episode_id}: finalized episode must have exactly one final checkpoint")
            if episode["status"] == "incomplete" and finalized:
                errors.append(f"{episode_id}: incomplete episode contains a final checkpoint")
            if episode["status"] == "incomplete" and any(
                episode[key] is not None
                for key in ["finalized_at", "finalization_mode", "retired_at", "retirement_reason"]
            ):
                errors.append(f"{episode_id}: incomplete episode contains finalization or retirement metadata")
            if episode["status"] == "finalized" and (
                episode["finalized_at"] is None
                or episode["finalization_mode"] is None
                or episode["retired_at"] is not None
                or episode["retirement_reason"] is not None
            ):
                errors.append(f"{episode_id}: finalized episode metadata is inconsistent")
            if episode["status"] == "retired" and (
                episode["retired_at"] is None or episode["retirement_reason"] is None
            ):
                errors.append(f"{episode_id}: retired episode lacks retirement metadata")
            if episode["status"] == "retired" and episode["finalization_mode"] is not None and len(finalized) != 1:
                errors.append(f"{episode_id}: retired finalized episode lacks its final checkpoint")
            if episode["finalization_mode"] == "strategy_outcome_recorded" and not any(
                item["event"] == "outcome_recorded" for item in checkpoints
            ):
                errors.append(f"{episode_id}: strategy finalization lacks an outcome checkpoint")
            for checkpoint in checkpoints:
                evidence_ref = checkpoint.get("evidence_ref")
                if evidence_ref is not None and evidence_ref not in evidence_ids:
                    errors.append(f"{episode_id}: checkpoint references missing Evidence {evidence_ref}")
                elif evidence_ref is not None:
                    try:
                        self._matching_evidence(episode, evidence_ref)
                    except EpisodeError as exc:
                        errors.append(f"{episode_id}: {exc}")
        if incomplete > 1:
            errors.append("more than one episode is incomplete")
        return errors

    def status(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self.state()
        episodes = [item for item in state["episodes"] if item["status"] != "retired"]
        total = len(episodes)

        def count(event: str) -> int:
            return sum(any(item["event"] == event for item in episode["checkpoints"]) for episode in episodes)

        def rate(value: int) -> float | None:
            return round(value / total, 6) if total else None

        event_counts = {
            event: count(event)
            for event in ["exposure_recorded", "teaching_step", "evidence_attempted", "outcome_recorded", "resumed"]
        }
        counts = {status: sum(item["status"] == status for item in state["episodes"]) for status in [
            "incomplete", "finalized", "retired"
        ]}
        return {
            "initialized": self.exists(),
            "enabled": state["enabled"],
            "observability_revision": state["revision"],
            "coverage_boundary": {
                "started_at": state["coverage_started_at"],
                "workspace_revision": state["coverage_start_workspace_revision"],
                "historical_episodes_backfilled": False,
            },
            "episode_counts": counts,
            "coverage": {
                "observed_episodes": total,
                **{f"{key}_count": value for key, value in event_counts.items()},
                **{f"{key}_rate": rate(value) for key, value in event_counts.items()},
                "finalized_count": counts["finalized"],
                "finalized_rate": rate(counts["finalized"]),
                "incomplete_without_outcome": sum(
                    episode["status"] == "incomplete"
                    and not any(item["event"] == "outcome_recorded" for item in episode["checkpoints"])
                    for episode in episodes
                ),
            },
            "privacy": {
                "raw_messages_stored": False,
                "free_text_profiles_stored": False,
                "sensitive_traits_inferred": False,
            },
            "claim_boundary": (
                "Checkpoint coverage measures harness observability only. It is not mastery Evidence, "
                "a strategy outcome, model-behavior verification, or proof of learning benefit."
            ),
        }


def _add_revisions(parser: argparse.ArgumentParser, *, workspace: bool = False) -> None:
    parser.add_argument("--expected-observability-revision", type=int)
    if workspace:
        parser.add_argument("--expected-workspace-revision", type=int, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage privacy-bounded incremental Atom episode checkpoints")
    sub = parser.add_subparsers(dest="action", required=True)
    status = sub.add_parser("status", help="Show opt-in state and observability coverage without learning claims")
    status.add_argument("workspace")
    validate = sub.add_parser("validate", help="Validate episode schemas identities and checkpoint consistency")
    validate.add_argument("workspace")
    enable = sub.add_parser("enable", help="Explicitly opt in to enum-only episode observability")
    enable.add_argument("workspace")
    _add_revisions(enable)
    disable = sub.add_parser("disable", help="Stop new episode observations while preserving inspectable history")
    disable.add_argument("workspace")
    _add_revisions(disable)
    begin = sub.add_parser("begin", help="Checkpoint activation for exactly one Active Atom episode")
    begin.add_argument("workspace")
    begin.add_argument("atom_id")
    begin.add_argument("--episode-key", required=True)
    begin.add_argument("--request-key", required=True)
    begin.add_argument("--context", choices=sorted(EPISODE_CONTEXTS), default="teaching")
    begin.add_argument("--episode-type", choices=sorted(EPISODE_TYPES), default="new_learning")
    _add_revisions(begin, workspace=True)
    checkpoint = sub.add_parser("checkpoint", help="Append one idempotent enum-only episode transition")
    checkpoint.add_argument("workspace")
    checkpoint.add_argument("episode_id")
    checkpoint.add_argument("--request-key", required=True)
    checkpoint.add_argument(
        "--event", required=True, choices=sorted(CHECKPOINT_EVENTS - {"activated", "resumed", "finalized"})
    )
    checkpoint.add_argument("--exposure-ref")
    checkpoint.add_argument("--strategy-outcome-ref")
    checkpoint.add_argument("--evidence-ref")
    checkpoint.add_argument("--review-ref")
    checkpoint.add_argument("--teaching-mode", choices=sorted(TEACHING_MODES))
    checkpoint.add_argument("--interaction-pattern", choices=sorted(INTERACTION_PATTERNS))
    checkpoint.add_argument("--attempt-status", choices=sorted(ATTEMPT_STATUSES))
    _add_revisions(checkpoint, workspace=True)
    resume = sub.add_parser("resume", help="Resume an incomplete episode only from its exact workspace revision")
    resume.add_argument("workspace")
    resume.add_argument("episode_id")
    resume.add_argument("--request-key", required=True)
    _add_revisions(resume, workspace=True)
    finalize = sub.add_parser("finalize", help="Finalize without turning checkpoints into mastery or outcomes")
    finalize.add_argument("workspace")
    finalize.add_argument("episode_id")
    finalize.add_argument("--request-key", required=True)
    finalize.add_argument("--mode", choices=sorted(FINALIZATION_MODES), required=True)
    finalize.add_argument("--evidence-ref")
    _add_revisions(finalize, workspace=True)
    inspect = sub.add_parser("inspect", help="Inspect one minimized episode checkpoint record")
    inspect.add_argument("workspace")
    inspect.add_argument("episode_id")
    retire = sub.add_parser("retire", help="Retire one episode from coverage without deleting its audit record")
    retire.add_argument("workspace")
    retire.add_argument("episode_id")
    retire.add_argument("--reason", choices=sorted(RETIREMENT_REASONS), required=True)
    _add_revisions(retire)
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    engine = EpisodeEngine.load(args.workspace)
    if args.action == "status":
        errors = engine.validate()
        if errors:
            raise EpisodeError("Episode validation failed:\n- " + "\n- ".join(errors))
        return engine.status()
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise EpisodeError("Episode validation failed:\n- " + "\n- ".join(errors))
        return {"ok": True, **engine.status()}
    if args.action == "enable":
        return engine.enable(args.expected_observability_revision)
    if args.action == "disable":
        return engine.disable(args.expected_observability_revision)
    if args.action == "begin":
        return engine.begin(
            args.atom_id,
            args.episode_key,
            args.request_key,
            args.context,
            args.episode_type,
            args.expected_observability_revision,
            args.expected_workspace_revision,
        )
    if args.action == "checkpoint":
        return engine.checkpoint(
            args.episode_id,
            args.request_key,
            args.event,
            args.expected_observability_revision,
            args.expected_workspace_revision,
            exposure_ref=args.exposure_ref,
            strategy_outcome_ref=args.strategy_outcome_ref,
            evidence_ref=args.evidence_ref,
            review_ref=args.review_ref,
            teaching_mode=args.teaching_mode,
            interaction_pattern=args.interaction_pattern,
            attempt_status=args.attempt_status,
        )
    if args.action == "resume":
        return engine.resume(
            args.episode_id,
            args.request_key,
            args.expected_observability_revision,
            args.expected_workspace_revision,
        )
    if args.action == "finalize":
        return engine.finalize(
            args.episode_id,
            args.request_key,
            args.mode,
            args.expected_observability_revision,
            args.expected_workspace_revision,
            args.evidence_ref,
        )
    if args.action == "inspect":
        return engine.inspect(args.episode_id)
    if args.action == "retire":
        return engine.retire(args.episode_id, args.reason, args.expected_observability_revision)
    raise EpisodeError(f"Unhandled episode action: {args.action}")


def main() -> int:
    try:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
        return 0
    except (EpisodeError, PlatformStateError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
