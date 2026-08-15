#!/usr/bin/env python3
"""Privacy-preserving session adaptation for AtomLearn workspaces."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from atomlearn import (
    SCHEMA_VERSION,
    AtomLearnError,
    Workspace,
    atomic_text,
    iso,
    load_workspace,
    parse_time,
    read_data,
    require_number,
    require_string,
    unique,
    write_yaml,
)


ADAPTATION_CONTEXTS = {"general", "orientation", "teaching", "review", "research", "exam"}
DIRECTIONS = {"prefer", "avoid"}
EVIDENCE_TYPES = {"explicit", "behavioral", "outcome"}
REASON_CODES = {
    "explicit": {"explicit_request", "user_confirmation", "user_correction", "user_rejection"},
    "behavioral": {"repeated_request", "format_correction", "accepted_format", "abandoned_format"},
    "outcome": {"task_success", "task_failure", "mastery_improved", "mastery_struggled"},
}
RETIRE_REASONS = {"user_rejection", "user_correction", "privacy_request", "no_longer_relevant"}
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_SIGNALS_PER_SESSION = 20
MAX_TURN_REFS = 10

PREFERENCE_VALUES: dict[str, set[str]] = {
    "response.detail": {"concise", "balanced", "detailed"},
    "answer.structure": {"prose", "checklist", "step_by_step", "mixed"},
    "language.mode": {"chinese", "english", "bilingual", "match_user"},
    "explanation.order": {"intuition_first", "example_first", "formal_first", "mixed"},
    "example.mode": {"practical", "code", "visual", "analogy", "theoretical", "mixed"},
    "interaction.pacing": {"one_atom", "short_batch", "user_led"},
    "teaching.mode": {"direct", "socratic", "guided_discovery", "mixed"},
    "feedback.style": {"direct", "supportive", "neutral"},
    "notation.level": {"plain", "mixed", "formal"},
    "challenge.level": {"gentle", "standard", "stretch"},
    "research.orientation": {"breadth_first", "depth_first", "evidence_first", "application_first"},
    "source.priority": {"user_materials", "primary_sources", "textbooks", "mixed"},
}

DIMENSION_CONTEXTS: dict[str, set[str]] = {
    "response.detail": ADAPTATION_CONTEXTS,
    "answer.structure": ADAPTATION_CONTEXTS,
    "language.mode": ADAPTATION_CONTEXTS,
    "explanation.order": {"orientation", "teaching", "review", "exam"},
    "example.mode": {"orientation", "teaching", "review", "exam"},
    "interaction.pacing": {"orientation", "teaching", "review", "exam"},
    "teaching.mode": {"teaching", "review", "exam"},
    "feedback.style": {"teaching", "review", "exam"},
    "notation.level": {"orientation", "teaching", "review", "research", "exam"},
    "challenge.level": {"teaching", "review", "exam"},
    "research.orientation": {"research"},
    "source.priority": {"orientation", "teaching", "review", "research", "exam"},
}

GUIDANCE: dict[tuple[str, str], str] = {
    ("response.detail", "concise"): "Keep responses concise and omit nonessential background.",
    ("response.detail", "balanced"): "Use moderate detail and expand only where it improves understanding.",
    ("response.detail", "detailed"): "Provide thorough explanations with the important intermediate reasoning made explicit.",
    ("answer.structure", "prose"): "Prefer cohesive prose over long checklists.",
    ("answer.structure", "checklist"): "Prefer compact checklists with clear completion criteria.",
    ("answer.structure", "step_by_step"): "Present procedures as numbered, sequential steps.",
    ("answer.structure", "mixed"): "Choose prose or lists according to the information structure.",
    ("language.mode", "chinese"): "Respond primarily in Chinese while preserving necessary technical terms.",
    ("language.mode", "english"): "Respond primarily in English.",
    ("language.mode", "bilingual"): "Use bilingual explanations for important terminology and conclusions.",
    ("language.mode", "match_user"): "Match the language used in the learner's current request.",
    ("explanation.order", "intuition_first"): "Lead with intuition before formal definitions and derivations.",
    ("explanation.order", "example_first"): "Lead with a concrete example, then extract the general idea.",
    ("explanation.order", "formal_first"): "Lead with the precise definition or formal model.",
    ("explanation.order", "mixed"): "Choose the explanation order that best fits the current Atom.",
    ("example.mode", "practical"): "Prefer practical, real-world examples.",
    ("example.mode", "code"): "Prefer small executable or pseudocode examples where relevant.",
    ("example.mode", "visual"): "Prefer spatial, diagrammatic, or visually structured explanations.",
    ("example.mode", "analogy"): "Use a bounded analogy and state where it stops applying.",
    ("example.mode", "theoretical"): "Prefer mathematically or conceptually clean examples.",
    ("example.mode", "mixed"): "Mix example types according to the learning objective.",
    ("interaction.pacing", "one_atom"): "Advance one Knowledge Atom and one check at a time.",
    ("interaction.pacing", "short_batch"): "Group a small number of tightly related steps when prerequisites are secure.",
    ("interaction.pacing", "user_led"): "Let the learner explicitly control when to expand, check, or advance.",
    ("teaching.mode", "direct"): "Explain directly before asking the learner to perform.",
    ("teaching.mode", "socratic"): "Use short guiding questions before revealing the next step.",
    ("teaching.mode", "guided_discovery"): "Provide hints and let the learner construct the key relationship.",
    ("teaching.mode", "mixed"): "Switch between direct and guided instruction based on observed difficulty.",
    ("feedback.style", "direct"): "State errors and corrective actions plainly and specifically.",
    ("feedback.style", "supportive"): "Frame corrections supportively while remaining precise.",
    ("feedback.style", "neutral"): "Use neutral, evidence-focused feedback.",
    ("notation.level", "plain"): "Prefer plain language and introduce notation only when needed.",
    ("notation.level", "mixed"): "Pair formal notation with a plain-language interpretation.",
    ("notation.level", "formal"): "Use standard formal notation and precise definitions.",
    ("challenge.level", "gentle"): "Use low-friction checks before increasing difficulty.",
    ("challenge.level", "standard"): "Use checks at the declared course depth.",
    ("challenge.level", "stretch"): "Add transfer and edge-case challenges after core mastery.",
    ("research.orientation", "breadth_first"): "Map the field broadly before deep reading.",
    ("research.orientation", "depth_first"): "Prioritize a narrow chain of closely related papers for deep reading.",
    ("research.orientation", "evidence_first"): "Prioritize methods, evaluations, replications, and claim-evidence quality.",
    ("research.orientation", "application_first"): "Organize reading around the learner's target application.",
    ("source.priority", "user_materials"): "Prefer the learner's supplied materials when they adequately support the claim.",
    ("source.priority", "primary_sources"): "Prefer primary, official, and peer-reviewed sources.",
    ("source.priority", "textbooks"): "Prefer coherent textbook treatments for foundations.",
    ("source.priority", "mixed"): "Balance learner materials, primary sources, and textbooks by claim type.",
}


class AdaptationError(RuntimeError):
    """A user-correctable session adaptation error."""


def template_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def initialize_adaptation(root: Path) -> None:
    root = root.resolve()
    meta = root / ".atomlearn"
    if not meta.is_dir():
        raise AdaptationError(f"Not an AtomLearn workspace: {root}")
    adaptation = meta / "adaptation"
    adaptation.mkdir(parents=True, exist_ok=True)
    mapping = {
        "state.yaml": "adaptation-state.yaml",
        "profile.yaml": "adaptation-profile.yaml",
    }
    timestamp = iso()
    for destination, source in mapping.items():
        path = adaptation / destination
        if path.exists():
            continue
        data = read_data(template_dir() / source)
        if destination == "state.yaml":
            data["created_at"] = timestamp
            data["updated_at"] = timestamp
        write_yaml(path, data)
    for filename in ["signals.ndjson", "ledger.ndjson"]:
        path = adaptation / filename
        if not path.exists():
            atomic_text(path, "")


def opaque_id(value: Any, label: str) -> str:
    result = require_string(value, label)
    if not OPAQUE_ID.fullmatch(result):
        raise AdaptationError(f"{label} must be an opaque ID using letters, numbers, dot, colon, underscore, or hyphen")
    return result


class AdaptationEngine:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.root = workspace.meta / "adaptation"
        initialize_adaptation(workspace.root)
        self.state = read_data(self.root / "state.yaml")
        self.profile = read_data(self.root / "profile.yaml")

    @classmethod
    def load(cls, workspace_path: str) -> "AdaptationEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise AdaptationError("Cannot adapt an invalid workspace:\n- " + "\n- ".join(errors))
        return cls(workspace)

    @property
    def revision(self) -> int:
        value = self.state.get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AdaptationError("adaptation revision must be a non-negative integer")
        return value

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise AdaptationError(
                f"Stale adaptation revision: expected {expected}, current is {self.revision}. Reload adaptation status."
            )

    def records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        path = self.root / "signals.ndjson"
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AdaptationError(f"signals.ndjson line {line_number} is invalid JSON") from exc
            if not isinstance(record, dict):
                raise AdaptationError(f"signals.ndjson line {line_number} must be an object")
            records.append(record)
        return records

    def ledger_records(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        path = self.root / "ledger.ndjson"
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AdaptationError(f"ledger.ndjson line {line_number} is invalid JSON") from exc
            if not isinstance(event, dict):
                raise AdaptationError(f"ledger.ndjson line {line_number} must be an object")
            events.append(event)
        return events

    def _normalize_signal(self, raw: Any, session_id: str, context: str, index: int, record_id: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise AdaptationError(f"signals[{index}] must be a mapping")
        allowed = {"dimension", "value", "direction", "evidence", "reason_code", "confidence", "turn_refs"}
        extra = sorted(set(raw) - allowed)
        if extra:
            raise AdaptationError(
                f"signals[{index}] contains unsupported fields: {', '.join(extra)}; never pass raw message text"
            )
        dimension = require_string(raw.get("dimension"), f"signals[{index}].dimension")
        if dimension not in PREFERENCE_VALUES:
            raise AdaptationError(f"signals[{index}].dimension is unsupported: {dimension!r}")
        value = require_string(raw.get("value"), f"signals[{index}].value")
        if value not in PREFERENCE_VALUES[dimension]:
            raise AdaptationError(
                f"signals[{index}].value must be one of: {', '.join(sorted(PREFERENCE_VALUES[dimension]))}"
            )
        direction = raw.get("direction")
        if direction not in DIRECTIONS:
            raise AdaptationError(f"signals[{index}].direction must be prefer or avoid")
        evidence = raw.get("evidence")
        if evidence not in EVIDENCE_TYPES:
            raise AdaptationError(f"signals[{index}].evidence must be explicit, behavioral, or outcome")
        reason_code = raw.get("reason_code")
        if reason_code not in REASON_CODES[evidence]:
            raise AdaptationError(
                f"signals[{index}].reason_code is not valid for {evidence} evidence"
            )
        confidence = require_number(raw.get("confidence"), f"signals[{index}].confidence", 0.5, 1.0)
        turn_refs = raw.get("turn_refs", [])
        if not isinstance(turn_refs, list) or len(turn_refs) > MAX_TURN_REFS:
            raise AdaptationError(f"signals[{index}].turn_refs must be a list with at most {MAX_TURN_REFS} items")
        normalized_refs = unique(opaque_id(item, f"signals[{index}].turn_refs") for item in turn_refs)
        return {
            "schema_version": SCHEMA_VERSION,
            "id": record_id,
            "type": "preference.signal",
            "session_id": session_id,
            "context": context,
            "dimension": dimension,
            "value": value,
            "direction": direction,
            "evidence": evidence,
            "reason_code": reason_code,
            "confidence": round(float(confidence), 3),
            "turn_refs": normalized_refs,
            "observed_at": iso(),
        }

    def observe_session(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AdaptationError("session observation must be a mapping")
        extra = sorted(set(payload) - {"session_id", "context", "signals"})
        if extra:
            raise AdaptationError(
                "session observation contains unsupported fields: " + ", ".join(extra) + "; never pass raw messages"
            )
        session_id = opaque_id(payload.get("session_id"), "session_id")
        context = payload.get("context", "general")
        if context not in ADAPTATION_CONTEXTS:
            raise AdaptationError(f"context must be one of: {', '.join(sorted(ADAPTATION_CONTEXTS))}")
        raw_signals = payload.get("signals")
        if not isinstance(raw_signals, list) or not raw_signals or len(raw_signals) > MAX_SIGNALS_PER_SESSION:
            raise AdaptationError(
                f"signals must be a non-empty list with at most {MAX_SIGNALS_PER_SESSION} entries"
            )
        records = self.records()
        known_sessions = {
            item.get("session_id")
            for item in records
            if item.get("type") == "preference.signal" and isinstance(item.get("session_id"), str)
        }
        if session_id in known_sessions:
            raise AdaptationError(f"session has already been observed: {session_id}")
        normalized = [
            self._normalize_signal(raw, session_id, context, index, f"pref-{len(records) + index + 1:06d}")
            for index, raw in enumerate(raw_signals)
        ]
        dimensions = [item["dimension"] for item in normalized]
        before = {
            key: value.get("active_value")
            for key, value in self.profile.get("preferences", {}).items()
            if isinstance(value, dict)
        }
        records.extend(normalized)
        self._commit(
            records,
            "adaptation.session_observed",
            {"session_id": session_id, "signal_ids": [item["id"] for item in normalized]},
            session_id=session_id,
        )
        after = {
            key: value.get("active_value")
            for key, value in self.profile.get("preferences", {}).items()
            if isinstance(value, dict)
        }
        changed = sorted(key for key in unique(dimensions) if before.get(key) != after.get(key))
        return {
            "session_id": session_id,
            "signal_ids": [item["id"] for item in normalized],
            "changed_dimensions": changed,
            "guidance": self.guidance(context),
        }

    def retire(self, dimension: str, reason_code: str) -> dict[str, Any]:
        if dimension not in PREFERENCE_VALUES:
            raise AdaptationError(f"unsupported preference dimension: {dimension!r}")
        if reason_code not in RETIRE_REASONS:
            raise AdaptationError(f"retire reason must be one of: {', '.join(sorted(RETIRE_REASONS))}")
        records = self.records()
        record = {
            "schema_version": SCHEMA_VERSION,
            "id": f"pref-{len(records) + 1:06d}",
            "type": "preference.retired",
            "dimension": dimension,
            "reason_code": reason_code,
            "observed_at": iso(),
        }
        records.append(record)
        self._commit(records, "adaptation.preference_retired", {"dimension": dimension, "record_id": record["id"]})
        return {"dimension": dimension, "status": "retired", "record_id": record["id"]}

    def _build_profile(self, records: list[dict[str, Any]], revision: int) -> dict[str, Any]:
        policy = self.state.get("policy", {})
        min_sessions = int(policy.get("behavioral_min_sessions", 2))
        min_confidence = float(policy.get("inferred_min_confidence", 0.7))
        conflict_margin = float(policy.get("conflict_margin", 0.15))
        by_dimension: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            dimension = record.get("dimension")
            if dimension in PREFERENCE_VALUES:
                by_dimension[dimension].append(record)
        preferences: dict[str, Any] = {}
        for dimension, dimension_records in sorted(by_dimension.items()):
            last_retirement = max(
                (index for index, record in enumerate(dimension_records) if record.get("type") == "preference.retired"),
                default=-1,
            )
            active_records = dimension_records[last_retirement + 1 :]
            signals = [item for item in active_records if item.get("type") == "preference.signal"]
            if not signals:
                if last_retirement >= 0:
                    preferences[dimension] = {
                        "active_value": None,
                        "status": "retired",
                        "confidence": 0.0,
                        "source": "retired",
                        "session_count": 0,
                        "supporting_signal_ids": [],
                        "candidates": {},
                        "updated_at": dimension_records[-1].get("observed_at"),
                    }
                continue
            explicit_value: str | None = None
            explicit_signal: dict[str, Any] | None = None
            explicitly_avoided: set[str] = set()
            for signal in signals:
                if signal.get("evidence") != "explicit":
                    continue
                if signal.get("direction") == "prefer":
                    explicitly_avoided.discard(signal["value"])
                    explicit_value = signal["value"]
                    explicit_signal = signal
                else:
                    explicitly_avoided.add(signal["value"])
                    if signal.get("value") == explicit_value:
                        explicit_value = None
                        explicit_signal = None
            candidates: dict[str, Any] = {}
            for value in sorted(PREFERENCE_VALUES[dimension]):
                support_by_session: dict[str, float] = {}
                oppose_by_session: dict[str, float] = {}
                supporting_ids: list[str] = []
                opposing_ids: list[str] = []
                for signal in signals:
                    if signal.get("evidence") == "explicit" or signal.get("value") != value:
                        continue
                    target = support_by_session if signal.get("direction") == "prefer" else oppose_by_session
                    session_id = signal["session_id"]
                    target[session_id] = max(target.get(session_id, 0.0), float(signal["confidence"]))
                    (supporting_ids if signal.get("direction") == "prefer" else opposing_ids).append(signal["id"])
                if not support_by_session and not oppose_by_session:
                    continue
                support = sum(support_by_session.values()) / len(support_by_session) if support_by_session else 0.0
                opposition = sum(oppose_by_session.values()) / len(oppose_by_session) if oppose_by_session else 0.0
                score = max(0.0, min(1.0, support - 0.5 * opposition))
                if value in explicitly_avoided:
                    score = 0.0
                candidates[value] = {
                    "score": round(score, 3),
                    "session_count": len(support_by_session),
                    "supporting_signal_ids": unique(supporting_ids),
                    "opposing_signal_ids": unique(opposing_ids),
                }
            if explicit_value is not None and explicit_signal is not None:
                active_value = explicit_value
                status = "active"
                confidence = max(0.9, float(explicit_signal["confidence"]))
                source = "explicit"
                session_count = len({item["session_id"] for item in signals if item.get("value") == explicit_value})
                supporting_signal_ids = [explicit_signal["id"]]
            else:
                ordered = sorted(
                    candidates.items(),
                    key=lambda item: (-item[1]["score"], -item[1]["session_count"], item[0]),
                )
                top_value, top = ordered[0] if ordered else (None, {"score": 0.0, "session_count": 0})
                second_score = ordered[1][1]["score"] if len(ordered) > 1 else 0.0
                inferred_ready = bool(
                    top_value
                    and top["session_count"] >= min_sessions
                    and top["score"] >= min_confidence
                )
                contested = inferred_ready and second_score > 0 and top["score"] - second_score < conflict_margin
                active_value = top_value if inferred_ready and not contested else None
                status = "contested" if contested else ("active" if active_value else "provisional")
                confidence = float(top["score"])
                source = "inferred" if active_value else "unconfirmed"
                session_count = int(top["session_count"])
                supporting_signal_ids = list(top.get("supporting_signal_ids", []))
            preferences[dimension] = {
                "active_value": active_value,
                "status": status,
                "confidence": round(confidence, 3),
                "source": source,
                "session_count": session_count,
                "supporting_signal_ids": supporting_signal_ids,
                "candidates": candidates,
                "updated_at": signals[-1].get("observed_at"),
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "adaptation_revision": revision,
            "generated_at": iso(),
            "preferences": preferences,
        }

    def _commit(
        self,
        records: list[dict[str, Any]],
        event_type: str,
        details: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> None:
        new_revision = self.revision + 1
        profile = self._build_profile(records, new_revision)
        timestamp = iso()
        self.state["revision"] = new_revision
        self.state["updated_at"] = timestamp
        if session_id is not None:
            self.state["last_session_at"] = timestamp
        session_ids = {
            item.get("session_id")
            for item in records
            if item.get("type") == "preference.signal" and isinstance(item.get("session_id"), str)
        }
        self.state["session_count"] = len(session_ids)
        serialized = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records)
        atomic_text(self.root / "signals.ndjson", serialized)
        write_yaml(self.root / "profile.yaml", profile)
        write_yaml(self.root / "state.yaml", self.state)
        self.profile = profile
        event = {
            "event_id": f"aevt-{new_revision:06d}",
            "revision": new_revision,
            "type": event_type,
            "at": timestamp,
            "course_revision": self.workspace.revision,
            "details": details,
        }
        with (self.root / "ledger.ndjson").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
        self.render()

    def guidance(self, context: str) -> dict[str, Any]:
        if context not in ADAPTATION_CONTEXTS:
            raise AdaptationError(f"context must be one of: {', '.join(sorted(ADAPTATION_CONTEXTS))}")
        active: list[dict[str, Any]] = []
        instructions: list[str] = []
        pending: list[dict[str, Any]] = []
        for dimension, preference in sorted(self.profile.get("preferences", {}).items()):
            if dimension not in DIMENSION_CONTEXTS or context not in DIMENSION_CONTEXTS[dimension]:
                continue
            if preference.get("status") == "active" and preference.get("active_value"):
                value = preference["active_value"]
                active.append(
                    {
                        "dimension": dimension,
                        "value": value,
                        "confidence": preference.get("confidence"),
                        "source": preference.get("source"),
                    }
                )
                instruction = GUIDANCE.get((dimension, value))
                if instruction:
                    instructions.append(instruction)
            elif preference.get("status") in {"provisional", "contested"}:
                pending.append(
                    {
                        "dimension": dimension,
                        "status": preference.get("status"),
                        "confidence": preference.get("confidence"),
                    }
                )
        return {
            "adaptation_revision": self.revision,
            "context": context,
            "active_preferences": active,
            "instructions": instructions,
            "pending_preferences": pending,
            "precedence": [
                "The learner's explicit request in the current turn overrides this profile.",
                "An explicit stored preference overrides an inferred preference.",
                "Never let presentation preferences weaken mastery, source, prerequisite, or safety guards.",
            ],
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        allowed_state_keys = {
            "schema_version", "revision", "created_at", "updated_at", "last_session_at", "session_count", "policy",
        }
        allowed_policy_keys = {
            "store_raw_messages", "cross_workspace_aggregation", "infer_sensitive_traits",
            "behavioral_min_sessions", "inferred_min_confidence", "conflict_margin",
        }
        allowed_profile_keys = {"schema_version", "adaptation_revision", "generated_at", "preferences"}
        if set(self.state) != allowed_state_keys:
            errors.append("adaptation state fields are invalid")
        if set(self.profile) != allowed_profile_keys:
            errors.append("adaptation profile fields are invalid")
        if self.state.get("schema_version") != SCHEMA_VERSION:
            errors.append("adaptation state has unsupported schema_version")
        if self.profile.get("schema_version") != SCHEMA_VERSION:
            errors.append("adaptation profile has unsupported schema_version")
        for field in ["created_at", "updated_at"]:
            try:
                parse_time(self.state.get(field) if isinstance(self.state.get(field), str) else None)
                if not isinstance(self.state.get(field), str):
                    raise AtomLearnError(f"{field} must be a timestamp")
            except AtomLearnError:
                errors.append(f"adaptation state {field} is invalid")
        last_session_at = self.state.get("last_session_at")
        if last_session_at is not None:
            try:
                parse_time(last_session_at if isinstance(last_session_at, str) else None)
                if not isinstance(last_session_at, str):
                    raise AtomLearnError("last_session_at must be a timestamp")
            except AtomLearnError:
                errors.append("adaptation state last_session_at is invalid")
        generated_at = self.profile.get("generated_at")
        if generated_at is not None:
            try:
                parse_time(generated_at if isinstance(generated_at, str) else None)
                if not isinstance(generated_at, str):
                    raise AtomLearnError("generated_at must be a timestamp")
            except AtomLearnError:
                errors.append("adaptation profile generated_at is invalid")
        try:
            revision = self.revision
        except AdaptationError as exc:
            errors.append(str(exc))
            revision = -1
        profile_revision = self.profile.get("adaptation_revision")
        if (
            not isinstance(profile_revision, int)
            or isinstance(profile_revision, bool)
            or profile_revision != revision
        ):
            errors.append("adaptation profile revision does not match state revision")
        session_count = self.state.get("session_count")
        if not isinstance(session_count, int) or isinstance(session_count, bool) or session_count < 0:
            errors.append("adaptation session_count must be a non-negative integer")
        policy = self.state.get("policy", {})
        if not isinstance(policy, dict):
            errors.append("adaptation policy must be a mapping")
            policy = {}
        elif set(policy) != allowed_policy_keys:
            errors.append("adaptation policy fields are invalid")
        for field in ["store_raw_messages", "cross_workspace_aggregation", "infer_sensitive_traits"]:
            if policy.get(field) is not False:
                errors.append(f"adaptation policy {field} must remain false")
        if not isinstance(policy.get("behavioral_min_sessions"), int) or policy.get("behavioral_min_sessions", 0) < 2:
            errors.append("behavioral_min_sessions must be an integer of at least 2")
        for field in ["inferred_min_confidence", "conflict_margin"]:
            value = policy.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
                errors.append(f"adaptation policy {field} must be between 0 and 1")
        try:
            records = self.records()
        except AdaptationError as exc:
            errors.append(str(exc))
            records = []
        identifiers: set[str] = set()
        sessions: set[str] = set()
        allowed_signal_keys = {
            "schema_version", "id", "type", "session_id", "context", "dimension", "value", "direction",
            "evidence", "reason_code", "confidence", "turn_refs", "observed_at",
        }
        allowed_retire_keys = {"schema_version", "id", "type", "dimension", "reason_code", "observed_at"}
        for index, record in enumerate(records):
            record_id = record.get("id")
            expected_record_id = f"pref-{index + 1:06d}"
            if not isinstance(record_id, str) or record_id != expected_record_id or record_id in identifiers:
                errors.append(f"adaptation record {index} has invalid or duplicate ID")
            if isinstance(record_id, str):
                identifiers.add(record_id)
            if record.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{record_id}: unsupported schema_version")
            observed_at = record.get("observed_at")
            try:
                parse_time(observed_at if isinstance(observed_at, str) else None)
                if not isinstance(observed_at, str):
                    raise AtomLearnError("observed_at must be a timestamp")
            except AtomLearnError:
                errors.append(f"{record_id}: invalid observed_at")
            if record.get("type") == "preference.signal":
                if set(record) != allowed_signal_keys:
                    errors.append(f"{record_id}: preference signal fields are invalid")
                if record.get("dimension") not in PREFERENCE_VALUES:
                    errors.append(f"{record_id}: invalid dimension")
                elif record.get("value") not in PREFERENCE_VALUES[record["dimension"]]:
                    errors.append(f"{record_id}: invalid value")
                if record.get("direction") not in DIRECTIONS or record.get("evidence") not in EVIDENCE_TYPES:
                    errors.append(f"{record_id}: invalid direction or evidence")
                elif record.get("reason_code") not in REASON_CODES[record["evidence"]]:
                    errors.append(f"{record_id}: invalid reason code")
                session_id = record.get("session_id")
                if not isinstance(session_id, str) or not OPAQUE_ID.fullmatch(session_id):
                    errors.append(f"{record_id}: invalid session ID")
                else:
                    sessions.add(session_id)
                if record.get("context") not in ADAPTATION_CONTEXTS:
                    errors.append(f"{record_id}: invalid context")
                if (
                    not isinstance(record.get("confidence"), (int, float))
                    or isinstance(record.get("confidence"), bool)
                    or not 0.5 <= float(record["confidence"]) <= 1
                ):
                    errors.append(f"{record_id}: invalid confidence")
                turn_refs = record.get("turn_refs")
                if (
                    not isinstance(turn_refs, list)
                    or len(turn_refs) > MAX_TURN_REFS
                    or any(not isinstance(item, str) or not OPAQUE_ID.fullmatch(item) for item in turn_refs)
                ):
                    errors.append(f"{record_id}: invalid turn references")
            elif record.get("type") == "preference.retired":
                if set(record) != allowed_retire_keys:
                    errors.append(f"{record_id}: retirement fields are invalid")
                if record.get("dimension") not in PREFERENCE_VALUES or record.get("reason_code") not in RETIRE_REASONS:
                    errors.append(f"{record_id}: invalid retirement")
            else:
                errors.append(f"{record_id}: invalid adaptation record type")
        if session_count != len(sessions):
            errors.append("adaptation session_count does not match signal records")
        if (len(sessions) == 0) != (last_session_at is None):
            errors.append("adaptation last_session_at does not match observed sessions")
        if not isinstance(self.profile.get("preferences"), dict):
            errors.append("adaptation profile preferences must be a mapping")
        try:
            expected = self._build_profile(records, revision)
            if self.profile.get("preferences") != expected.get("preferences"):
                errors.append("adaptation profile does not match the signal ledger")
        except (AttributeError, KeyError, TypeError, ValueError):
            errors.append("adaptation policy cannot build a valid profile")

        try:
            events = self.ledger_records()
        except AdaptationError as exc:
            errors.append(str(exc))
            events = []
        allowed_event_keys = {"event_id", "revision", "type", "at", "course_revision", "details"}
        signal_by_id = {
            record["id"]: record
            for record in records
            if record.get("type") == "preference.signal" and isinstance(record.get("id"), str)
        }
        retirement_by_id = {
            record["id"]: record
            for record in records
            if record.get("type") == "preference.retired" and isinstance(record.get("id"), str)
        }
        event_record_ids: set[str] = set()
        for index, event in enumerate(events, start=1):
            event_id = event.get("event_id")
            if set(event) != allowed_event_keys:
                errors.append(f"adaptation event {index} fields are invalid")
            event_revision = event.get("revision")
            if (
                event_id != f"aevt-{index:06d}"
                or not isinstance(event_revision, int)
                or isinstance(event_revision, bool)
                or event_revision != index
            ):
                errors.append(f"adaptation event {index} has an invalid ID or revision")
            try:
                event_at = event.get("at")
                parse_time(event_at if isinstance(event_at, str) else None)
                if not isinstance(event_at, str):
                    raise AtomLearnError("event time must be a timestamp")
            except AtomLearnError:
                errors.append(f"{event_id}: invalid event time")
            course_revision = event.get("course_revision")
            if not isinstance(course_revision, int) or isinstance(course_revision, bool) or course_revision < 0:
                errors.append(f"{event_id}: invalid course revision")
            details = event.get("details")
            if not isinstance(details, dict):
                errors.append(f"{event_id}: event details must be a mapping")
                continue
            if event.get("type") == "adaptation.session_observed":
                if set(details) != {"session_id", "signal_ids"}:
                    errors.append(f"{event_id}: session event details are invalid")
                    continue
                session_id = details.get("session_id")
                signal_ids = details.get("signal_ids")
                if not isinstance(session_id, str) or not OPAQUE_ID.fullmatch(session_id):
                    errors.append(f"{event_id}: invalid event session ID")
                valid_signal_ids = bool(
                    isinstance(signal_ids, list)
                    and signal_ids
                    and all(isinstance(item, str) for item in signal_ids)
                    and len(signal_ids) == len(set(signal_ids))
                )
                if not valid_signal_ids:
                    errors.append(f"{event_id}: invalid event signal IDs")
                elif any(
                    signal_id not in signal_by_id or signal_by_id[signal_id].get("session_id") != session_id
                    for signal_id in signal_ids
                ):
                    errors.append(f"{event_id}: event signals do not match the session ledger")
                else:
                    event_record_ids.update(signal_ids)
            elif event.get("type") == "adaptation.preference_retired":
                if set(details) != {"dimension", "record_id"}:
                    errors.append(f"{event_id}: retirement event details are invalid")
                    continue
                retired_record_id = details.get("record_id")
                record = retirement_by_id.get(retired_record_id) if isinstance(retired_record_id, str) else None
                if record is None or record.get("dimension") != details.get("dimension"):
                    errors.append(f"{event_id}: retirement event does not match the signal ledger")
                else:
                    event_record_ids.add(record["id"])
            else:
                errors.append(f"{event_id}: invalid adaptation event type")
        if len(events) != revision:
            errors.append("adaptation ledger length does not match state revision")
        if event_record_ids != identifiers:
            errors.append("adaptation events do not account for every signal-ledger record")
        return unique(errors)

    def status(self) -> dict[str, Any]:
        errors = self.validate()
        preferences = self.profile.get("preferences", {})
        return {
            "valid": not errors,
            "validation_errors": errors,
            "adaptation_revision": self.revision,
            "course_revision": self.workspace.revision,
            "session_count": self.state.get("session_count"),
            "active_preferences": sum(item.get("status") == "active" for item in preferences.values()),
            "provisional_preferences": sum(item.get("status") == "provisional" for item in preferences.values()),
            "contested_preferences": sum(item.get("status") == "contested" for item in preferences.values()),
            "retired_preferences": sum(item.get("status") == "retired" for item in preferences.values()),
            "privacy": {
                "raw_messages_stored": False,
                "cross_workspace_aggregation": False,
                "sensitive_traits_inferred": False,
            },
        }

    def render(self) -> None:
        lines = [
            "# Learner Adaptation",
            "",
            "> Generated by AtomLearn. Canonical adaptation state lives under `.atomlearn/adaptation/`.",
            "",
            f"- Adaptation revision: `{self.revision}`",
            f"- Observed sessions: `{self.state.get('session_count')}`",
            "- Raw messages stored: `false`",
            "- Cross-workspace aggregation: `false`",
            "- Sensitive-trait inference: `false`",
            "",
            "## Active Preferences",
            "",
        ]
        active = [
            (dimension, preference)
            for dimension, preference in sorted(self.profile.get("preferences", {}).items())
            if preference.get("status") == "active"
        ]
        lines.extend(
            [
                f"- `{dimension}` = `{preference['active_value']}` "
                f"({preference['source']}, confidence {preference['confidence']})"
                for dimension, preference in active
            ]
            or ["- None"]
        )
        lines.extend(["", "## Pending or Contested", ""])
        pending = [
            (dimension, preference)
            for dimension, preference in sorted(self.profile.get("preferences", {}).items())
            if preference.get("status") in {"provisional", "contested"}
        ]
        lines.extend(
            [f"- `{dimension}` — `{preference['status']}` (confidence {preference['confidence']})" for dimension, preference in pending]
            or ["- None"]
        )
        lines.extend(
            [
                "",
                "## Precedence",
                "",
                "- The learner's current explicit request overrides this profile.",
                "- Explicit preferences override inferred preferences.",
                "- Preferences never weaken mastery, source, prerequisite, or safety guards.",
            ]
        )
        atomic_text(self.workspace.root / "PERSONALIZATION.md", "\n".join(lines).rstrip() + "\n")


def add_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-adaptation-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adapt AtomLearn to distilled cross-session preference signals")
    sub = parser.add_subparsers(dest="action", required=True)
    simple_help = {
        "status": "Show adaptation validity, revision, and preference counts",
        "validate": "Validate the canonical adaptation profile and audit log",
        "profile": "Print the complete canonical preference profile",
        "render": "Regenerate the learner-facing personalization view",
    }
    for action in ["status", "validate", "profile", "render"]:
        command = sub.add_parser(action, help=simple_help[action])
        command.add_argument("workspace")
    guidance = sub.add_parser("guidance", help="Generate applicable guidance for one interaction context")
    guidance.add_argument("workspace")
    guidance.add_argument("--context", choices=sorted(ADAPTATION_CONTEXTS), default="general")
    observe = sub.add_parser("observe-session", help="Distill allowlisted preference signals from one session")
    observe.add_argument("workspace")
    observe.add_argument("--input", required=True)
    observe.add_argument("--expected-profile-revision", type=int)
    observe.add_argument("--data-dir")
    add_revision(observe)
    retire = sub.add_parser("retire", help="Stop one preference dimension from influencing guidance")
    retire.add_argument("workspace")
    retire.add_argument("dimension", choices=sorted(PREFERENCE_VALUES))
    retire.add_argument("--reason-code", choices=sorted(RETIRE_REASONS), required=True)
    add_revision(retire)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    observation = read_data(Path(args.input)) if args.action == "observe-session" else None
    if args.action == "observe-session" and observation.get("scope", "workspace") == "user":
        from user_profile import UserProfileEngine, UserProfileError

        try:
            profile, _ = UserProfileEngine.for_workspace(args.workspace, args.data_dir)
            result = profile.observe_session(observation, args.expected_profile_revision)
        except UserProfileError as exc:
            raise AdaptationError(str(exc)) from exc
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return
    engine = AdaptationEngine.load(args.workspace)
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise AdaptationError("Adaptation validation failed:\n- " + "\n- ".join(errors))
        print(json.dumps({"ok": True, "adaptation_revision": engine.revision}))
        return
    errors = engine.validate()
    if errors:
        raise AdaptationError("Refusing to use invalid adaptation state:\n- " + "\n- ".join(errors))
    if args.action == "status":
        print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
    elif args.action == "profile":
        print(json.dumps(engine.profile, ensure_ascii=False, indent=2))
    elif args.action == "guidance":
        from effective_policy import backward_compatible_guidance, effective_for_workspace

        policy = effective_for_workspace(args.workspace, args.context)
        print(json.dumps(backward_compatible_guidance(policy), ensure_ascii=False, indent=2))
    elif args.action == "render":
        engine.render()
        print(json.dumps({"ok": True, "view": "PERSONALIZATION.md"}))
    elif args.action == "observe-session":
        engine.expect_revision(args.expected_adaptation_revision)
        observation.pop("scope", None)
        result = engine.observe_session(observation)
        print(json.dumps({"ok": True, "adaptation_revision": engine.revision, "result": result}, ensure_ascii=False, indent=2))
    elif args.action == "retire":
        engine.expect_revision(args.expected_adaptation_revision)
        result = engine.retire(args.dimension, args.reason_code)
        print(json.dumps({"ok": True, "adaptation_revision": engine.revision, "result": result}, ensure_ascii=False, indent=2))
    else:  # pragma: no cover
        raise AdaptationError(f"Unhandled adaptation action: {args.action}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (AdaptationError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
