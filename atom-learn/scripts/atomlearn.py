#!/usr/bin/env python3
"""Deterministic state manager for AtomLearn course workspaces."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment-specific guidance
    raise SystemExit("AtomLearn requires PyYAML. Install it with: python -m pip install PyYAML") from exc


SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
ATOM_STATUSES = {"locked", "available", "active", "mastered", "review_due", "archived"}
MASTERY_LIKE = {"mastered", "review_due"}
PHASES = {
    "orientation",
    "teaching",
    "questioning",
    "checking",
    "reviewing",
    "paused",
    "blocked",
    "transitioning",
}
QUESTION_CLASSES = {
    "in_atom",
    "blocking_prerequisite",
    "non_blocking",
    "future_atom",
    "out_of_scope",
}
QUESTION_PRIORITIES = {"low", "normal", "high"}
QUESTION_STATUSES = {"open", "parked", "resolved", "dismissed"}
EVIDENCE_KINDS = {"mastery_check", "review", "diagnostic"}
REVIEW_STATUSES = {"pending", "completed", "superseded"}
DEFAULT_DIMENSIONS = ["explain", "apply"]
VIEW_FILES = ["LEARNING_MAP.md", "CURRENT.md", "PROGRESS.md", "QUESTIONS.md", "SOURCES.md"]


class AtomLearnError(RuntimeError):
    """A user-correctable workspace or command error."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(value: str | None) -> datetime:
    if not value:
        return now_utc()
    normalized = value.replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AtomLearnError(f"Invalid ISO 8601 timestamp: {value}") from exc
    if result.tzinfo is None:
        raise AtomLearnError("Timestamp must include a timezone offset")
    return result


def iso(value: datetime | None = None) -> str:
    return (value or now_utc()).isoformat()


def read_data(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AtomLearnError(f"Required file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            if path.suffix.lower() == ".json":
                data = json.load(handle)
            else:
                data = yaml.safe_load(handle)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise AtomLearnError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AtomLearnError(f"Expected a mapping in {path}")
    return data


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    atomic_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100))


