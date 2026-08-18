#!/usr/bin/env python3
"""Revisioned, invalidation-aware exam schedules for AtomLearn."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import iso, parse_time
from core_paths import CORE_ROOT
from platform_state import FileLock, atomic_yaml


STATE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "exam-schedule-state.schema.json"
OUTCOME_SCHEMA = CORE_ROOT / "assets" / "schemas" / "exam-day-outcome.schema.json"
REPLAN_REASONS = {
    "initial",
    "learner_request",
    "availability_changed",
    "day_missed",
    "day_partial",
    "unrecorded_past_day",
    "exam_corpus_changed",
    "exam_target_changed",
    "mapping_review_completed",
    "difficulty_review_completed",
    "learning_evidence_changed",
    "skip_revoked",
    "prerequisite_inserted",
    "course_plan_changed",
    "course_revision_changed",
}


class ExamScheduleError(RuntimeError):
    """A user-correctable revisioned exam schedule error."""


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise ExamScheduleError(f"Schema is not an object: {path}")
    Draft202012Validator.check_schema(value)
    return value


def _errors(value: dict[str, Any], path: Path) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(_schema(path)).iter_errors(value), key=lambda item: list(item.path))
    ]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class ExamScheduleEngine:
    """Workspace-local canonical schedule state with derived invalidation events."""

    def __init__(self, exam: Any):
        self.exam = exam
        self.workspace = exam.workspace
        self.path = exam.root / "schedule.yaml"
        self.lock_path = exam.root / ".schedule.lock"

    def _empty(self) -> dict[str, Any]:
        return {
            "kind": "atomlearn.exam-schedule-state",
            "schema_version": 1,
            "revision": 0,
            "updated_at": None,
            "configuration": None,
            "configuration_sha256": None,
            "plan": None,
            "outcomes": [],
            "events": [],
        }

    def state(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            value = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ExamScheduleError(f"Cannot parse exam schedule state: {exc}") from exc
        if not isinstance(value, dict):
            raise ExamScheduleError("Exam schedule state must be a mapping")
        errors = _errors(value, STATE_SCHEMA)
        if errors:
            raise ExamScheduleError("Exam schedule state is invalid:\n- " + "\n- ".join(errors[:20]))
        return value

    @staticmethod
    def _expect_revision(state: dict[str, Any], expected: int | None) -> None:
        if expected is None:
            raise ExamScheduleError("Schedule mutation requires --expected-schedule-revision")
        if expected != state["revision"]:
            raise ExamScheduleError(
                f"Stale schedule revision: expected {expected}, current is {state['revision']}. Reload exam plan-status."
            )

    def _workspace_events_after(self, revision: int) -> list[dict[str, Any]]:
        path = self.workspace.meta / "events.ndjson"
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:  # root validation owns full event validation
                raise ExamScheduleError("Cannot inspect workspace events for schedule invalidation") from exc
            if isinstance(value, dict) and isinstance(value.get("revision"), int) and value["revision"] > revision:
                events.append(value)
        return events

    def _invalidation_reasons(self, state: dict[str, Any], as_of: date) -> list[str]:
        plan = state.get("plan")
        if not isinstance(plan, dict):
            return []
        reasons: list[str] = []
        if plan["exam_revision"] != self.exam.revision:
            exam_events = [item for item in self.exam.events() if item.get("revision", 0) > plan["exam_revision"]]
            event_types = {item.get("type") for item in exam_events}
            if "exam.mappings_reviewed" in event_types:
                reasons.append("mapping_review_completed")
            if "exam.target_changed" in event_types:
                reasons.append("exam_target_changed")
            if event_types & {"exam.difficulty_calibrated", "exam.empirical_difficulty_recorded", "exam.official_difficulty_recorded"}:
                reasons.append("difficulty_review_completed")
            if event_types - {
                "exam.mappings_reviewed",
                "exam.difficulty_calibrated",
                "exam.empirical_difficulty_recorded",
                "exam.official_difficulty_recorded",
                "exam.target_changed",
            }:
                reasons.append("exam_corpus_changed")
        if plan["course_revision"] != self.workspace.revision:
            course_events = self._workspace_events_after(plan["course_revision"])
            event_types = {item.get("type") for item in course_events}
            course_reason_added = False
            if event_types & {"evidence.recorded", "atom.assessed", "reviews.refreshed"}:
                reasons.append("learning_evidence_changed")
                course_reason_added = True
            if "atom.flexibility_revoked" in event_types:
                reasons.append("skip_revoked")
                course_reason_added = True
            if "session.backtracked" in event_types:
                reasons.append("prerequisite_inserted")
                course_reason_added = True
            if event_types & {"plan.imported", "graph.restructured", "atom.expanded_for_detail", "concept.routed"}:
                reasons.append("course_plan_changed")
                course_reason_added = True
            if not course_reason_added:
                reasons.append("course_revision_changed")
        current_plan_revision = plan["plan_revision"]
        current_outcomes = [item for item in state["outcomes"] if item["plan_revision"] == current_plan_revision]
        for outcome in current_outcomes:
            if outcome["status"] == "missed":
                reasons.append("day_missed")
            elif outcome["status"] == "partial":
                reasons.append("day_partial")
            planned = next((item for item in plan["days"] if item["date"] == outcome["date"]), None)
            if planned is not None and outcome["available_minutes"] is not None and outcome["available_minutes"] != planned["capacity_minutes"]:
                reasons.append("availability_changed")
        outcome_dates = {item["date"] for item in current_outcomes}
        if any(item["date"] < as_of.isoformat() and item["tasks"] and item["date"] not in outcome_dates for item in plan["days"]):
            reasons.append("unrecorded_past_day")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _completion(state: dict[str, Any], plan_revision: int) -> set[str]:
        return {
            task_id
            for item in state["outcomes"]
            if item["plan_revision"] == plan_revision
            for task_id in item["completed_task_ids"]
        }

    def status(self, as_of: date | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
        observed_at = as_of or date.today()
        state = state or self.state()
        plan = state.get("plan")
        if plan is None:
            return {
                "initialized": self.path.is_file(),
                "schedule_revision": state["revision"],
                "plan_revision": None,
                "freshness": "uninitialized",
                "invalidation_reasons": [],
                "as_of": observed_at.isoformat(),
                "events": [],
                "plan": None,
            }
        reasons = self._invalidation_reasons(state, observed_at)
        completed = self._completion(state, plan["plan_revision"])
        events: list[dict[str, Any]] = []
        for day in plan["days"]:
            remaining = [item["id"] for item in day["tasks"] if item["id"] not in completed]
            if not remaining:
                continue
            if day["date"] < observed_at.isoformat():
                events.append({"type": "overdue", "date": day["date"], "task_ids": remaining})
            elif day["date"] == observed_at.isoformat():
                events.append({"type": "due", "date": day["date"], "task_ids": remaining})
        freshness = "stale" if reasons else "infeasible" if plan["status"] == "infeasible" else "current"
        return {
            "initialized": self.path.is_file(),
            "schedule_revision": state["revision"],
            "plan_revision": plan["plan_revision"],
            "freshness": freshness,
            "invalidation_reasons": reasons,
            "as_of": observed_at.isoformat(),
            "events": events,
            "plan": plan,
        }

    def replan(
        self,
        payload: dict[str, Any],
        *,
        expected_revision: int | None,
        reason: str,
        as_of: date,
    ) -> dict[str, Any]:
        if reason not in REPLAN_REASONS:
            raise ExamScheduleError("Unsupported replan reason")
        with FileLock(self.lock_path):
            state = self.state()
            self._expect_revision(state, expected_revision)
            previous_reasons = self._invalidation_reasons(state, as_of)
            if state["revision"] == 0 and reason != "initial":
                raise ExamScheduleError("The first canonical schedule must use reason initial")
            if state["revision"] > 0 and reason == "initial":
                raise ExamScheduleError("Reason initial is valid only for the first canonical schedule")
            normalized_payload = dict(payload)
            exam_target = self.exam.state.get("target_date")
            if exam_target is None:
                raise ExamScheduleError("Canonical scheduling requires an exam target date; run exam set-target first")
            if str(normalized_payload.get("target_date")) != exam_target:
                raise ExamScheduleError(
                    "Canonical schedule target_date must match the exam target date; run exam set-target first"
                )
            try:
                requested_start = date.fromisoformat(str(normalized_payload.get("start_date")))
            except ValueError as exc:
                raise ExamScheduleError("Schedule start_date must use YYYY-MM-DD") from exc
            if requested_start < as_of:
                normalized_payload["start_date"] = as_of.isoformat()
            carry_reasons = {"day_missed", "day_partial", "availability_changed", "unrecorded_past_day"}
            previous_plan_revision = (state.get("plan") or {}).get("plan_revision", 0)
            completed = (
                self._completion(state, previous_plan_revision)
                if not previous_reasons or set(previous_reasons) <= carry_reasons
                else set()
            )
            preview = self.exam.daily_plan(normalized_payload, completed_task_ids=completed)
            next_revision = state["revision"] + 1
            plan = {
                key: value
                for key, value in preview.items()
                if key not in {"schema_version"}
            }
            plan["plan_revision"] = next_revision
            plan["carried_completed_task_ids"] = sorted(completed)
            plan["replan_reasons"] = list(dict.fromkeys(previous_reasons or [reason]))
            configuration = {
                key: normalized_payload[key]
                for key in [
                    "start_date", "target_date", "available_weekdays", "minutes_per_day", "durations",
                    "desired_retention", "final_review_days", "mode",
                ]
            }
            configuration["start_date"] = preview["start_date"]
            configuration["target_date"] = preview["target_date"]
            timestamp = iso()
            event_type = "infeasible" if plan["status"] == "infeasible" else "replanned"
            state.update(
                {
                    "revision": next_revision,
                    "updated_at": timestamp,
                    "configuration": configuration,
                    "configuration_sha256": _fingerprint(configuration),
                    "plan": plan,
                }
            )
            state["events"].append(
                {
                    "id": f"xplan-{next_revision:06d}",
                    "revision": next_revision,
                    "type": event_type,
                    "at": timestamp,
                    "reason": reason,
                    "exam_revision": self.exam.revision,
                    "course_revision": self.workspace.revision,
                    "plan_revision": next_revision,
                }
            )
            errors = self.validate(state)
            if errors:
                raise ExamScheduleError("Schedule replan is invalid:\n- " + "\n- ".join(errors))
            atomic_yaml(self.path, state)
            return {
                "replayed": False,
                "event": {"type": event_type, "plan_revision": next_revision},
                **self.status(as_of, state),
            }

    def record_day(self, payload: dict[str, Any], *, expected_revision: int | None) -> dict[str, Any]:
        errors = _errors(payload, OUTCOME_SCHEMA)
        if errors:
            raise ExamScheduleError("Day outcome is invalid:\n- " + "\n- ".join(errors))
        with FileLock(self.lock_path):
            state = self.state()
            self._expect_revision(state, expected_revision)
            plan = state.get("plan")
            if not isinstance(plan, dict):
                raise ExamScheduleError("Create a canonical schedule before recording a day")
            day = next((item for item in plan["days"] if item["date"] == payload["date"]), None)
            if day is None:
                raise ExamScheduleError("Day outcome date is not present in the current plan")
            planned_ids = {item["id"] for item in day["tasks"]}
            completed_ids = set(payload["completed_task_ids"])
            if not completed_ids <= planned_ids:
                raise ExamScheduleError("Day outcome contains a task outside the planned date")
            status = payload["status"]
            if status == "completed" and completed_ids != planned_ids:
                raise ExamScheduleError("completed requires every planned task ID")
            if status == "partial" and (not completed_ids or completed_ids == planned_ids):
                raise ExamScheduleError("partial requires a non-empty proper subset of planned task IDs")
            if status == "missed" and completed_ids:
                raise ExamScheduleError("missed cannot contain completed task IDs")
            existing = next(
                (
                    item
                    for item in state["outcomes"]
                    if item["plan_revision"] == plan["plan_revision"] and item["date"] == payload["date"]
                ),
                None,
            )
            comparable = {
                "date": payload["date"],
                "plan_revision": plan["plan_revision"],
                "status": status,
                "completed_task_ids": sorted(completed_ids),
                "actual_minutes": payload["actual_minutes"],
                "available_minutes": payload["available_minutes"],
            }
            if existing is not None:
                if {key: existing[key] for key in comparable} != comparable:
                    raise ExamScheduleError("A different outcome is already recorded for this plan date")
                return {"replayed": True, **self.status(state=state), "outcome": existing}
            next_revision = state["revision"] + 1
            timestamp = iso()
            outcome = {**comparable, "recorded_at": timestamp}
            state["revision"] = next_revision
            state["updated_at"] = timestamp
            state["outcomes"].append(outcome)
            event_type = {"completed": "day_completed", "partial": "day_partial", "missed": "day_missed"}[status]
            state["events"].append(
                {
                    "id": f"xplan-{next_revision:06d}",
                    "revision": next_revision,
                    "type": event_type,
                    "at": timestamp,
                    "reason": {"completed": "learner_request", "partial": "day_partial", "missed": "day_missed"}[status],
                    "exam_revision": self.exam.revision,
                    "course_revision": self.workspace.revision,
                    "plan_revision": plan["plan_revision"],
                }
            )
            validation = self.validate(state)
            if validation:
                raise ExamScheduleError("Day outcome mutation is invalid:\n- " + "\n- ".join(validation))
            atomic_yaml(self.path, state)
            return {"replayed": False, **self.status(state=state), "outcome": outcome}

    def validate(self, state: dict[str, Any] | None = None) -> list[str]:
        state = state or self.state()
        errors = _errors(state, STATE_SCHEMA)
        if errors:
            return errors
        if state["revision"] != len(state["events"]):
            errors.append("schedule revision must equal its event count")
        for index, event in enumerate(state["events"], start=1):
            if event["id"] != f"xplan-{index:06d}" or event["revision"] != index:
                errors.append(f"schedule event {index} has an invalid identity or revision")
            try:
                parse_time(event["at"])
            except Exception:
                errors.append(f"schedule event {index} has an invalid timestamp")
        plan = state.get("plan")
        if plan is None:
            if state["configuration"] is not None or state["configuration_sha256"] is not None or state["outcomes"]:
                errors.append("uninitialized schedule contains configuration, plan outcomes, or fingerprint")
            return errors
        if state["configuration_sha256"] != _fingerprint(state["configuration"]):
            errors.append("schedule configuration fingerprint is stale")
        if plan["plan_revision"] > state["revision"]:
            errors.append("plan references a future schedule revision")
        if plan["exam_revision"] > self.exam.revision or plan["course_revision"] > self.workspace.revision:
            errors.append("plan references a future exam or course revision")
        task_ids = [item["id"] for day in plan["days"] for item in day["tasks"]]
        task_ids.extend(item["id"] for item in plan["unscheduled_tasks"])
        if len(task_ids) != len(set(task_ids)):
            errors.append("current plan contains duplicate task IDs")
        outcome_keys: set[tuple[int, str]] = set()
        for outcome in state["outcomes"]:
            key = (outcome["plan_revision"], outcome["date"])
            if key in outcome_keys:
                errors.append("schedule contains duplicate outcomes for one plan date")
            outcome_keys.add(key)
            if outcome["plan_revision"] > state["revision"]:
                errors.append("day outcome references a future plan revision")
            try:
                parse_time(outcome["recorded_at"])
            except Exception:
                errors.append("day outcome has an invalid timestamp")
        return list(dict.fromkeys(errors))
