#!/usr/bin/env python3
"""Explicitly consented, privacy-minimized real learning-effect study records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import iso
from platform_state import CORE_ROOT, FileLock, PlatformStateError, atomic_text, atomic_yaml, resolve_user_data_root
from user_profile import json_lines, load_yaml, serialize_json_lines


STUDY_SCHEMA = CORE_ROOT / "assets" / "schemas" / "learning-study.schema.json"
OBSERVATION_SCHEMA = CORE_ROOT / "assets" / "schemas" / "learning-study-observation.schema.json"
MEASUREMENT_CATEGORIES = {
    "immediate_mastery": "assessment_scores",
    "delayed_retention_7d": "assessment_scores",
    "delayed_retention_30d": "assessment_scores",
    "near_transfer": "assessment_scores",
    "far_transfer": "assessment_scores",
    "completion": "process_counts",
    "withdrawal": "process_counts",
    "total_time": "timing_buckets",
    "prompt_burden": "process_counts",
}


class LearningStudyError(RuntimeError):
    """A user-correctable learning-study contract error."""


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise LearningStudyError(f"Schema is not an object: {path}")
    return value


def _errors(value: dict[str, Any], schema_path: Path) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(_schema(schema_path)).iter_errors(value), key=lambda item: list(item.path))
    ]


def _event(revision: int, event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": f"study-event-{revision:06d}",
        "revision": revision,
        "type": event_type,
        "details": details,
        "at": iso(),
    }


class LearningStudyEngine:
    """Local-only study namespace with independent consent and withdrawal."""

    def __init__(self, data_root: Path, study_id: str):
        self.data_root = data_root.resolve(strict=False)
        self.study_id = study_id
        self.root = self.data_root / "learning-studies" / study_id
        self.state_path = self.root / "state.yaml"
        self.observations_path = self.root / "observations.ndjson"
        self.ledger_path = self.root / "ledger.ndjson"
        self.lock_path = self.root / ".study.lock"

    @classmethod
    def at_default_root(cls, study_id: str, data_dir: str | Path | None = None) -> "LearningStudyEngine":
        return cls(resolve_user_data_root(data_dir, create=False), study_id)

    def exists(self) -> bool:
        return self.state_path.is_file()

    def state(self) -> dict[str, Any]:
        if not self.exists():
            raise LearningStudyError(f"Unknown learning study: {self.study_id}")
        return load_yaml(self.state_path)

    def observations(self) -> list[dict[str, Any]]:
        return json_lines(self.observations_path)

    def ledger(self) -> list[dict[str, Any]]:
        return json_lines(self.ledger_path)

    @property
    def revision(self) -> int:
        value = self.state().get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise LearningStudyError("learning-study revision must be a positive integer")
        return value

    def _expect(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise LearningStudyError(
                f"Stale learning-study revision: expected {expected}, current is {self.revision}"
            )

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.exists():
            raise LearningStudyError(f"Learning study already exists: {self.study_id}")
        if payload.get("study_id") != self.study_id:
            raise LearningStudyError("study_id in the enrollment input must match the command")
        allowed = {"study_id", "consent", "design"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise LearningStudyError("Enrollment contains unsupported or content-bearing fields: " + ", ".join(unexpected))
        consent = payload.get("consent")
        if not isinstance(consent, dict) or consent.get("explicit") is not True:
            raise LearningStudyError("Enrollment requires separate explicit learning-study consent")
        now = iso()
        state = {
            "kind": "atomlearn.learning-study",
            "schema_version": 1,
            "revision": 1,
            "study_id": self.study_id,
            "status": "enrolled",
            "consent": consent,
            "design": payload.get("design"),
            "privacy": {
                "raw_answers": False,
                "content_text": False,
                "opaque_refs_only": True,
                "local_only": True,
                "automatic_export": False,
            },
            "created_at": now,
            "updated_at": now,
        }
        errors = _errors(state, STUDY_SCHEMA)
        if errors:
            raise LearningStudyError("Learning-study enrollment is invalid:\n- " + "\n- ".join(errors))
        with FileLock(self.lock_path):
            if self.exists():
                raise LearningStudyError(f"Learning study already exists: {self.study_id}")
            atomic_text(self.observations_path, "")
            atomic_text(self.ledger_path, serialize_json_lines([_event(1, "study.enrolled", {
                "study_id": self.study_id,
                "consent_version": consent["consent_version"],
                "data_categories": consent["data_categories"],
            })]))
            atomic_yaml(self.state_path, state)
        return state

    def _commit(
        self,
        state: dict[str, Any],
        event_type: str,
        details: dict[str, Any],
        *,
        observations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        current = self.state()
        value = dict(state)
        value["revision"] = current["revision"] + 1
        value["created_at"] = current["created_at"]
        value["updated_at"] = iso()
        errors = _errors(value, STUDY_SCHEMA)
        if errors:
            raise LearningStudyError("Refusing to write invalid learning-study state:\n- " + "\n- ".join(errors))
        if observations is not None:
            observation_errors: list[str] = []
            for item in observations:
                observation_errors.extend(f"{item.get('id')}: {error}" for error in _errors(item, OBSERVATION_SCHEMA))
            if observation_errors:
                raise LearningStudyError("Refusing to write invalid observations:\n- " + "\n- ".join(observation_errors))
            atomic_text(self.observations_path, serialize_json_lines(observations))
        ledger = self.ledger()
        ledger.append(_event(value["revision"], event_type, details))
        atomic_text(self.ledger_path, serialize_json_lines(ledger))
        # State is the commit marker and is written last.
        atomic_yaml(self.state_path, value)
        return value

    def record(self, payload: dict[str, Any], expected: int | None = None) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self._expect(expected)
            state = self.state()
            if state["status"] != "enrolled":
                raise LearningStudyError(f"Cannot record an observation while study status is {state['status']}")
            system_fields = {"kind", "schema_version", "id", "study_id", "included_in_analysis", "recorded_at"}
            unexpected = sorted(system_fields.intersection(payload))
            if unexpected:
                raise LearningStudyError("Observation system fields are generated by Core: " + ", ".join(unexpected))
            observation = {
                "kind": "atomlearn.learning-study-observation",
                "schema_version": 1,
                "id": "obs-" + "0" * 24,
                "study_id": self.study_id,
                **payload,
                "included_in_analysis": True,
                "recorded_at": iso(),
            }
            errors = _errors(observation, OBSERVATION_SCHEMA)
            if errors:
                raise LearningStudyError("Learning-study observation is invalid:\n- " + "\n- ".join(errors))
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            observation_id = "obs-" + hashlib.sha256(
                f"{self.study_id}|{canonical}".encode("utf-8")
            ).hexdigest()[:24]
            observation["id"] = observation_id
            categories = set(state["consent"]["data_categories"])
            required = {MEASUREMENT_CATEGORIES[observation["measurement_kind"]]}
            if observation["score"] is not None:
                required.add("assessment_scores")
            if observation["prompt_count"]:
                required.add("process_counts")
            if observation["duration_bucket"] != "unknown":
                required.add("timing_buckets")
            if "ux" in observation:
                required.add("ux_ratings")
            missing = sorted(required - categories)
            if missing:
                raise LearningStudyError("Observation exceeds consented data categories: " + ", ".join(missing))
            records = self.observations()
            duplicate_key = (
                observation["participant_ref"], observation["episode_ref"], observation["measurement_kind"]
            )
            if any(
                (item["participant_ref"], item["episode_ref"], item["measurement_kind"]) == duplicate_key
                for item in records
            ):
                raise LearningStudyError("This participant/episode/measurement observation is already recorded")
            records.append(observation)
            committed = self._commit(
                state,
                "study.observation_recorded",
                {"observation_id": observation_id, "measurement_kind": observation["measurement_kind"]},
                observations=records,
            )
            return {"study_revision": committed["revision"], "observation": observation}

    def withdraw(self, confirmed: bool, expected: int | None = None) -> dict[str, Any]:
        if not confirmed:
            raise LearningStudyError("Withdrawal requires --confirmed")
        with FileLock(self.lock_path):
            self._expect(expected)
            state = self.state()
            if state["status"] == "withdrawn":
                return {"study_revision": state["revision"], "status": "withdrawn", "excluded_observations": 0}
            if state["status"] != "enrolled":
                raise LearningStudyError(f"Cannot withdraw a study while status is {state['status']}")
            records = self.observations()
            excluded = sum(item.get("included_in_analysis") is True for item in records)
            for item in records:
                item["included_in_analysis"] = False
            value = dict(state)
            value["status"] = "withdrawn"
            value["withdrawn_at"] = iso()
            committed = self._commit(
                value,
                "study.withdrawn",
                {"excluded_observations": excluded},
                observations=records,
            )
            return {
                "study_revision": committed["revision"],
                "status": "withdrawn",
                "excluded_observations": excluded,
            }

    def validate(self) -> list[str]:
        errors = _errors(self.state(), STUDY_SCHEMA)
        records = self.observations()
        seen_ids: set[str] = set()
        seen_keys: set[tuple[str, str, str]] = set()
        for item in records:
            errors.extend(f"observation {item.get('id')}: {error}" for error in _errors(item, OBSERVATION_SCHEMA))
            if item.get("id") in seen_ids:
                errors.append(f"duplicate observation ID: {item.get('id')}")
            seen_ids.add(str(item.get("id")))
            key = (str(item.get("participant_ref")), str(item.get("episode_ref")), str(item.get("measurement_kind")))
            if key in seen_keys:
                errors.append("duplicate participant/episode/measurement observation")
            seen_keys.add(key)
        state = self.state()
        if state["status"] == "withdrawn" and any(item.get("included_in_analysis") for item in records):
            errors.append("withdrawn study retains analysis-eligible observations")
        ledger = self.ledger()
        if len(ledger) != state["revision"]:
            errors.append("learning-study ledger length does not match state revision")
        for index, event in enumerate(ledger, start=1):
            if event.get("revision") != index or event.get("id") != f"study-event-{index:06d}":
                errors.append(f"learning-study ledger event {index} has an invalid revision or ID")
        return errors

    def require_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise LearningStudyError("Learning-study validation failed:\n- " + "\n- ".join(errors))

    def status(self) -> dict[str, Any]:
        self.require_valid()
        state = self.state()
        observations = self.observations()
        return {
            "study_id": self.study_id,
            "status": state["status"],
            "study_revision": state["revision"],
            "consent_version": state["consent"]["consent_version"],
            "privacy": state["privacy"],
            "observation_count": len(observations),
            "included_observation_count": sum(item["included_in_analysis"] for item in observations),
            "measurement_counts": dict(sorted(Counter(item["measurement_kind"] for item in observations).items())),
            "learning_effect_claim_supported": False,
            "limitations": [
                "This namespace records minimized local observations; it does not automatically aggregate or export them.",
                "Engineering and calibration tests do not establish a real learning benefit.",
            ],
        }


def _payload(path: str) -> dict[str, Any]:
    return load_yaml(Path(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage explicit, local-only real learning-effect study records")
    parser.add_argument("--data-dir", help="Absolute AtomLearn user-data root (or use ATOMLEARN_DATA_DIR)")
    sub = parser.add_subparsers(dest="action", required=True)
    enroll = sub.add_parser("enroll", help="Create a study after separate explicit informed consent")
    enroll.add_argument("study_id")
    enroll.add_argument("--input", required=True)
    record = sub.add_parser("record", help="Append one schema-checked minimized observation without content text")
    record.add_argument("study_id")
    record.add_argument("--input", required=True)
    record.add_argument("--expected-study-revision", type=int)
    status = sub.add_parser("status", help="Show consent, privacy, inclusion, and measurement summaries")
    status.add_argument("study_id")
    validate = sub.add_parser("validate", help="Validate study state, observations, withdrawal, and audit ledger")
    validate.add_argument("study_id")
    withdraw = sub.add_parser("withdraw", help="Withdraw consent and exclude retained observations from analysis")
    withdraw.add_argument("study_id")
    withdraw.add_argument("--confirmed", action="store_true")
    withdraw.add_argument("--expected-study-revision", type=int)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    engine = LearningStudyEngine.at_default_root(args.study_id, args.data_dir)
    if args.action == "enroll":
        result = engine.enroll(_payload(args.input))
    elif args.action == "record":
        result = engine.record(_payload(args.input), args.expected_study_revision)
    elif args.action == "status":
        result = engine.status()
    elif args.action == "validate":
        engine.require_valid()
        result = {"ok": True, "study_id": args.study_id, "study_revision": engine.revision}
    elif args.action == "withdraw":
        result = engine.withdraw(args.confirmed, args.expected_study_revision)
    else:  # pragma: no cover
        raise LearningStudyError(f"Unhandled learning-study action: {args.action}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (LearningStudyError, PlatformStateError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
