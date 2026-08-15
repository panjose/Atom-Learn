#!/usr/bin/env python3
"""Opt-in cross-course learner profiles with enum-only privacy-safe signals."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from adaptation import (
    ADAPTATION_CONTEXTS,
    DIRECTIONS,
    EVIDENCE_TYPES,
    MAX_SIGNALS_PER_SESSION,
    MAX_TURN_REFS,
    OPAQUE_ID,
    PREFERENCE_VALUES,
    REASON_CODES,
    RETIRE_REASONS,
)
from atomlearn import iso, load_workspace, require_number, require_string, unique
from platform_state import (
    CORE_ROOT,
    FileLock,
    PlatformStateError,
    atomic_text,
    atomic_yaml,
    core_version,
    resolve_user_data_root,
)


PROFILE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "user-profile.schema.json"
BINDING_SCHEMA = CORE_ROOT / "assets" / "schemas" / "profile-binding.schema.json"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class UserProfileError(RuntimeError):
    """A user-correctable cross-course profile error."""


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UserProfileError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UserProfileError(f"Expected a mapping in {path}")
    return value


def load_json_schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise UserProfileError(f"Schema is not an object: {path}")
    return value


def json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UserProfileError(f"{path} line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise UserProfileError(f"{path} line {line_number} must be an object")
        result.append(value)
    return result


def serialize_json_lines(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)


def validate_with_schema(value: dict[str, Any], schema_path: Path) -> list[str]:
    schema = load_json_schema(schema_path)
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    ]


class WorkspaceBinding:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.path = self.workspace / ".atomlearn" / "profile-binding.yaml"
        self.lock = self.workspace / ".atomlearn" / ".profile-binding.lock"

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> dict[str, Any] | None:
        if not self.exists():
            return None
        value = load_yaml(self.path)
        errors = validate_with_schema(value, BINDING_SCHEMA)
        if errors:
            raise UserProfileError("Workspace profile binding is invalid:\n- " + "\n- ".join(errors))
        return value

    def write(self, profile_id: str, enabled: bool, expected_revision: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock):
            current = self.read()
            if current is None:
                if expected_revision not in {None, 0}:
                    raise UserProfileError("Cannot expect a non-zero revision for a new workspace profile binding")
                revision = 0
            else:
                if expected_revision is not None and current["revision"] != expected_revision:
                    raise UserProfileError(
                        f"Stale profile binding revision: expected {expected_revision}, current is {current['revision']}"
                    )
                revision = current["revision"] + 1
            value = {
                "kind": "atomlearn.workspace-profile-binding",
                "schema_version": 1,
                "revision": revision,
                "profile_id": profile_id,
                "enabled": enabled,
            }
            errors = validate_with_schema(value, BINDING_SCHEMA)
            if errors:  # pragma: no cover - construction guard
                raise UserProfileError("Cannot write invalid profile binding:\n- " + "\n- ".join(errors))
            atomic_yaml(self.path, value)
            return value


class UserProfileEngine:
    def __init__(self, data_root: Path, profile_id: str = "default"):
        if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise UserProfileError("profile_id must be an opaque ID using letters, numbers, dot, colon, underscore, or hyphen")
        self.data_root = data_root.resolve(strict=False)
        self.profile_id = profile_id
        self.root = self.data_root / "profiles" / profile_id
        self.state_path = self.root / "state.yaml"
        self.signals_path = self.root / "signals.ndjson"
        self.ledger_path = self.root / "ledger.ndjson"
        self.lock_path = self.root / ".profile.lock"

    @classmethod
    def at_default_root(cls, profile_id: str = "default", data_dir: str | Path | None = None) -> "UserProfileEngine":
        return cls(resolve_user_data_root(data_dir, create=False), profile_id)

    @classmethod
    def for_workspace(
        cls,
        workspace: str | Path,
        data_dir: str | Path | None = None,
        *,
        require_enabled: bool = True,
    ) -> tuple["UserProfileEngine", dict[str, Any]]:
        loaded = load_workspace(str(workspace))
        binding = WorkspaceBinding(loaded.root).read()
        if binding is None:
            raise UserProfileError("This workspace has no global profile binding; run profile enable first")
        if require_enabled and not binding["enabled"]:
            raise UserProfileError("The global profile binding is disabled for this workspace")
        engine = cls.at_default_root(binding["profile_id"], data_dir)
        if not engine.exists():
            raise UserProfileError(f"Bound user profile does not exist: {binding['profile_id']}")
        engine.require_valid()
        if require_enabled and not engine.state()["global_enabled"]:
            raise UserProfileError("Cross-course personalization is globally disabled for this profile")
        return engine, binding

    def exists(self) -> bool:
        return self.state_path.is_file()

    def state(self) -> dict[str, Any]:
        if not self.exists():
            raise UserProfileError(f"User profile does not exist: {self.profile_id}")
        return load_yaml(self.state_path)

    @property
    def revision(self) -> int:
        revision = self.state().get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise UserProfileError("profile revision must be a non-negative integer")
        return revision

    def signals(self) -> list[dict[str, Any]]:
        return json_lines(self.signals_path)

    def ledger(self) -> list[dict[str, Any]]:
        return json_lines(self.ledger_path)

    def initialize(self, enabled: bool = True) -> dict[str, Any]:
        with FileLock(self.lock_path):
            if self.exists():
                return self.state()
            version = core_version()
            state = {
                "kind": "atomlearn.user-profile",
                "schema_version": 1,
                "created_by_core_version": version,
                "last_written_by_core_version": version,
                "min_reader_core_version": version,
                "revision": 1,
                "profile_id": self.profile_id,
                "global_enabled": enabled,
                "policy": {
                    "store_raw_messages": False,
                    "infer_sensitive_traits": False,
                    "behavioral_min_sessions": 2,
                    "inferred_min_confidence": 0.7,
                    "conflict_margin": 0.15,
                },
                "preferences": {},
            }
            errors = validate_with_schema(state, PROFILE_SCHEMA)
            if errors:  # pragma: no cover - construction guard
                raise UserProfileError("Cannot initialize invalid profile:\n- " + "\n- ".join(errors))
            event = {
                "event_id": "upevt-000001",
                "revision": 1,
                "type": "user_profile.created",
                "at": iso(),
                "details": {"global_enabled": enabled},
            }
            atomic_text(self.signals_path, "")
            atomic_yaml(self.state_path, state)
            atomic_text(self.ledger_path, serialize_json_lines([event]))
            return state

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and self.revision != expected:
            raise UserProfileError(f"Stale user profile revision: expected {expected}, current is {self.revision}")

    def _normalize_signal(
        self,
        raw: Any,
        session_id: str,
        context: str,
        index: int,
        record_id: str,
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise UserProfileError(f"signals[{index}] must be a mapping")
        allowed = {"dimension", "value", "direction", "evidence", "reason_code", "confidence", "turn_refs"}
        extra = sorted(set(raw) - allowed)
        if extra:
            raise UserProfileError(
                f"signals[{index}] contains unsupported fields: {', '.join(extra)}; never pass raw message text"
            )
        dimension = require_string(raw.get("dimension"), f"signals[{index}].dimension")
        if dimension not in PREFERENCE_VALUES:
            raise UserProfileError(f"signals[{index}].dimension is unsupported: {dimension!r}")
        value = require_string(raw.get("value"), f"signals[{index}].value")
        if value not in PREFERENCE_VALUES[dimension]:
            raise UserProfileError(
                f"signals[{index}].value must be one of: {', '.join(sorted(PREFERENCE_VALUES[dimension]))}"
            )
        direction = raw.get("direction")
        evidence = raw.get("evidence")
        reason_code = raw.get("reason_code")
        if direction not in DIRECTIONS:
            raise UserProfileError(f"signals[{index}].direction must be prefer or avoid")
        if evidence not in EVIDENCE_TYPES:
            raise UserProfileError(f"signals[{index}].evidence must be explicit, behavioral, or outcome")
        if reason_code not in REASON_CODES[evidence]:
            raise UserProfileError(f"signals[{index}].reason_code is invalid for {evidence} evidence")
        confidence = require_number(raw.get("confidence"), f"signals[{index}].confidence", 0.5, 1.0)
        turn_refs = raw.get("turn_refs", [])
        if not isinstance(turn_refs, list) or len(turn_refs) > MAX_TURN_REFS:
            raise UserProfileError(f"signals[{index}].turn_refs must contain at most {MAX_TURN_REFS} opaque IDs")
        normalized_refs: list[str] = []
        for reference in turn_refs:
            if not isinstance(reference, str) or not OPAQUE_ID.fullmatch(reference):
                raise UserProfileError(f"signals[{index}].turn_refs contains an invalid opaque ID")
            normalized_refs.append(reference)
        return {
            "schema_version": 1,
            "id": record_id,
            "type": "user.preference.signal",
            "session_id": session_id,
            "context": context,
            "dimension": dimension,
            "value": value,
            "direction": direction,
            "evidence": evidence,
            "reason_code": reason_code,
            "confidence": round(float(confidence), 3),
            "turn_refs": unique(normalized_refs),
            "observed_at": iso(),
        }

    def _derive_preferences(self, records: list[dict[str, Any]], revision: int) -> dict[str, Any]:
        state = self.state()
        policy = state["policy"]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if record.get("dimension") in PREFERENCE_VALUES:
                grouped[record["dimension"]].append(record)
        result: dict[str, Any] = {}
        for dimension, dimension_records in sorted(grouped.items()):
            retirement = max(
                (index for index, record in enumerate(dimension_records) if record.get("type") == "user.preference.retired"),
                default=-1,
            )
            active_records = dimension_records[retirement + 1 :]
            signals = [record for record in active_records if record.get("type") == "user.preference.signal"]
            if not signals:
                if retirement >= 0:
                    result[dimension] = {
                        "status": "retired",
                        "source": "retired",
                        "active_value": None,
                        "updated_revision": revision,
                        "confidence": 0.0,
                        "session_count": 0,
                        "signal_ids": [],
                    }
                continue
            explicit_value: str | None = None
            explicit_signal: dict[str, Any] | None = None
            avoided: set[str] = set()
            for signal in signals:
                if signal["evidence"] != "explicit":
                    continue
                if signal["direction"] == "prefer":
                    avoided.discard(signal["value"])
                    explicit_value = signal["value"]
                    explicit_signal = signal
                else:
                    avoided.add(signal["value"])
                    if signal["value"] == explicit_value:
                        explicit_value = None
                        explicit_signal = None
            if explicit_value is not None and explicit_signal is not None:
                supporting = [explicit_signal["id"]]
                session_count = len({item["session_id"] for item in signals if item["value"] == explicit_value})
                result[dimension] = {
                    "status": "active",
                    "source": "explicit",
                    "active_value": explicit_value,
                    "updated_revision": revision,
                    "confidence": max(0.9, float(explicit_signal["confidence"])),
                    "session_count": session_count,
                    "signal_ids": supporting,
                }
                continue
            candidates: list[tuple[str, float, int, list[str]]] = []
            for value in sorted(PREFERENCE_VALUES[dimension]):
                support: dict[str, float] = {}
                oppose: dict[str, float] = {}
                ids: list[str] = []
                for signal in signals:
                    if signal["evidence"] == "explicit" or signal["value"] != value:
                        continue
                    target = support if signal["direction"] == "prefer" else oppose
                    target[signal["session_id"]] = max(target.get(signal["session_id"], 0.0), signal["confidence"])
                    if signal["direction"] == "prefer":
                        ids.append(signal["id"])
                positive = sum(support.values()) / len(support) if support else 0.0
                negative = sum(oppose.values()) / len(oppose) if oppose else 0.0
                score = 0.0 if value in avoided else max(0.0, min(1.0, positive - 0.5 * negative))
                if support or oppose:
                    candidates.append((value, round(score, 3), len(support), unique(ids)))
            candidates.sort(key=lambda item: (-item[1], -item[2], item[0]))
            top = candidates[0] if candidates else (None, 0.0, 0, [])
            second_score = candidates[1][1] if len(candidates) > 1 else 0.0
            ready = bool(
                top[0]
                and top[2] >= int(policy["behavioral_min_sessions"])
                and top[1] >= float(policy["inferred_min_confidence"])
            )
            contested = ready and second_score > 0 and top[1] - second_score < float(policy["conflict_margin"])
            result[dimension] = {
                "status": "contested" if contested else ("active" if ready else "provisional"),
                "source": "inferred" if ready and not contested else "unconfirmed",
                "active_value": top[0] if ready and not contested else None,
                "updated_revision": revision,
                "confidence": top[1],
                "session_count": top[2],
                "signal_ids": top[3],
            }
        return result

    def _commit(
        self,
        state: dict[str, Any],
        signals: list[dict[str, Any]],
        event_type: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.state()
        revision = current["revision"] + 1
        state = dict(state)
        state["revision"] = revision
        state["created_by_core_version"] = current["created_by_core_version"]
        state["last_written_by_core_version"] = core_version()
        state["min_reader_core_version"] = current["min_reader_core_version"]
        errors = validate_with_schema(state, PROFILE_SCHEMA)
        if errors:
            raise UserProfileError("Refusing to write invalid user profile:\n- " + "\n- ".join(errors))
        events = self.ledger()
        events.append(
            {
                "event_id": f"upevt-{revision:06d}",
                "revision": revision,
                "type": event_type,
                "at": iso(),
                "details": details,
            }
        )
        atomic_text(self.signals_path, serialize_json_lines(signals))
        atomic_yaml(self.state_path, state)
        atomic_text(self.ledger_path, serialize_json_lines(events))
        return state

    def set_enabled(self, enabled: bool, expected_revision: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected_revision)
            state = self.state()
            if state["global_enabled"] == enabled:
                return state
            state["global_enabled"] = enabled
            return self._commit(
                state,
                self.signals(),
                "user_profile.enabled" if enabled else "user_profile.disabled",
                {"global_enabled": enabled},
            )

    def observe_session(self, payload: Any, expected_revision: int | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise UserProfileError("session observation must be a mapping")
        extra = sorted(set(payload) - {"session_id", "context", "scope", "signals"})
        if extra:
            raise UserProfileError(
                "session observation contains unsupported fields: " + ", ".join(extra) + "; never pass raw messages"
            )
        if payload.get("scope", "user") != "user":
            raise UserProfileError("UserProfileEngine only accepts scope: user")
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not OPAQUE_ID.fullmatch(session_id):
            raise UserProfileError("session_id must be an opaque ID")
        context = payload.get("context", "general")
        if context not in ADAPTATION_CONTEXTS:
            raise UserProfileError(f"context must be one of: {', '.join(sorted(ADAPTATION_CONTEXTS))}")
        raw_signals = payload.get("signals")
        if not isinstance(raw_signals, list) or not raw_signals or len(raw_signals) > MAX_SIGNALS_PER_SESSION:
            raise UserProfileError(f"signals must contain 1-{MAX_SIGNALS_PER_SESSION} entries")
        with FileLock(self.lock_path):
            self.expect_revision(expected_revision)
            state = self.state()
            if not state["global_enabled"]:
                raise UserProfileError("Cross-course personalization is globally disabled")
            records = self.signals()
            known_sessions = {record.get("session_id") for record in records if record.get("type") == "user.preference.signal"}
            if session_id in known_sessions:
                raise UserProfileError(f"session has already been observed in the global profile: {session_id}")
            offset = sum(record.get("type") == "user.preference.signal" for record in records)
            normalized = [
                self._normalize_signal(raw, session_id, context, index, f"ups-{offset + index + 1:06d}")
                for index, raw in enumerate(raw_signals)
            ]
            records.extend(normalized)
            next_revision = state["revision"] + 1
            state["preferences"] = self._derive_preferences(records, next_revision)
            committed = self._commit(
                state,
                records,
                "user_profile.session_observed",
                {"session_id": session_id, "signal_ids": [record["id"] for record in normalized]},
            )
            return {
                "profile_revision": committed["revision"],
                "session_id": session_id,
                "signal_ids": [record["id"] for record in normalized],
                "preferences": committed["preferences"],
            }

    def retire(self, dimension: str, reason_code: str, expected_revision: int | None = None) -> dict[str, Any]:
        if dimension not in PREFERENCE_VALUES:
            raise UserProfileError(f"unsupported preference dimension: {dimension!r}")
        if reason_code not in RETIRE_REASONS:
            raise UserProfileError(f"retire reason must be one of: {', '.join(sorted(RETIRE_REASONS))}")
        with FileLock(self.lock_path):
            self.expect_revision(expected_revision)
            state = self.state()
            records = self.signals()
            record = {
                "schema_version": 1,
                "id": f"upr-{sum(item.get('type') == 'user.preference.retired' for item in records) + 1:06d}",
                "type": "user.preference.retired",
                "dimension": dimension,
                "reason_code": reason_code,
                "observed_at": iso(),
            }
            records.append(record)
            next_revision = state["revision"] + 1
            state["preferences"] = self._derive_preferences(records, next_revision)
            committed = self._commit(
                state,
                records,
                "user_profile.preference_retired",
                {"dimension": dimension, "record_id": record["id"]},
            )
            return {"profile_revision": committed["revision"], "dimension": dimension, "status": "retired"}

    def reset(self, expected_revision: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self.expect_revision(expected_revision)
            state = self.state()
            records = self.signals()
            active_dimensions = [
                dimension for dimension, item in state["preferences"].items() if item.get("status") != "retired"
            ]
            for dimension in active_dimensions:
                records.append(
                    {
                        "schema_version": 1,
                        "id": f"upr-{sum(item.get('type') == 'user.preference.retired' for item in records) + 1:06d}",
                        "type": "user.preference.retired",
                        "dimension": dimension,
                        "reason_code": "privacy_request",
                        "observed_at": iso(),
                    }
                )
            next_revision = state["revision"] + 1
            state["global_enabled"] = False
            state["preferences"] = self._derive_preferences(records, next_revision)
            committed = self._commit(
                state,
                records,
                "user_profile.reset",
                {"retired_dimensions": sorted(active_dimensions), "global_enabled": False},
            )
            return {"profile_revision": committed["revision"], "retired_dimensions": sorted(active_dimensions)}

    def validate(self) -> list[str]:
        if not self.exists():
            return [f"User profile does not exist: {self.profile_id}"]
        state = self.state()
        errors = validate_with_schema(state, PROFILE_SCHEMA)
        records = self.signals()
        events = self.ledger()
        signal_ids: set[str] = set()
        sessions: set[str] = set()
        allowed_signal_fields = {
            "schema_version", "id", "type", "session_id", "context", "dimension", "value", "direction",
            "evidence", "reason_code", "confidence", "turn_refs", "observed_at",
        }
        allowed_retire_fields = {"schema_version", "id", "type", "dimension", "reason_code", "observed_at"}
        for index, record in enumerate(records, start=1):
            record_type = record.get("type")
            allowed = allowed_signal_fields if record_type == "user.preference.signal" else allowed_retire_fields
            if record_type not in {"user.preference.signal", "user.preference.retired"} or set(record) != allowed:
                errors.append(f"global signal record {index} fields are invalid")
                continue
            identifier = record.get("id")
            if not isinstance(identifier, str) or identifier in signal_ids:
                errors.append(f"global signal record {index} has invalid or duplicate ID")
            signal_ids.add(str(identifier))
            if record_type == "user.preference.signal":
                session_id = record.get("session_id")
                if not isinstance(session_id, str) or not OPAQUE_ID.fullmatch(session_id):
                    errors.append(f"global signal record {index} has invalid session ID")
                sessions.add(str(session_id))
                if record.get("dimension") not in PREFERENCE_VALUES:
                    errors.append(f"global signal record {index} has unsupported dimension")
                elif record.get("value") not in PREFERENCE_VALUES[record["dimension"]]:
                    errors.append(f"global signal record {index} has unsupported value")
        if len(events) != state.get("revision"):
            errors.append("user profile ledger length does not match revision")
        for index, event in enumerate(events, start=1):
            if set(event) != {"event_id", "revision", "type", "at", "details"}:
                errors.append(f"user profile event {index} fields are invalid")
            if event.get("revision") != index or event.get("event_id") != f"upevt-{index:06d}":
                errors.append(f"user profile event {index} revision is invalid")
            if not isinstance(event.get("details"), dict):
                errors.append(f"user profile event {index} details must be a mapping")
                continue
            expected_detail_fields = {
                "user_profile.created": {"global_enabled"},
                "user_profile.enabled": {"global_enabled"},
                "user_profile.disabled": {"global_enabled"},
                "user_profile.session_observed": {"session_id", "signal_ids"},
                "user_profile.preference_retired": {"dimension", "record_id"},
                "user_profile.reset": {"retired_dimensions", "global_enabled"},
            }.get(event.get("type"))
            if expected_detail_fields is None or set(event["details"]) != expected_detail_fields:
                errors.append(f"user profile event {index} details are invalid")
        try:
            expected = self._derive_preferences(records, state.get("revision", 0))
        except (KeyError, TypeError, ValueError, UserProfileError) as exc:
            errors.append(f"user profile cannot be derived from its policy and signals: {exc}")
        else:
            if state.get("preferences") != expected:
                errors.append("user profile preferences do not match the signal ledger")
        return unique(errors)

    def require_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise UserProfileError("User profile validation failed:\n- " + "\n- ".join(errors))

    def status(self) -> dict[str, Any]:
        state = self.state()
        errors = self.validate()
        preferences = state.get("preferences", {})
        return {
            "valid": not errors,
            "validation_errors": errors,
            "profile_id": self.profile_id,
            "profile_revision": state["revision"],
            "global_enabled": state["global_enabled"],
            "observed_sessions": len({
                record["session_id"] for record in self.signals() if record.get("type") == "user.preference.signal"
            }),
            "active_preferences": sum(item.get("status") == "active" for item in preferences.values()),
            "pending_preferences": sum(item.get("status") in {"provisional", "contested"} for item in preferences.values()),
            "privacy": {"raw_messages_stored": False, "sensitive_traits_inferred": False},
        }


def enable_profile(
    workspace: str | Path,
    profile_id: str,
    data_dir: str | Path | None,
    expected_profile_revision: int | None,
    expected_binding_revision: int | None,
) -> dict[str, Any]:
    loaded = load_workspace(str(workspace))
    engine = UserProfileEngine.at_default_root(profile_id, data_dir)
    created = not engine.exists()
    state = engine.initialize(enabled=True)
    if not created:
        engine.require_valid()
        engine.expect_revision(expected_profile_revision)
        if not state["global_enabled"]:
            state = engine.set_enabled(True, expected_profile_revision)
    binding = WorkspaceBinding(loaded.root).write(profile_id, True, expected_binding_revision)
    return {
        "profile_id": profile_id,
        "profile_revision": state["revision"],
        "binding_revision": binding["revision"],
        "created": created,
        "enabled": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage opt-in cross-course learner profiles")
    sub = parser.add_subparsers(dest="action", required=True)
    for action, help_text in [
        ("status", "Show global profile and workspace binding status"),
        ("show", "Print the complete canonical global preference profile"),
        ("validate", "Validate global profile schema signals and ledger"),
    ]:
        command = sub.add_parser(action, help=help_text)
        command.add_argument("workspace")
        command.add_argument("--data-dir")
    enable = sub.add_parser("enable", help="Explicitly enable global personalization for one workspace")
    enable.add_argument("workspace")
    enable.add_argument("--profile", default="default")
    enable.add_argument("--data-dir")
    enable.add_argument("--expected-profile-revision", type=int)
    enable.add_argument("--expected-binding-revision", type=int)
    disable = sub.add_parser("disable", help="Stop global profile guidance for one workspace")
    disable.add_argument("workspace")
    disable.add_argument("--data-dir")
    disable.add_argument("--all", action="store_true", help="Also disable the global profile itself")
    disable.add_argument("--expected-profile-revision", type=int)
    disable.add_argument("--expected-binding-revision", type=int)
    observe = sub.add_parser("observe-session", help="Store one enum-only user-scope preference observation")
    observe.add_argument("workspace")
    observe.add_argument("--input", required=True)
    observe.add_argument("--data-dir")
    observe.add_argument("--expected-profile-revision", type=int)
    promote = sub.add_parser("promote-preference", help="Promote one explicit workspace preference to user scope")
    promote.add_argument("workspace")
    promote.add_argument("dimension", choices=sorted(PREFERENCE_VALUES))
    promote.add_argument("--data-dir")
    promote.add_argument("--expected-profile-revision", type=int)
    retire = sub.add_parser("retire", help="Retire one global preference without deleting history")
    retire.add_argument("workspace")
    retire.add_argument("dimension", choices=sorted(PREFERENCE_VALUES))
    retire.add_argument("--reason-code", choices=sorted(RETIRE_REASONS), required=True)
    retire.add_argument("--data-dir")
    retire.add_argument("--expected-profile-revision", type=int)
    export = sub.add_parser("export", help="Export an inspectable local copy of profile state")
    export.add_argument("workspace")
    export.add_argument("--output", required=True)
    export.add_argument("--data-dir")
    reset = sub.add_parser("reset", help="Disable and retire global preferences while preserving audit history")
    reset.add_argument("workspace")
    reset.add_argument("--data-dir")
    reset.add_argument("--expected-profile-revision", type=int)
    reset.add_argument("--confirmed", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "enable":
        result = enable_profile(
            args.workspace,
            args.profile,
            args.data_dir,
            args.expected_profile_revision,
            args.expected_binding_revision,
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return
    engine, binding = UserProfileEngine.for_workspace(
        args.workspace,
        args.data_dir,
        require_enabled=args.action in {"observe-session", "promote-preference"},
    )
    if args.action == "status":
        print(json.dumps({**engine.status(), "binding": binding}, ensure_ascii=False, indent=2))
    elif args.action == "show":
        print(json.dumps(engine.state(), ensure_ascii=False, indent=2))
    elif args.action == "validate":
        engine.require_valid()
        print(json.dumps({"ok": True, "profile_revision": engine.revision}))
    elif args.action == "disable":
        loaded = load_workspace(args.workspace)
        updated_binding = WorkspaceBinding(loaded.root).write(
            binding["profile_id"], False, args.expected_binding_revision
        )
        state = engine.set_enabled(False, args.expected_profile_revision) if args.all else engine.state()
        print(json.dumps({"ok": True, "binding": updated_binding, "profile_revision": state["revision"]}, indent=2))
    elif args.action == "observe-session":
        payload = load_yaml(Path(args.input))
        payload.setdefault("scope", "user")
        result = engine.observe_session(payload, args.expected_profile_revision)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    elif args.action == "promote-preference":
        from adaptation import AdaptationEngine

        local = AdaptationEngine.load(args.workspace)
        errors = local.validate()
        if errors:
            raise UserProfileError("Workspace adaptation is invalid:\n- " + "\n- ".join(errors))
        preference = local.profile.get("preferences", {}).get(args.dimension)
        if not isinstance(preference, dict) or preference.get("status") != "active" or preference.get("source") != "explicit":
            raise UserProfileError("Only an active explicit workspace preference can be promoted")
        payload = {
            "session_id": "promotion-" + uuid.uuid4().hex,
            "context": "general",
            "scope": "user",
            "signals": [{
                "dimension": args.dimension,
                "value": preference["active_value"],
                "direction": "prefer",
                "evidence": "explicit",
                "reason_code": "user_confirmation",
                "confidence": 1.0,
                "turn_refs": [],
            }],
        }
        result = engine.observe_session(payload, args.expected_profile_revision)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    elif args.action == "retire":
        result = engine.retire(args.dimension, args.reason_code, args.expected_profile_revision)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    elif args.action == "export":
        output = Path(args.output).resolve(strict=False)
        if output.exists():
            raise UserProfileError(f"Refusing to overwrite existing export: {output}")
        document = {
            "export_kind": "atomlearn.user-profile.local-copy",
            "exported_at": iso(),
            "state": engine.state(),
            "signals": engine.signals(),
            "ledger": engine.ledger(),
        }
        atomic_yaml(output, document)
        print(json.dumps({"ok": True, "output": str(output), "profile_revision": engine.revision}, indent=2))
    elif args.action == "reset":
        if not args.confirmed:
            raise UserProfileError("Reset requires --confirmed; history will be retained as retirement tombstones")
        result = engine.reset(args.expected_profile_revision)
        loaded = load_workspace(args.workspace)
        updated_binding = WorkspaceBinding(loaded.root).write(binding["profile_id"], False, binding["revision"])
        print(json.dumps({"ok": True, **result, "binding": updated_binding}, ensure_ascii=False, indent=2))
    else:  # pragma: no cover
        raise UserProfileError(f"Unhandled profile action: {args.action}")


def main() -> int:
    try:
        run()
        return 0
    except (UserProfileError, PlatformStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