def unique(items: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def require_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise AtomLearnError(f"{label} must match {ID_PATTERN.pattern}: {value!r}")
    return value


def require_string(value: Any, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise AtomLearnError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AtomLearnError(f"{label} must be a list")
    return value


def require_number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AtomLearnError(f"{label} must be a number")
    number = float(value)
    if not low <= number <= high:
        raise AtomLearnError(f"{label} must be between {low} and {high}")
    return number


def check_schema(record: dict[str, Any], label: str, errors: list[str]) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}: unsupported schema_version {record.get('schema_version')!r}")


def next_record_id(prefix: str, items: list[dict[str, Any]]) -> str:
    maximum = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for item in items:
        match = pattern.fullmatch(str(item.get("id", "")))
        if match:
            maximum = max(maximum, int(match.group(1)))
    return f"{prefix}-{maximum + 1:06d}"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.meta = self.root / ".atomlearn"
        self.atom_dir = self.meta / "atoms"
        self.state_dir = self.meta / "state"
        self.course: dict[str, Any] = {}
        self.graph: dict[str, Any] = {}
        self.current: dict[str, Any] = {}
        self.questions: dict[str, Any] = {}
        self.evidence: dict[str, Any] = {}
        self.reviews: dict[str, Any] = {}
        self.atoms: dict[str, dict[str, Any]] = {}

    @classmethod
    def create(cls, root: Path, course_id: str, title: str, goal: str) -> "Workspace":
        root = root.resolve()
        meta = root / ".atomlearn"
        if meta.exists():
            raise AtomLearnError(f"AtomLearn workspace already exists: {meta}")
        require_id(course_id, "course id")
        require_string(title, "title")
        root.mkdir(parents=True, exist_ok=True)
        (meta / "atoms").mkdir(parents=True, exist_ok=False)
        (meta / "state").mkdir(parents=True, exist_ok=False)
        timestamp = iso()
        workspace = cls(root)
        workspace.course = {
            "schema_version": SCHEMA_VERSION,
            "revision": 0,
            "id": course_id,
            "title": title,
            "goal": goal,
            "status": "orientation",
            "learner": {"prior_knowledge": [], "preferences": []},
            "settings": {"review_intervals_days": [1, 3, 7, 30], "mastery_default_threshold": 0.8},
            "sources": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        workspace.graph = {"schema_version": SCHEMA_VERSION, "revision": 0, "modules": [], "edges": [], "aliases": {}}
        workspace.current = {
            "schema_version": SCHEMA_VERSION,
            "revision": 0,
            "active_atom_id": None,
            "phase": "orientation",
            "current_question": None,
            "learner_understands": [],
            "learner_confusions": [],
            "next_action": "Complete orientation and select the first available Atom.",
            "backtrack_stack": [],
            "updated_at": timestamp,
        }
        workspace.questions = {"schema_version": SCHEMA_VERSION, "revision": 0, "items": []}
        workspace.evidence = {"schema_version": SCHEMA_VERSION, "revision": 0, "items": []}
        workspace.reviews = {"schema_version": SCHEMA_VERSION, "revision": 0, "items": []}
        workspace._write_all()
        atomic_text(workspace.meta / "events.ndjson", "")
        from evolution import initialize_evolution

        initialize_evolution(workspace.root)
        workspace.render()
        return workspace

    def load(self) -> "Workspace":
        if not self.meta.is_dir():
            raise AtomLearnError(f"Not an AtomLearn workspace: {self.root}")
        self.course = read_data(self.meta / "course.yaml")
        self.graph = read_data(self.meta / "graph.yaml")
        self.current = read_data(self.state_dir / "current.yaml")
        self.questions = read_data(self.meta / "questions.yaml")
        self.evidence = read_data(self.meta / "evidence.yaml")
        self.reviews = read_data(self.meta / "reviews.yaml")
        self.atoms = {}
        if not self.atom_dir.is_dir():
            raise AtomLearnError(f"Required directory not found: {self.atom_dir}")
        for path in sorted(self.atom_dir.glob("*.yaml")):
            atom = read_data(path)
            atom_id = atom.get("id")
            if isinstance(atom_id, str) and atom_id in self.atoms:
                raise AtomLearnError(f"Duplicate Atom ID: {atom_id}")
            self.atoms[str(atom_id)] = atom
        return self

    @property
    def revision(self) -> int:
        value = self.course.get("revision")
        if not isinstance(value, int) or value < 0:
            raise AtomLearnError("course.revision must be a non-negative integer")
        return value

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise AtomLearnError(f"Stale revision: expected {expected}, current is {self.revision}. Reload status.")

    def _write_all(self) -> None:
        write_yaml(self.meta / "graph.yaml", self.graph)
        write_yaml(self.state_dir / "current.yaml", self.current)
        write_yaml(self.meta / "questions.yaml", self.questions)
        write_yaml(self.meta / "evidence.yaml", self.evidence)
        write_yaml(self.meta / "reviews.yaml", self.reviews)
        for atom_id, atom in sorted(self.atoms.items()):
            write_yaml(self.atom_dir / f"{atom_id}.yaml", atom)
        # Write the course revision last so it acts as the commit marker. Validation
        # detects a process interruption that leaves other files at a newer revision.
        write_yaml(self.meta / "course.yaml", self.course)

    def _append_event(self, event_type: str, reason: str, details: dict[str, Any] | None = None) -> None:
        event = {
            "event_id": f"evt-{self.revision:06d}",
            "revision": self.revision,
            "type": event_type,
            "at": self.course["updated_at"],
            "actor": "codex",
            "reason": reason,
        }
        if details:
            event["details"] = details
        path = self.meta / "events.ndjson"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def commit(
        self,
        event_type: str,
        reason: str,
        details: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> None:
        timestamp = iso(at)
        self.course["revision"] = self.revision + 1
        new_revision = self.course["revision"]
        for record in [self.graph, self.current, self.questions, self.evidence, self.reviews]:
            record["revision"] = new_revision
        for atom in self.atoms.values():
            atom["revision"] = new_revision
        self.course["updated_at"] = timestamp
        self.current["updated_at"] = timestamp
        self.recalculate_availability()
        self.recalculate_course_status()
        errors = self.validate()
        if errors:
            raise AtomLearnError("Mutation would create invalid state:\n- " + "\n- ".join(errors))
        self._write_all()
        self._append_event(event_type, reason, details)
        self.render()

    def recalculate_availability(self) -> None:
        for atom in self.atoms.values():
            if atom.get("status") in {"active", "mastered", "review_due", "archived"}:
                continue
            prerequisites = atom.get("prerequisites", [])
            satisfied = all(
                prereq in self.atoms and self.atoms[prereq].get("status") in MASTERY_LIKE
                for prereq in prerequisites
            )
            atom["status"] = "available" if satisfied else "locked"

    def recalculate_course_status(self) -> None:
        if self.current.get("phase") == "paused":
            self.course["status"] = "paused"
            return
        required = [
            atom for atom in self.atoms.values()
            if atom.get("status") != "archived" and not atom.get("optional", False)
        ]
        blocking_open = any(
            item.get("classification") == "blocking_prerequisite" and item.get("status") == "open"
            for item in self.questions.get("items", [])
        )
        if self.atoms and all(atom.get("status") in MASTERY_LIKE for atom in required) and not blocking_open:
            self.course["status"] = "completed"
        elif self.atoms:
            self.course["status"] = "active"
        else:
            self.course["status"] = "orientation"

    def validate(self) -> list[str]:
        errors: list[str] = []
        for label, record in [
            ("course", self.course),
            ("graph", self.graph),
            ("current", self.current),
            ("questions", self.questions),
            ("evidence", self.evidence),
            ("reviews", self.reviews),
        ]:
            check_schema(record, label, errors)
            if record.get("revision") != self.course.get("revision"):
                errors.append(
                    f"{label}.revision {record.get('revision')!r} does not match "
                    f"course.revision {self.course.get('revision')!r}"
                )

        try:
            require_id(self.course.get("id"), "course.id")
            require_string(self.course.get("title"), "course.title")
            require_string(self.course.get("goal", ""), "course.goal", allow_empty=True)
        except AtomLearnError as exc:
            errors.append(str(exc))
        if not isinstance(self.course.get("revision"), int) or self.course.get("revision", -1) < 0:
            errors.append("course.revision must be a non-negative integer")
        if self.course.get("status") not in {"orientation", "active", "completed", "paused"}:
            errors.append(f"course.status is invalid: {self.course.get('status')!r}")

        source_ids: set[str] = set()
        for index, source in enumerate(self.course.get("sources", [])):
            try:
                source_id = require_id(source.get("id"), f"sources[{index}].id")
                require_string(source.get("title"), f"sources[{index}].title")
                if source_id in source_ids:
                    errors.append(f"Duplicate source ID: {source_id}")
                source_ids.add(source_id)
            except (AtomLearnError, AttributeError) as exc:
                errors.append(str(exc))

        active_status_ids: list[str] = []
        evidence_ids = {item.get("id") for item in self.evidence.get("items", []) if isinstance(item, dict)}
        for atom_id, atom in self.atoms.items():
            try:
                require_id(atom_id, "atom key")
                if atom.get("id") != atom_id:
                    errors.append(f"Atom file key {atom_id} does not match id {atom.get('id')!r}")
                require_string(atom.get("title"), f"{atom_id}.title")
                require_string(atom.get("objective"), f"{atom_id}.objective")
                require_list(atom.get("prerequisites"), f"{atom_id}.prerequisites")
            except AtomLearnError as exc:
                errors.append(str(exc))
            check_schema(atom, atom_id, errors)
            if atom.get("revision") != self.course.get("revision"):
                errors.append(
                    f"{atom_id}.revision {atom.get('revision')!r} does not match "
                    f"course.revision {self.course.get('revision')!r}"
                )
            status = atom.get("status")
            if status not in ATOM_STATUSES:
                errors.append(f"{atom_id}.status is invalid: {status!r}")
            if status == "active":
                active_status_ids.append(atom_id)
            prerequisites = atom.get("prerequisites", [])
            if isinstance(prerequisites, list):
                for prereq in prerequisites:
                    if prereq == atom_id:
                        errors.append(f"{atom_id} depends on itself")
                    elif prereq not in self.atoms:
                        errors.append(f"{atom_id} has missing prerequisite {prereq!r}")
                    elif self.atoms[prereq].get("status") == "archived":
                        errors.append(f"{atom_id} depends on archived Atom {prereq!r}")
            mastery = atom.get("mastery")
            if not isinstance(mastery, dict):
                errors.append(f"{atom_id}.mastery must be a mapping")
            else:
                dimensions = mastery.get("required_dimensions")
                if not isinstance(dimensions, list) or not dimensions or not all(isinstance(x, str) for x in dimensions):
                    errors.append(f"{atom_id}.mastery.required_dimensions must be a non-empty string list")
                for field, default in [("pass_threshold", 0.8), ("minimum_dimension_score", 0.6)]:
                    try:
                        require_number(mastery.get(field, default), f"{atom_id}.mastery.{field}", 0, 1)
                    except AtomLearnError as exc:
                        errors.append(str(exc))
            for source_ref in atom.get("sources", []):
                if not isinstance(source_ref, dict):
                    errors.append(f"{atom_id}.sources contains a non-mapping")
                elif source_ref.get("source_id") != "synthesized" and source_ref.get("source_id") not in source_ids:
                    errors.append(f"{atom_id} references unknown source {source_ref.get('source_id')!r}")
                elif not source_ref.get("locator") and source_ref.get("source_id") != "synthesized":
                    errors.append(f"{atom_id} has a source without locator")
            if status in MASTERY_LIKE:
                atom_evidence = [item for item in self.evidence.get("items", []) if item.get("atom_id") == atom_id]
                if not any(item.get("result") == "mastered" for item in atom_evidence):
                    errors.append(f"{atom_id} is {status} without mastered Evidence")
            for evidence_id in atom.get("evidence_ids", []):
                if evidence_id not in evidence_ids:
                    errors.append(f"{atom_id} references missing Evidence {evidence_id!r}")

        if len(active_status_ids) > 1:
            errors.append(f"Multiple active Atoms: {', '.join(active_status_ids)}")
        current_active = self.current.get("active_atom_id")
        if current_active is not None and current_active not in self.atoms:
            errors.append(f"current.active_atom_id does not exist: {current_active!r}")
        if current_active is None and active_status_ids:
            errors.append(f"Atom {active_status_ids[0]} is active but current.active_atom_id is null")
        if current_active is not None and active_status_ids != [current_active]:
            errors.append("current.active_atom_id and Atom active status disagree")
        if current_active in self.atoms:
            unsatisfied = [
                prereq
                for prereq in self.atoms[current_active].get("prerequisites", [])
                if self.atoms.get(prereq, {}).get("status") not in MASTERY_LIKE
            ]
            if unsatisfied:
                errors.append(f"Active Atom {current_active} has unsatisfied prerequisites: {', '.join(unsatisfied)}")
        if self.current.get("phase") not in PHASES:
            errors.append(f"current.phase is invalid: {self.current.get('phase')!r}")

        self._validate_dag(errors)
        self._validate_graph_projection(errors)
        self._validate_questions(errors)
        self._validate_evidence(errors)
        self._validate_reviews(errors)
        self._validate_aliases(errors)
        self._validate_course_completion(errors)
        return unique(errors)

    def _validate_course_completion(self, errors: list[str]) -> None:
        if self.course.get("status") != "completed":
            return
        incomplete = [
            atom["id"] for atom in self.atoms.values()
            if atom.get("status") != "archived"
            and not atom.get("optional", False)
            and atom.get("status") not in MASTERY_LIKE
        ]
        blocking = [
            item.get("id") for item in self.questions.get("items", [])
            if item.get("classification") == "blocking_prerequisite" and item.get("status") == "open"
        ]
        if incomplete or blocking:
            errors.append("course.status is completed while required work remains")

    def _validate_dag(self, errors: list[str]) -> None:
        indegree = {atom_id: 0 for atom_id, atom in self.atoms.items() if atom.get("status") != "archived"}
        successors: dict[str, list[str]] = defaultdict(list)
        for atom_id in indegree:
            for prereq in self.atoms[atom_id].get("prerequisites", []):
                if prereq in indegree:
                    indegree[atom_id] += 1
                    successors[prereq].append(atom_id)
        queue = deque(atom_id for atom_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for successor in successors[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        if visited != len(indegree):
            cyclic = sorted(atom_id for atom_id, degree in indegree.items() if degree > 0)
            errors.append(f"Knowledge graph contains a cycle involving: {', '.join(cyclic)}")

    def _validate_graph_projection(self, errors: list[str]) -> None:
        expected = {
            (prereq, atom_id)
            for atom_id, atom in self.atoms.items()
            if atom.get("status") != "archived"
            for prereq in atom.get("prerequisites", [])
        }
        actual: set[tuple[str, str]] = set()
        for edge in self.graph.get("edges", []):
            if not isinstance(edge, dict):
                errors.append("graph.edges contains a non-mapping")
                continue
            actual.add((edge.get("from"), edge.get("to")))
        if actual != expected:
            errors.append("graph.edges does not match Atom prerequisites; run import-plan or restructure")

    def _validate_questions(self, errors: list[str]) -> None:
        ids: set[str] = set()
        for item in self.questions.get("items", []):
            if not isinstance(item, dict):
                errors.append("questions.items contains a non-mapping")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in ids:
                errors.append(f"Invalid or duplicate question ID: {item_id!r}")
            ids.add(item_id)
            if item.get("classification") not in QUESTION_CLASSES:
                errors.append(f"{item_id}.classification is invalid")
            if item.get("priority") not in QUESTION_PRIORITIES:
                errors.append(f"{item_id}.priority is invalid")
            if item.get("status") not in QUESTION_STATUSES:
                errors.append(f"{item_id}.status is invalid")
            for field in ["related_atom_id", "active_atom_id_at_creation"]:
                value = item.get(field)
                if value is not None and value not in self.atoms:
                    errors.append(f"{item_id}.{field} references missing Atom {value!r}")

    def _validate_evidence(self, errors: list[str]) -> None:
        ids: set[str] = set()
        for item in self.evidence.get("items", []):
            if not isinstance(item, dict):
                errors.append("evidence.items contains a non-mapping")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in ids:
                errors.append(f"Invalid or duplicate Evidence ID: {item_id!r}")
            ids.add(item_id)
            if item.get("atom_id") not in self.atoms:
                errors.append(f"{item_id} references missing Atom {item.get('atom_id')!r}")
            if item.get("kind") not in EVIDENCE_KINDS:
                errors.append(f"{item_id}.kind is invalid")
            if item.get("result") not in {"pending", "mastered", "partial", "not_mastered"}:
                errors.append(f"{item_id}.result is invalid")
            scores = item.get("scores")
            if not isinstance(scores, dict) or not scores:
                errors.append(f"{item_id}.scores must be a non-empty mapping")
            else:
                for dimension, score in scores.items():
                    try:
                        require_number(score, f"{item_id}.scores.{dimension}", 0, 1)
                    except AtomLearnError as exc:
                        errors.append(str(exc))

    def _validate_reviews(self, errors: list[str]) -> None:
        ids: set[str] = set()
        pending_by_atom: dict[str, int] = defaultdict(int)
        evidence_ids = {item.get("id") for item in self.evidence.get("items", []) if isinstance(item, dict)}
        for item in self.reviews.get("items", []):
            if not isinstance(item, dict):
                errors.append("reviews.items contains a non-mapping")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in ids:
                errors.append(f"Invalid or duplicate Review ID: {item_id!r}")
            ids.add(item_id)
            if item.get("atom_id") not in self.atoms:
                errors.append(f"{item_id} references missing Atom {item.get('atom_id')!r}")
            if item.get("status") not in REVIEW_STATUSES:
                errors.append(f"{item_id}.status is invalid")
            if item.get("status") == "pending":
                pending_by_atom[str(item.get("atom_id"))] += 1
            if item.get("evidence_id") is not None and item.get("evidence_id") not in evidence_ids:
                errors.append(f"{item_id} references missing Evidence {item.get('evidence_id')!r}")
            try:
                parse_time(item.get("due_at"))
            except AtomLearnError as exc:
                errors.append(f"{item_id}: {exc}")
        for atom_id, count in pending_by_atom.items():
            if count > 1:
                errors.append(f"Atom {atom_id} has multiple pending reviews")
        for atom_id, atom in self.atoms.items():
            if atom.get("status") == "review_due" and pending_by_atom.get(atom_id, 0) != 1:
                errors.append(f"Review-due Atom {atom_id} must have exactly one pending review")

    def _validate_aliases(self, errors: list[str]) -> None:
        aliases = self.graph.get("aliases", {})
        if not isinstance(aliases, dict):
            errors.append("graph.aliases must be a mapping")
            return
        for source, target in aliases.items():
            if source not in self.atoms:
                errors.append(f"Alias source does not exist: {source!r}")
            if target not in self.atoms:
                errors.append(f"Alias target does not exist: {target!r}")
            if source == target:
                errors.append(f"Alias cannot point to itself: {source!r}")

    def rebuild_graph(self) -> None:
        modules = unique(
            atom.get("module", "Uncategorized")
            for atom in self.atoms.values()
            if atom.get("status") != "archived"
        )
        edges = [
            {"from": prereq, "to": atom_id, "type": "prerequisite"}
            for atom_id, atom in self.atoms.items()
            if atom.get("status") != "archived"
            for prereq in atom.get("prerequisites", [])
        ]
        self.graph["modules"] = modules
        self.graph["edges"] = edges

    def import_plan(self, plan: dict[str, Any]) -> dict[str, int]:
        course_update = plan.get("course", {})
        if course_update and not isinstance(course_update, dict):
            raise AtomLearnError("plan.course must be a mapping")
        for field in ["title", "goal"]:
            if field in course_update:
                self.course[field] = require_string(course_update[field], f"course.{field}", allow_empty=field == "goal")
        if "learner" in course_update:
            if not isinstance(course_update["learner"], dict):
                raise AtomLearnError("course.learner must be a mapping")
            self.course["learner"].update(copy.deepcopy(course_update["learner"]))
        if "settings" in course_update:
            if not isinstance(course_update["settings"], dict):
                raise AtomLearnError("course.settings must be a mapping")
            self.course["settings"].update(copy.deepcopy(course_update["settings"]))

        sources = plan.get("sources", [])
        require_list(sources, "plan.sources")
        source_map = {source["id"]: source for source in self.course.get("sources", [])}
        for source in sources:
            if not isinstance(source, dict):
                raise AtomLearnError("Each source must be a mapping")
            source_id = require_id(source.get("id"), "source.id")
            require_string(source.get("title"), f"source {source_id}.title")
            source_map[source_id] = copy.deepcopy(source)
        self.course["sources"] = list(source_map.values())

        atoms = plan.get("atoms", [])
        require_list(atoms, "plan.atoms")
        timestamp = iso()
        added = 0
        updated = 0
        for candidate in atoms:
            if not isinstance(candidate, dict):
                raise AtomLearnError("Each Atom must be a mapping")
            atom_id = require_id(candidate.get("id"), "atom.id")
            require_string(candidate.get("title"), f"{atom_id}.title")
            require_string(candidate.get("objective"), f"{atom_id}.objective")
            prerequisites = require_list(candidate.get("prerequisites", []), f"{atom_id}.prerequisites")
            for prereq in prerequisites:
                require_id(prereq, f"{atom_id}.prerequisite")
            mastery = copy.deepcopy(candidate.get("mastery", {}))
            if not mastery:
                mastery = {
                    "required_dimensions": DEFAULT_DIMENSIONS,
                    "pass_threshold": self.course["settings"].get("mastery_default_threshold", 0.8),
                    "minimum_dimension_score": 0.6,
                }
            existing = self.atoms.get(atom_id)
            progress = {}
            if existing:
                updated += 1
                progress = {
                    key: copy.deepcopy(existing.get(key))
                    for key in ["status", "attempts", "confidence", "last_reviewed_at", "evidence_ids", "created_at"]
                }
            else:
                added += 1
            atom = {
                "schema_version": SCHEMA_VERSION,
                "revision": self.revision,
                "id": atom_id,
                "title": candidate["title"],
                "module": candidate.get("module", "Uncategorized"),
                "objective": candidate["objective"],
                "prerequisites": unique(prerequisites),
                "difficulty": candidate.get("difficulty", 1),
                "estimated_minutes": candidate.get("estimated_minutes", 20),
                "optional": bool(candidate.get("optional", False)),
                "status": "locked",
                "sources": copy.deepcopy(candidate.get("sources", [])),
                "misconceptions": copy.deepcopy(candidate.get("misconceptions", [])),
                "mastery": mastery,
                "attempts": 0,
                "confidence": None,
                "last_reviewed_at": None,
                "evidence_ids": [],
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            atom.update({key: value for key, value in progress.items() if value is not None})
            atom["updated_at"] = timestamp
            self.atoms[atom_id] = atom
        self.rebuild_graph()
        if self.atoms and self.course.get("status") == "orientation":
            self.course["status"] = "active"
        return {"added": added, "updated": updated, "sources": len(sources)}

    def status_summary(self) -> dict[str, Any]:
        active_id = self.current.get("active_atom_id")
        active = self.atoms.get(active_id) if active_id else None
        validation_errors = self.validate()
        counts: dict[str, int] = defaultdict(int)
        for atom in self.atoms.values():
            counts[atom.get("status", "unknown")] += 1
        open_questions = [item for item in self.questions.get("items", []) if item.get("status") in {"open", "parked"}]
        due_reviews = [item for item in self.reviews.get("items", []) if item.get("status") == "pending" and self.atoms.get(item.get("atom_id"), {}).get("status") == "review_due"]
        return {
            "valid": not validation_errors,
            "validation_errors": validation_errors,
            "course": {
                "id": self.course.get("id"),
                "title": self.course.get("title"),
                "goal": self.course.get("goal"),
                "status": self.course.get("status"),
                "revision": self.course.get("revision"),
            },
            "session": copy.deepcopy(self.current),
            "active_atom": copy.deepcopy(active),
            "counts": dict(sorted(counts.items())),
            "open_questions": open_questions,
            "due_reviews": due_reviews,
            "next_candidates": [] if validation_errors else self.suggest_next(),
        }

    def suggest_next(self) -> list[dict[str, Any]]:
        due_ids = {
            item.get("atom_id")
            for item in self.reviews.get("items", [])
            if item.get("status") == "pending" and self.atoms.get(item.get("atom_id"), {}).get("status") == "review_due"
        }
        candidates = [atom for atom in self.atoms.values() if atom.get("status") in {"available", "review_due"}]
        candidates.sort(
            key=lambda atom: (
                0 if atom["id"] in due_ids else 1,
                atom.get("difficulty", 1),
                atom.get("created_at", ""),
                atom["id"],
            )
        )
        return [
            {
                "id": atom["id"],
                "title": atom["title"],
                "module": atom.get("module", "Uncategorized"),
                "status": atom["status"],
                "difficulty": atom.get("difficulty", 1),
            }
            for atom in candidates[:5]
        ]

    def activate(self, atom_id: str) -> None:
        require_id(atom_id, "atom id")
        if self.current.get("active_atom_id") is not None:
            raise AtomLearnError(f"Another Atom is already active: {self.current['active_atom_id']}")
        atom = self.atoms.get(atom_id)
        if not atom:
            raise AtomLearnError(f"Unknown Atom: {atom_id}")
        if atom.get("status") not in {"available", "review_due"}:
            raise AtomLearnError(f"Atom {atom_id} cannot be activated from status {atom.get('status')}")
        reviewing = atom.get("status") == "review_due"
        atom["status"] = "active"
        self.current["active_atom_id"] = atom_id
        self.current["phase"] = "reviewing" if reviewing else "teaching"
        self.current["current_question"] = None
        self.current["learner_confusions"] = []
        self.current["next_action"] = "Run a focused review check." if reviewing else f"Teach why {atom['title']} matters."
        self.course["status"] = "active"

    def update_session(self, payload: dict[str, Any]) -> None:
        if "phase" in payload:
            if payload["phase"] not in PHASES:
                raise AtomLearnError(f"Invalid phase: {payload['phase']!r}")
            self.current["phase"] = payload["phase"]
        for field in ["current_question", "next_action"]:
            if field in payload:
                value = payload[field]
                if value is not None and not isinstance(value, str):
                    raise AtomLearnError(f"{field} must be a string or null")
                self.current[field] = value
        for target, add_key, remove_key in [
            ("learner_understands", "add_understands", "remove_understands"),
            ("learner_confusions", "add_confusions", "remove_confusions"),
        ]:
            values = list(self.current.get(target, []))
            additions = payload.get(add_key, [])
            removals = payload.get(remove_key, [])
            require_list(additions, add_key)
            require_list(removals, remove_key)
            if not all(isinstance(value, str) for value in additions + removals):
                raise AtomLearnError(f"{add_key} and {remove_key} must contain strings")
            self.current[target] = [value for value in unique(values + additions) if value not in set(removals)]
        if self.current.get("phase") != "paused" and self.course.get("status") == "paused":
            self.course["status"] = "active" if self.atoms else "orientation"

    def record_question(self, payload: dict[str, Any]) -> str:
        text = require_string(payload.get("text"), "question.text")
        classification = payload.get("classification")
        if classification not in QUESTION_CLASSES:
            raise AtomLearnError(f"Invalid question classification: {classification!r}")
        priority = payload.get("priority", "normal")
        if priority not in QUESTION_PRIORITIES:
            raise AtomLearnError(f"Invalid question priority: {priority!r}")
        related = payload.get("related_atom_id")
        if related is not None and related not in self.atoms:
            raise AtomLearnError(f"Unknown related Atom: {related}")
        question_id = next_record_id("q", self.questions["items"])
        status = "parked" if classification in {"non_blocking", "future_atom", "out_of_scope"} else "open"
        item = {
            "id": question_id,
            "text": text,
            "classification": classification,
            "related_atom_id": related,
            "active_atom_id_at_creation": self.current.get("active_atom_id"),
            "rationale": require_string(payload.get("rationale"), "question.rationale"),
            "priority": priority,
            "status": status,
            "resolution": None,
            "created_at": iso(),
            "resolved_at": None,
        }
        self.questions["items"].append(item)
        if classification in {"in_atom", "blocking_prerequisite"}:
            self.current["current_question"] = text
            self.current["phase"] = "blocked" if classification == "blocking_prerequisite" else "questioning"
        return question_id

    def resolve_question(self, question_id: str, resolution: str, dismissed: bool = False) -> None:
        item = self.find_record(self.questions["items"], question_id, "Question")
        if item.get("status") in {"resolved", "dismissed"}:
            raise AtomLearnError(f"Question {question_id} is already closed")
        item["status"] = "dismissed" if dismissed else "resolved"
        item["resolution"] = require_string(resolution, "resolution")
        item["resolved_at"] = iso()
        if self.current.get("current_question") == item.get("text"):
            self.current["current_question"] = None

    def record_evidence(self, payload: dict[str, Any]) -> str:
        atom_id = payload.get("atom_id") or self.current.get("active_atom_id")
        if atom_id not in self.atoms:
            raise AtomLearnError(f"Unknown Evidence Atom: {atom_id!r}")
        kind = payload.get("kind", "mastery_check")
        if kind not in EVIDENCE_KINDS:
            raise AtomLearnError(f"Invalid Evidence kind: {kind!r}")
        scores = payload.get("scores")
        if not isinstance(scores, dict) or not scores:
            raise AtomLearnError("evidence.scores must be a non-empty mapping")
        normalized_scores = {
            str(dimension): require_number(score, f"score {dimension}", 0, 1)
            for dimension, score in scores.items()
        }
        evidence_id = next_record_id("ev", self.evidence["items"])
        item = {
            "id": evidence_id,
            "atom_id": atom_id,
            "kind": kind,
            "prompt": require_string(payload.get("prompt"), "evidence.prompt"),
            "response_summary": require_string(payload.get("response_summary"), "evidence.response_summary"),
            "scores": normalized_scores,
            "feedback": require_string(payload.get("feedback"), "evidence.feedback"),
            "rationale": require_string(payload.get("rationale"), "evidence.rationale"),
            "result": "pending",
            "created_at": iso(),
        }
        self.evidence["items"].append(item)
        self.atoms[atom_id].setdefault("evidence_ids", []).append(evidence_id)
        self.current["phase"] = "checking"
        return evidence_id

    def assess(self, atom_id: str, evidence_id: str, at: datetime | None = None) -> str:
        if self.current.get("active_atom_id") != atom_id:
            raise AtomLearnError(f"Atom {atom_id} is not the Active Atom")
        atom = self.atoms.get(atom_id)
        if not atom:
            raise AtomLearnError(f"Unknown Atom: {atom_id}")
        evidence = self.find_record(self.evidence["items"], evidence_id, "Evidence")
        if evidence.get("atom_id") != atom_id:
            raise AtomLearnError(f"Evidence {evidence_id} belongs to {evidence.get('atom_id')}, not {atom_id}")
        if evidence.get("result") != "pending":
            raise AtomLearnError(f"Evidence {evidence_id} was already assessed")
        mastery = atom["mastery"]
        required = mastery["required_dimensions"]
        missing = [dimension for dimension in required if dimension not in evidence["scores"]]
        if missing:
            raise AtomLearnError(f"Evidence is missing required dimensions: {', '.join(missing)}")
        required_scores = [float(evidence["scores"][dimension]) for dimension in required]
        average = sum(required_scores) / len(required_scores)
        minimum = min(required_scores)
        if average >= float(mastery.get("pass_threshold", 0.8)) and minimum >= float(mastery.get("minimum_dimension_score", 0.6)):
            result = "mastered"
        elif average >= 0.5:
            result = "partial"
        else:
            result = "not_mastered"
        evidence["result"] = result
        evidence["assessed_at"] = iso(at)
        atom["attempts"] = int(atom.get("attempts", 0)) + 1
        atom["confidence"] = round(average, 3)
        atom["last_reviewed_at"] = iso(at)
        if result == "mastered":
            atom["status"] = "mastered"
            self.current["active_atom_id"] = None
            self.current["phase"] = "transitioning"
            self.current["current_question"] = None
            self.current["learner_confusions"] = []
            self.current["next_action"] = "Review progress and choose the next available Atom."
            self._complete_due_review(atom_id, evidence_id)
            self._schedule_next_review(atom_id, at or now_utc())
        else:
            atom["status"] = "active"
            weakest = min(required, key=lambda dimension: evidence["scores"][dimension])
            self.current["phase"] = "teaching"
            self.current["next_action"] = f"Remediate the weakest mastery dimension: {weakest}."
        return result

    def _complete_due_review(self, atom_id: str, evidence_id: str) -> None:
        due = [
            item for item in self.reviews["items"]
            if item.get("atom_id") == atom_id and item.get("status") == "pending"
        ]
        if not due:
            return
        due.sort(key=lambda item: (item.get("interval_index", 0), item.get("due_at", "")))
        item = due[0]
        item["status"] = "completed"
        item["evidence_id"] = evidence_id
        item["completed_at"] = iso()

    def _schedule_next_review(self, atom_id: str, base: datetime) -> None:
        intervals = self.course.get("settings", {}).get("review_intervals_days", [1, 3, 7, 30])
        if not isinstance(intervals, list) or not intervals or not all(isinstance(day, int) and day > 0 for day in intervals):
            raise AtomLearnError("settings.review_intervals_days must contain positive integers")
        reviews = [item for item in self.reviews["items"] if item.get("atom_id") == atom_id]
        completed_indexes = [item.get("interval_index", -1) for item in reviews if item.get("status") == "completed"]
        pending = [item for item in reviews if item.get("status") == "pending"]
        if pending:
            return
        next_index = (max(completed_indexes) + 1) if completed_indexes else 0
        if next_index >= len(intervals):
            return
        review_id = next_record_id("rv", self.reviews["items"])
        self.reviews["items"].append(
            {
                "id": review_id,
                "atom_id": atom_id,
                "interval_index": next_index,
                "interval_days": intervals[next_index],
                "due_at": iso(base + timedelta(days=intervals[next_index])),
                "status": "pending",
                "evidence_id": None,
                "created_at": iso(base),
                "completed_at": None,
            }
        )

    def refresh_reviews(self, at: datetime) -> list[str]:
        due_ids: list[str] = []
        for item in self.reviews["items"]:
            if item.get("status") != "pending" or parse_time(item.get("due_at")) > at:
                continue
            atom = self.atoms.get(item.get("atom_id"))
            if atom and atom.get("status") == "mastered":
                atom["status"] = "review_due"
                due_ids.append(atom["id"])
        return unique(due_ids)

    def pause(self, reason: str) -> None:
        self.course["status"] = "paused"
        self.current["phase"] = "paused"
        self.current["next_action"] = require_string(reason, "pause reason")

    def backtrack(self, target_id: str, question_id: str | None) -> None:
        parent_id = self.current.get("active_atom_id")
        if not parent_id:
            raise AtomLearnError("Backtracking requires an Active Atom")
        if target_id == parent_id:
            raise AtomLearnError("Backtrack target must differ from the Active Atom")
        target = self.atoms.get(target_id)
        if not target:
            raise AtomLearnError(f"Unknown backtrack target: {target_id}")
        if target.get("status") not in {"available", "mastered", "review_due"}:
            raise AtomLearnError(f"Backtrack target {target_id} is not available for remediation")
        if question_id:
            question = self.find_record(self.questions["items"], question_id, "Question")
            if question.get("classification") != "blocking_prerequisite":
                raise AtomLearnError(f"Question {question_id} is not blocking_prerequisite")
        stack_item = {
            "atom_id": parent_id,
            "phase": self.current.get("phase"),
            "current_question": self.current.get("current_question"),
            "next_action": self.current.get("next_action"),
            "question_id": question_id,
        }
        self.current.setdefault("backtrack_stack", []).append(stack_item)
        self.atoms[parent_id]["status"] = "available"
        target["status"] = "active"
        self.current["active_atom_id"] = target_id
        self.current["phase"] = "reviewing" if target.get("last_reviewed_at") else "teaching"
        self.current["current_question"] = None
        self.current["next_action"] = f"Repair prerequisite {target['title']}, then return to {self.atoms[parent_id]['title']}."

    def resume(self) -> str:
        if self.current.get("active_atom_id") is not None:
            raise AtomLearnError("Finish the remedial Active Atom before resuming")
        stack = self.current.get("backtrack_stack", [])
        if not stack:
            raise AtomLearnError("No backtrack state to resume")
        saved = stack[-1]
        parent_id = saved["atom_id"]
        parent = self.atoms.get(parent_id)
        if not parent or parent.get("status") != "available":
            raise AtomLearnError(f"Saved parent Atom {parent_id} is not available")
        unsatisfied = [
            prereq for prereq in parent.get("prerequisites", [])
            if self.atoms.get(prereq, {}).get("status") not in MASTERY_LIKE
        ]
        if unsatisfied:
            raise AtomLearnError(f"Cannot resume; prerequisites remain unmastered: {', '.join(unsatisfied)}")
        stack.pop()
        parent["status"] = "active"
        self.current["active_atom_id"] = parent_id
        self.current["phase"] = "questioning"
        self.current["current_question"] = saved.get("current_question")
        self.current["next_action"] = saved.get("next_action") or "Reconnect the repaired prerequisite to the original question."
        question_id = saved.get("question_id")
        if question_id:
            question = self.find_record(self.questions["items"], question_id, "Question")
            question["status"] = "resolved"
            question["resolution"] = "Prerequisite remediation completed; resumed parent Atom."
            question["resolved_at"] = iso()
        return parent_id

    def restructure(self, proposal: dict[str, Any]) -> dict[str, Any]:
        action = proposal.get("action")
        if action == "split":
            return self._split(proposal)
        if action == "merge":
            return self._merge(proposal)
        raise AtomLearnError("Restructure action must be split or merge")

    def _split(self, proposal: dict[str, Any]) -> dict[str, Any]:
        source_id = proposal.get("source_atom_id")
        source = self.atoms.get(source_id)
        if not source:
            raise AtomLearnError(f"Unknown split source Atom: {source_id!r}")
        if source.get("status") == "active":
            raise AtomLearnError("Cannot split the Active Atom")
        candidates = proposal.get("new_atoms")
        if not isinstance(candidates, list) or len(candidates) < 2:
            raise AtomLearnError("Split requires at least two new_atoms")
        replacement = proposal.get("downstream_replacement_id")
        new_ids = [require_id(item.get("id"), "new atom id") for item in candidates if isinstance(item, dict)]
        if len(new_ids) != len(candidates) or len(set(new_ids)) != len(new_ids):
            raise AtomLearnError("Split new_atoms must have unique valid IDs")
        if replacement not in new_ids:
            raise AtomLearnError("downstream_replacement_id must name one of new_atoms")
        if any(atom_id in self.atoms for atom_id in new_ids):
            raise AtomLearnError("Split new Atom IDs must not already exist")
        self._add_restructure_atoms(candidates, inherited_sources=source.get("sources", []))
        for atom in self.atoms.values():
            if atom["id"] not in new_ids:
                atom["prerequisites"] = [replacement if item == source_id else item for item in atom.get("prerequisites", [])]
        source["status"] = "archived"
        source["archived_reason"] = "split"
        self.graph.setdefault("aliases", {})[source_id] = replacement
        self._migrate_pending_state([source_id], replacement)
        self.rebuild_graph()
        return {"action": "split", "archived": [source_id], "created": new_ids, "alias": {source_id: replacement}}

    def _merge(self, proposal: dict[str, Any]) -> dict[str, Any]:
        source_ids = proposal.get("source_atom_ids")
        if not isinstance(source_ids, list) or len(source_ids) < 2:
            raise AtomLearnError("Merge requires at least two source_atom_ids")
        source_ids = unique(require_id(item, "merge source id") for item in source_ids)
        if any(atom_id not in self.atoms for atom_id in source_ids):
            raise AtomLearnError("One or more merge source Atoms do not exist")
        if any(self.atoms[atom_id].get("status") == "active" for atom_id in source_ids):
            raise AtomLearnError("Cannot merge an Active Atom")
        merged = proposal.get("merged_atom")
        if not isinstance(merged, dict):
            raise AtomLearnError("merged_atom must be a mapping")
        merged_id = require_id(merged.get("id"), "merged atom id")
        if merged_id in self.atoms:
            raise AtomLearnError(f"Merged Atom ID already exists: {merged_id}")
        inherited_sources = []
        for atom_id in source_ids:
            inherited_sources.extend(self.atoms[atom_id].get("sources", []))
        if "prerequisites" not in merged:
            merged["prerequisites"] = unique(
                prereq
                for atom_id in source_ids
                for prereq in self.atoms[atom_id].get("prerequisites", [])
                if prereq not in source_ids
            )
        self._add_restructure_atoms([merged], inherited_sources=inherited_sources)
        for atom in self.atoms.values():
            if atom["id"] != merged_id:
                replaced = [merged_id if item in source_ids else item for item in atom.get("prerequisites", [])]
                atom["prerequisites"] = unique(replaced)
        aliases = self.graph.setdefault("aliases", {})
        for atom_id in source_ids:
            self.atoms[atom_id]["status"] = "archived"
            self.atoms[atom_id]["archived_reason"] = "merged"
            aliases[atom_id] = merged_id
        self._migrate_pending_state(source_ids, merged_id)
        self.rebuild_graph()
        return {"action": "merge", "archived": source_ids, "created": [merged_id], "aliases": {key: merged_id for key in source_ids}}

    def _migrate_pending_state(self, source_ids: list[str], target_id: str) -> None:
        source_set = set(source_ids)
        for question in self.questions.get("items", []):
            if question.get("related_atom_id") in source_set and question.get("status") in {"open", "parked"}:
                question["migrated_from_atom_id"] = question["related_atom_id"]
                question["related_atom_id"] = target_id
        for review in self.reviews.get("items", []):
            if review.get("atom_id") in source_set and review.get("status") == "pending":
                review["status"] = "superseded"
                review["superseded_by_atom_id"] = target_id
                review["superseded_at"] = iso()

    def _add_restructure_atoms(self, candidates: list[dict[str, Any]], inherited_sources: list[dict[str, Any]]) -> None:
        timestamp = iso()
        for candidate in candidates:
            atom_id = require_id(candidate.get("id"), "new atom id")
            self.atoms[atom_id] = {
                "schema_version": SCHEMA_VERSION,
                "revision": self.revision,
                "id": atom_id,
                "title": require_string(candidate.get("title"), f"{atom_id}.title"),
                "module": candidate.get("module", "Uncategorized"),
                "objective": require_string(candidate.get("objective"), f"{atom_id}.objective"),
                "prerequisites": unique(candidate.get("prerequisites", [])),
                "difficulty": candidate.get("difficulty", 1),
                "estimated_minutes": candidate.get("estimated_minutes", 20),
                "optional": bool(candidate.get("optional", False)),
                "status": "locked",
                "sources": copy.deepcopy(candidate.get("sources", inherited_sources)),
                "misconceptions": copy.deepcopy(candidate.get("misconceptions", [])),
                "mastery": copy.deepcopy(candidate.get("mastery", {
                    "required_dimensions": DEFAULT_DIMENSIONS,
                    "pass_threshold": 0.8,
                    "minimum_dimension_score": 0.6,
                })),
                "attempts": 0,
                "confidence": None,
                "last_reviewed_at": None,
                "evidence_ids": [],
                "created_at": timestamp,
                "updated_at": timestamp,
            }

    @staticmethod
    def find_record(items: list[dict[str, Any]], record_id: str, label: str) -> dict[str, Any]:
        for item in items:
            if item.get("id") == record_id:
                return item
        raise AtomLearnError(f"{label} not found: {record_id}")

    def render(self) -> None:
        active_id = self.current.get("active_atom_id")
        status_icon = {
            "locked": "🔒",
            "available": "○",
            "active": "▶",
            "mastered": "✓",
            "review_due": "↻",
            "archived": "—",
        }
        modules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for atom in self.atoms.values():
            modules[atom.get("module", "Uncategorized")].append(atom)
        map_lines = [f"# {self.course.get('title')} Learning Map", "", "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.", ""]
        for module, atoms in modules.items():
            map_lines.extend([f"## {module}", ""])
            for atom in atoms:
                map_lines.append(f"- {status_icon.get(atom.get('status'), '?')} `{atom['id']}` — {atom['title']} ({atom.get('status')})")
            map_lines.append("")

        active = self.atoms.get(active_id) if active_id else None
        current_lines = [
            "# Current Learning State",
            "",
            "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.",
            "",
            "## Course",
            "",
            str(self.course.get("title")),
            "",
            "## Active Atom",
            "",
            f"`{active_id}` — {active['title']}" if active else "None",
            "",
            "## Phase",
            "",
            str(self.current.get("phase")),
            "",
            "## Current Question",
            "",
            str(self.current.get("current_question") or "None"),
            "",
            "## Learner Understands",
            "",
        ]
        current_lines.extend([f"- {item}" for item in self.current.get("learner_understands", [])] or ["- None recorded"])
        current_lines.extend(["", "## Learner Confusions", ""])
        current_lines.extend([f"- {item}" for item in self.current.get("learner_confusions", [])] or ["- None recorded"])
        current_lines.extend(["", "## Next Action", "", str(self.current.get("next_action") or "None"), "", "## Backtrack Depth", "", str(len(self.current.get("backtrack_stack", []))), ""])

        non_archived = [atom for atom in self.atoms.values() if atom.get("status") != "archived"]
        mastered = [atom for atom in non_archived if atom.get("status") in MASTERY_LIKE]
        percent = (100 * len(mastered) / len(non_archived)) if non_archived else 0
        progress_lines = [
            "# Learning Progress",
            "",
            "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.",
            "",
            "## Overall",
            "",
            f"{len(mastered)} / {len(non_archived)} Atoms ({percent:.1f}%)",
            "",
            "## Current",
            "",
            f"`{active_id}` — {active['title']}" if active else "None",
            "",
            "## Available Next",
            "",
        ]
        progress_lines.extend([f"- `{item['id']}` — {item['title']} ({item['status']})" for item in self.suggest_next()] or ["- None"])
        progress_lines.append("")

        question_lines = ["# Questions", "", "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.", ""]
        groups = [("Blocking and Current", {"open"}), ("Parking Lot", {"parked"}), ("Resolved", {"resolved", "dismissed"})]
        for title, statuses in groups:
            question_lines.extend([f"## {title}", ""])
            items = [item for item in self.questions.get("items", []) if item.get("status") in statuses]
            for item in items:
                relation = f" → `{item['related_atom_id']}`" if item.get("related_atom_id") else ""
                question_lines.append(f"- **{item['id']}** [{item['classification']}] {item['text']}{relation}")
            if not items:
                question_lines.append("- None")
            question_lines.append("")

        source_lines = ["# Sources", "", "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.", ""]
        for source in self.course.get("sources", []):
            source_lines.extend([f"## {source.get('title')}", "", f"- ID: `{source.get('id')}`", f"- Type: {source.get('type', 'unknown')}", f"- Location: {source.get('location', 'not recorded')}"])
            if source.get("version"):
                source_lines.append(f"- Version: {source['version']}")
            source_lines.append("")
        if not self.course.get("sources"):
            source_lines.append("No sources recorded.\n")

        rendered = {
            "LEARNING_MAP.md": map_lines,
            "CURRENT.md": current_lines,
            "PROGRESS.md": progress_lines,
            "QUESTIONS.md": question_lines,
            "SOURCES.md": source_lines,
        }
        for filename, lines in rendered.items():
            atomic_text(self.root / filename, "\n".join(lines).rstrip() + "\n")


def load_workspace(path: str) -> Workspace:
    return Workspace(Path(path)).load()


def emit(data: Any, as_json: bool = False) -> None:
    if as_json or isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data)


def mutation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage AtomLearn course state")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Create a course workspace")
    init_parser.add_argument("workspace")
    init_parser.add_argument("--course-id", required=True)
    init_parser.add_argument("--title", required=True)
    init_parser.add_argument("--goal", default="")

    for command in ["validate", "render", "status", "suggest-next"]:
        command_parser = sub.add_parser(command)
        command_parser.add_argument("workspace")
        if command == "status":
            command_parser.add_argument("--json", action="store_true")

    import_parser = sub.add_parser("import-plan")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    mutation_args(import_parser)

    activate_parser = sub.add_parser("activate")
    activate_parser.add_argument("workspace")
    activate_parser.add_argument("atom_id")
    mutation_args(activate_parser)

    update_parser = sub.add_parser("update-session")
    update_parser.add_argument("workspace")
    update_parser.add_argument("--input", required=True)
    mutation_args(update_parser)

    question_parser = sub.add_parser("record-question")
    question_parser.add_argument("workspace")
    question_parser.add_argument("--input", required=True)
    mutation_args(question_parser)

    resolve_parser = sub.add_parser("resolve-question")
    resolve_parser.add_argument("workspace")
    resolve_parser.add_argument("question_id")
    resolve_parser.add_argument("--resolution", required=True)
    resolve_parser.add_argument("--dismissed", action="store_true")
    mutation_args(resolve_parser)

    evidence_parser = sub.add_parser("record-evidence")
    evidence_parser.add_argument("workspace")
    evidence_parser.add_argument("--input", required=True)
    mutation_args(evidence_parser)

    assess_parser = sub.add_parser("assess")
    assess_parser.add_argument("workspace")
    assess_parser.add_argument("atom_id")
    assess_parser.add_argument("--evidence-id", required=True)
    assess_parser.add_argument("--now")
    mutation_args(assess_parser)

    refresh_parser = sub.add_parser("refresh-reviews")
    refresh_parser.add_argument("workspace")
    refresh_parser.add_argument("--now")
    mutation_args(refresh_parser)

    pause_parser = sub.add_parser("pause")
    pause_parser.add_argument("workspace")
    pause_parser.add_argument("--reason", required=True)
    mutation_args(pause_parser)

    backtrack_parser = sub.add_parser("backtrack")
    backtrack_parser.add_argument("workspace")
    backtrack_parser.add_argument("--to", required=True, dest="target_id")
    backtrack_parser.add_argument("--question-id")
    mutation_args(backtrack_parser)

    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("workspace")
    mutation_args(resume_parser)

    restructure_parser = sub.add_parser("restructure")
    restructure_parser.add_argument("workspace")
    restructure_parser.add_argument("--proposal", required=True)
    restructure_parser.add_argument("--confirmed", action="store_true")
    mutation_args(restructure_parser)
    evolve_parser = sub.add_parser("evolve", add_help=False)
    evolve_parser.add_argument("-h", "--help", action="store_true", dest="evolution_help")
    evolve_parser.add_argument("evolution_args", nargs=argparse.REMAINDER)
    return parser


def run(args: argparse.Namespace) -> None:
    if args.command == "evolve":
        from evolution import AtomLearnError as EvolutionAtomLearnError
        from evolution import EvolutionError, run as run_evolution

        try:
            run_evolution(["--help"] if args.evolution_help else args.evolution_args)
        except (EvolutionError, EvolutionAtomLearnError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "init":
        workspace = Workspace.create(Path(args.workspace), args.course_id, args.title, args.goal)
        emit({"ok": True, "workspace": str(workspace.root), "revision": 0})
        return

    workspace = load_workspace(args.workspace)
    if args.command == "validate":
        errors = workspace.validate()
        evolution_root = workspace.meta / "evolution"
        if evolution_root.is_dir():
            from evolution import EvolutionEngine

            errors.extend(f"evolution: {error}" for error in EvolutionEngine(workspace).validate())
        if errors:
            raise AtomLearnError("Workspace validation failed:\n- " + "\n- ".join(errors))
        emit({"ok": True, "revision": workspace.revision, "atoms": len(workspace.atoms)})
        return
    if args.command == "render":
        errors = workspace.validate()
        if errors:
            raise AtomLearnError("Cannot render invalid workspace:\n- " + "\n- ".join(errors))
        workspace.render()
        emit({"ok": True, "views": VIEW_FILES})
        return
    if args.command == "status":
        emit(workspace.status_summary(), as_json=args.json)
        return
    if args.command == "suggest-next":
        errors = workspace.validate()
        if errors:
            raise AtomLearnError("Cannot suggest from an invalid workspace:\n- " + "\n- ".join(errors))
        emit(workspace.suggest_next())
        return

    existing_errors = workspace.validate()
    if existing_errors:
        raise AtomLearnError(
            "Refusing to mutate an invalid workspace; run validate and repair it first:\n- "
            + "\n- ".join(existing_errors)
        )
    workspace.expect_revision(getattr(args, "expected_revision", None))
    if args.command == "import-plan":
        result = workspace.import_plan(read_data(Path(args.input)))
        workspace.commit("plan.imported", "Imported or updated the learning plan", result)
        emit({"ok": True, "revision": workspace.revision, **result})
    elif args.command == "activate":
        workspace.activate(args.atom_id)
        workspace.commit("atom.activated", "Activated a learner-selected Atom", {"atom_id": args.atom_id})
        emit({"ok": True, "revision": workspace.revision, "active_atom_id": args.atom_id})
    elif args.command == "update-session":
        workspace.update_session(read_data(Path(args.input)))
        workspace.commit("session.updated", "Persisted the current teaching state")
        emit({"ok": True, "revision": workspace.revision})
    elif args.command == "record-question":
        question_id = workspace.record_question(read_data(Path(args.input)))
        workspace.commit("question.recorded", "Recorded and routed a learner question", {"question_id": question_id})
        emit({"ok": True, "revision": workspace.revision, "question_id": question_id})
    elif args.command == "resolve-question":
        workspace.resolve_question(args.question_id, args.resolution, args.dismissed)
        workspace.commit("question.closed", "Closed a recorded question", {"question_id": args.question_id})
        emit({"ok": True, "revision": workspace.revision, "question_id": args.question_id})
    elif args.command == "record-evidence":
        evidence_id = workspace.record_evidence(read_data(Path(args.input)))
        workspace.commit("evidence.recorded", "Recorded learner performance", {"evidence_id": evidence_id})
        emit({"ok": True, "revision": workspace.revision, "evidence_id": evidence_id})
    elif args.command == "assess":
        at = parse_time(args.now)
        result = workspace.assess(args.atom_id, args.evidence_id, at)
        workspace.commit("atom.assessed", "Applied the Atom mastery rubric", {"atom_id": args.atom_id, "evidence_id": args.evidence_id, "result": result}, at)
        emit({"ok": True, "revision": workspace.revision, "result": result})
    elif args.command == "refresh-reviews":
        at = parse_time(args.now)
        due = workspace.refresh_reviews(at)
        workspace.commit("reviews.refreshed", "Marked due reviews", {"due_atom_ids": due}, at)
        emit({"ok": True, "revision": workspace.revision, "due_atom_ids": due})
    elif args.command == "pause":
        workspace.pause(args.reason)
        workspace.commit("course.paused", args.reason)
        emit({"ok": True, "revision": workspace.revision})
    elif args.command == "backtrack":
        workspace.backtrack(args.target_id, args.question_id)
        workspace.commit("session.backtracked", "Activated a blocking prerequisite", {"target_atom_id": args.target_id, "question_id": args.question_id})
        emit({"ok": True, "revision": workspace.revision, "active_atom_id": args.target_id})
    elif args.command == "resume":
        atom_id = workspace.resume()
        workspace.commit("session.resumed", "Returned from prerequisite remediation", {"atom_id": atom_id})
        emit({"ok": True, "revision": workspace.revision, "active_atom_id": atom_id})
    elif args.command == "restructure":
        if not args.confirmed:
            proposal = read_data(Path(args.proposal))
            emit({"ok": True, "applied": False, "proposal": proposal, "message": "Review the proposal and rerun with --confirmed."})
            return
        result = workspace.restructure(read_data(Path(args.proposal)))
        workspace.commit("graph.restructured", "Applied a learner-confirmed Atom restructure", result)
        emit({"ok": True, "revision": workspace.revision, "applied": True, **result})
    else:  # pragma: no cover
        raise AtomLearnError(f"Unhandled command: {args.command}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    try:
        run(parser.parse_args())
        return 0
    except AtomLearnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
