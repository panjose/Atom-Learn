#!/usr/bin/env python3
"""Deterministic state manager for AtomLearn course workspaces."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sqlite3
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


# Prefer this Skill's sibling modules over unrelated third-party packages with
# names such as ``intake`` when the installed console entry delegates commands.
RUNTIME_DIR = str(Path(__file__).resolve().parent)
if not sys.path or str(Path(sys.path[0] or ".").resolve()) != RUNTIME_DIR:
    sys.path = [item for item in sys.path if str(Path(item or ".").resolve()) != RUNTIME_DIR]
    sys.path.insert(0, RUNTIME_DIR)


SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
ATOM_STATUSES = {"locked", "available", "active", "mastered", "review_due", "skipped", "deferred", "archived"}
MASTERY_LIKE = {"mastered", "review_due"}
SATISFIED_STATUSES = MASTERY_LIKE | {"skipped"}
SKIP_MODES = {"diagnostic", "provisional", "defer"}
SKIP_REASON_CODES = {"already_mastered", "too_easy", "not_relevant", "time_constraint", "different_goal", "other"}
SKIP_POLICIES = {"diagnostic_first", "learner_choice", "strict_mastery"}
FLEXIBILITY_KEYS = {
    "mode", "reason_code", "note", "diagnostic_offered", "confirmed", "created_at", "revoked_at",
}
EXPANSION_REASON_CODES = {"learner_requested_detail", "cognitive_load", "remediation", "other"}
EXPANSION_KEYS = {
    "child_atom_ids", "base_prerequisite_ids", "reason_code", "note", "requested_at", "completed_at",
}
EXPANSION_FRAME_KEYS = {"parent_atom_id", "child_atom_ids", "started_at", "backtrack_depth"}
MIN_EXPANSION_CHILDREN = 2
MAX_EXPANSION_CHILDREN = 12
CONCEPT_RELATIONS = {
    "inside_current",
    "required_prerequisite",
    "scheduled_successor",
    "optional_extension",
    "out_of_scope",
}
CONCEPT_ACTIONS = {
    "preview",
    "explain_now",
    "learn_prerequisite",
    "diagnose_prerequisite",
    "park",
    "brief_context",
    "add_optional_branch",
    "dismiss",
}
CONCEPT_ROUTING_KEYS = {"concept", "relation", "action", "impact", "at"}
BRANCH_KEYS = {"kind", "anchor_atom_id", "origin_question_id", "created_at"}
BRANCH_KINDS = {"optional_extension"}
PHASES = {
    "orientation",
    "teaching",
    "questioning",
    "checking",
    "integrating",
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
    "optional_extension",
    "out_of_scope",
}
QUESTION_PRIORITIES = {"low", "normal", "high"}
QUESTION_STATUSES = {"open", "parked", "resolved", "dismissed"}
EVIDENCE_KINDS = {"mastery_check", "review", "diagnostic"}
REVIEW_STATUSES = {"pending", "completed", "superseded"}
DEFAULT_DIMENSIONS = ["explain", "apply"]
VIEW_FILES = ["LEARNING_MAP.md", "CURRENT.md", "PROGRESS.md", "QUESTIONS.md", "SOURCES.md"]
ZH_VIEW_FILES = [path.replace(".md", ".zh-CN.md") for path in VIEW_FILES]


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


def chinese_view(lines: list[str]) -> list[str]:
    exact = {
        "# Current Learning State": "# 当前学习状态",
        "# Learning Progress": "# 学习进度",
        "# Questions": "# 问题",
        "# Sources": "# 资料来源",
        "## Course": "## 课程",
        "## Active Atom": "## 当前 Active Atom",
        "## Phase": "## 阶段",
        "## Current Question": "## 当前问题",
        "## Learner Understands": "## 学习者已理解",
        "## Learner Confusions": "## 学习者困惑",
        "## Next Action": "## 下一步",
        "## Backtrack Depth": "## 回退深度",
        "## Detailed Expansion Depth": "## 详细展开深度",
        "## Overall": "## 总览",
        "## Current": "## 当前",
        "## Available Next": "## 接下来可学",
        "## Flexible Decisions": "## 弹性决策",
        "## Optional Branches": "## 可选支线",
        "## Detailed Expansions": "## 详细展开",
        "## Blocking and Current": "## 阻塞与当前问题",
        "## Parking Lot": "## 待处理问题",
        "## Resolved": "## 已解决",
        "None": "无",
        "- None": "- 无",
        "- None recorded": "- 尚无记录",
        "No sources recorded.": "尚未记录资料来源。",
        "orientation": "导向",
        "teaching": "学习中",
        "reviewing": "复习中",
        "paused": "已暂停",
    }
    prefixes = {
        "- Mastered with Evidence:": "- 有 Evidence 的已掌握项：",
        "- Provisionally skipped:": "- 暂定跳过：",
        "- Deferred:": "- 已延后：",
        "- Path satisfied:": "- 路径已满足：",
        "- Optional Atoms:": "- 可选 Atom：",
        "- ID:": "- ID：",
        "- Type:": "- 类型：",
        "- Location:": "- 位置：",
        "- Version:": "- 版本：",
    }
    statuses = {
        "locked": "锁定",
        "available": "可学习",
        "active": "进行中",
        "mastered": "已掌握",
        "review_due": "待复习",
        "skipped": "暂定跳过",
        "deferred": "已延后",
        "archived": "已归档",
    }
    translated: list[str] = []
    for line in lines:
        if line.startswith("# ") and line.endswith(" Learning Map"):
            line = "# " + line[2:-13] + " 学习地图"
        line = exact.get(line, line)
        if line.startswith("> Generated by AtomLearn."):
            line = "> 由 AtomLearn 自动生成。请通过 CLI 修改 `.atomlearn/` 规范状态。"
        for source, target in prefixes.items():
            if line.startswith(source):
                line = target + line[len(source):]
                break
        line = line.replace(" [optional branch]", " [可选支线]")
        for source, target in statuses.items():
            line = line.replace(f"({source})", f"({target})")
        translated.append(line)
    return translated


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
            "settings": {
                "review_intervals_days": [1, 3, 7, 30],
                "mastery_default_threshold": 0.8,
                "skip_policy": "diagnostic_first",
            },
            "sources": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        workspace.graph = {
            "schema_version": SCHEMA_VERSION,
            "revision": 0,
            "modules": [],
            "edges": [],
            "expansions": [],
            "branches": [],
            "aliases": {},
        }
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
            "expansion_stack": [],
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
            if atom.get("status") in {"active", "mastered", "review_due", "skipped", "deferred", "archived"}:
                continue
            expansion = atom.get("expansion")
            if (
                isinstance(expansion, dict)
                and expansion.get("completed_at") is None
                and not all(
                    self.atoms.get(child_id, {}).get("status") in MASTERY_LIKE
                    for child_id in expansion.get("child_atom_ids", [])
                )
            ):
                atom["status"] = "locked"
                continue
            prerequisites = atom.get("prerequisites", [])
            satisfied = all(
                prereq in self.atoms and self.atoms[prereq].get("status") in SATISFIED_STATUSES
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
        if self.atoms and all(atom.get("status") in SATISFIED_STATUSES for atom in required) and not blocking_open:
            self.course["status"] = (
                "completed_with_skips" if any(atom.get("status") == "skipped" for atom in required) else "completed"
            )
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
        if self.course.get("status") not in {"orientation", "active", "completed", "completed_with_skips", "paused"}:
            errors.append(f"course.status is invalid: {self.course.get('status')!r}")
        settings = self.course.get("settings", {})
        if not isinstance(settings, dict) or settings.get("skip_policy", "diagnostic_first") not in SKIP_POLICIES:
            errors.append("course.settings.skip_policy must be diagnostic_first, learner_choice, or strict_mastery")

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
            self._validate_flexibility(atom_id, atom, errors)
            for evidence_id in atom.get("evidence_ids", []):
                if evidence_id not in evidence_ids:
                    errors.append(f"{atom_id} references missing Evidence {evidence_id!r}")

        self._validate_expansions(errors)
        self._validate_branches(errors)

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
                if self.atoms.get(prereq, {}).get("status") not in SATISFIED_STATUSES
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

    def _validate_flexibility(self, atom_id: str, atom: dict[str, Any], errors: list[str]) -> None:
        record = atom.get("flexibility")
        status = atom.get("status")
        if record is None:
            if status in {"skipped", "deferred"}:
                errors.append(f"{atom_id} is {status} without an active flexibility record")
            return
        if not isinstance(record, dict) or set(record) != FLEXIBILITY_KEYS:
            errors.append(f"{atom_id}.flexibility fields are invalid")
            return
        mode = record.get("mode")
        if mode not in {"provisional", "defer"}:
            errors.append(f"{atom_id}.flexibility.mode is invalid")
        if record.get("reason_code") not in SKIP_REASON_CODES:
            errors.append(f"{atom_id}.flexibility.reason_code is invalid")
        note = record.get("note")
        if not isinstance(note, str) or len(note) > 1000:
            errors.append(f"{atom_id}.flexibility.note must be a string of at most 1000 characters")
        if not isinstance(record.get("diagnostic_offered"), bool) or not isinstance(record.get("confirmed"), bool):
            errors.append(f"{atom_id}.flexibility diagnostic and confirmation flags must be booleans")
        try:
            created_at = record.get("created_at")
            if not isinstance(created_at, str) or not created_at:
                raise AtomLearnError("created_at must be a timestamp string")
            parse_time(created_at)
            revoked_at = record.get("revoked_at")
            if revoked_at is not None:
                if not isinstance(revoked_at, str) or not revoked_at:
                    raise AtomLearnError("revoked_at must be null or a timestamp string")
                parse_time(revoked_at)
        except AtomLearnError as exc:
            errors.append(f"{atom_id}.flexibility: {exc}")
        active = record.get("revoked_at") is None
        expected_status = "skipped" if mode == "provisional" else "deferred"
        if active and status != expected_status:
            errors.append(f"{atom_id} has an active {mode} record but status is {status}")
        if not active and status in {"skipped", "deferred"}:
            errors.append(f"{atom_id} is {status} with a revoked flexibility record")
        if mode == "provisional" and active and (
            record.get("diagnostic_offered") is not True or record.get("confirmed") is not True
        ):
            errors.append(f"{atom_id} provisional skip requires an offered diagnostic and explicit confirmation")
        if (
            mode == "provisional"
            and active
            and self.course.get("settings", {}).get("skip_policy", "diagnostic_first") == "strict_mastery"
        ):
            errors.append(f"{atom_id} has a provisional skip under strict_mastery policy")

    def _validate_expansions(self, errors: list[str]) -> None:
        claimed_children: dict[str, str] = {}
        for parent_id, parent in self.atoms.items():
            record = parent.get("expansion")
            if record is None:
                continue
            if not isinstance(record, dict) or set(record) != EXPANSION_KEYS:
                errors.append(f"{parent_id}.expansion fields are invalid")
                continue
            child_ids = record.get("child_atom_ids")
            base_prerequisites = record.get("base_prerequisite_ids")
            if (
                not isinstance(child_ids, list)
                or not MIN_EXPANSION_CHILDREN <= len(child_ids) <= MAX_EXPANSION_CHILDREN
                or not all(isinstance(item, str) and ID_PATTERN.fullmatch(item) for item in child_ids)
                or len(child_ids) != len(set(child_ids))
            ):
                errors.append(
                    f"{parent_id}.expansion.child_atom_ids must contain "
                    f"{MIN_EXPANSION_CHILDREN}-{MAX_EXPANSION_CHILDREN} unique valid IDs"
                )
                child_ids = []
            if (
                not isinstance(base_prerequisites, list)
                or not all(isinstance(item, str) and ID_PATTERN.fullmatch(item) for item in base_prerequisites)
                or len(base_prerequisites) != len(set(base_prerequisites))
            ):
                errors.append(f"{parent_id}.expansion.base_prerequisite_ids must be a unique valid ID list")
                base_prerequisites = []
            if record.get("reason_code") not in EXPANSION_REASON_CODES:
                errors.append(f"{parent_id}.expansion.reason_code is invalid")
            note = record.get("note")
            if not isinstance(note, str) or len(note) > 1000:
                errors.append(f"{parent_id}.expansion.note must be a string of at most 1000 characters")
            for field in ["requested_at", "completed_at"]:
                value = record.get(field)
                if field == "completed_at" and value is None:
                    continue
                try:
                    if not isinstance(value, str) or not value:
                        raise AtomLearnError("timestamp must be a non-empty string")
                    parse_time(value)
                except AtomLearnError as exc:
                    errors.append(f"{parent_id}.expansion.{field}: {exc}")
            if child_ids and child_ids[-1] not in parent.get("prerequisites", []):
                errors.append(f"{parent_id} must depend on the final expanded child {child_ids[-1]}")
            for prerequisite in base_prerequisites:
                if prerequisite not in self.atoms:
                    errors.append(f"{parent_id}.expansion references missing base prerequisite {prerequisite!r}")
                elif self.atoms[prerequisite].get("status") == "archived":
                    errors.append(f"{parent_id}.expansion references archived base prerequisite {prerequisite!r}")
            for index, child_id in enumerate(child_ids):
                child = self.atoms.get(child_id)
                if child is None:
                    errors.append(f"{parent_id}.expansion references missing child {child_id!r}")
                    continue
                previous_parent = claimed_children.setdefault(child_id, parent_id)
                if previous_parent != parent_id:
                    errors.append(f"Expanded child {child_id} is claimed by multiple parents")
                if child.get("parent_atom_id") != parent_id:
                    errors.append(f"Expanded child {child_id} must point to parent {parent_id}")
                if child.get("optional", False) != parent.get("optional", False):
                    errors.append(f"Expanded child {child_id} must inherit optional from parent {parent_id}")
                if child.get("status") == "skipped":
                    errors.append(f"Expanded child {child_id} cannot use a provisional skip")
                expected = base_prerequisites if index == 0 else [child_ids[index - 1]]
                child_expansion = child.get("expansion")
                effective_prerequisites = (
                    child_expansion.get("base_prerequisite_ids")
                    if isinstance(child_expansion, dict)
                    else child.get("prerequisites")
                )
                if not set(expected).issubset(set(effective_prerequisites or [])):
                    errors.append(
                        f"Expanded child {child_id} is missing sequence prerequisites {expected!r}"
                    )
            completed_at = record.get("completed_at")
            mastered_evidence = any(
                item.get("atom_id") == parent_id and item.get("result") == "mastered"
                for item in self.evidence.get("items", [])
            )
            if completed_at is not None and not mastered_evidence:
                errors.append(f"{parent_id}.expansion is complete without mastered integration Evidence")
            if completed_at is None and parent.get("status") in MASTERY_LIKE:
                errors.append(f"{parent_id} is mastered before its detailed expansion is integrated")

        for atom_id, atom in self.atoms.items():
            parent_id = atom.get("parent_atom_id")
            if parent_id is None:
                continue
            if not isinstance(parent_id, str) or not ID_PATTERN.fullmatch(parent_id):
                errors.append(f"{atom_id}.parent_atom_id must be null or a valid Atom ID")
                continue
            parent = self.atoms.get(parent_id)
            if parent is None:
                errors.append(f"{atom_id}.parent_atom_id references missing Atom {parent_id!r}")
                continue
            expansion = parent.get("expansion")
            if not isinstance(expansion, dict) or atom_id not in expansion.get("child_atom_ids", []):
                errors.append(f"{atom_id}.parent_atom_id is not mirrored by parent expansion metadata")

        stack = self.current.get("expansion_stack", [])
        if not isinstance(stack, list):
            errors.append("current.expansion_stack must be a list")
            return
        seen_parents: set[str] = set()
        for index, frame in enumerate(stack):
            if not isinstance(frame, dict) or set(frame) != EXPANSION_FRAME_KEYS:
                errors.append(f"current.expansion_stack[{index}] fields are invalid")
                continue
            parent_id = frame.get("parent_atom_id")
            parent = self.atoms.get(parent_id)
            expansion = parent.get("expansion") if parent else None
            if not isinstance(expansion, dict):
                errors.append(f"current.expansion_stack[{index}] references an Atom without expansion")
                continue
            if parent_id in seen_parents:
                errors.append(f"current.expansion_stack repeats parent {parent_id}")
            seen_parents.add(str(parent_id))
            if frame.get("child_atom_ids") != expansion.get("child_atom_ids"):
                errors.append(f"current.expansion_stack[{index}] child IDs do not match parent expansion")
            depth = frame.get("backtrack_depth")
            if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
                errors.append(f"current.expansion_stack[{index}].backtrack_depth must be a non-negative integer")
            try:
                started_at = frame.get("started_at")
                if not isinstance(started_at, str) or not started_at:
                    raise AtomLearnError("timestamp must be a non-empty string")
                parse_time(started_at)
            except AtomLearnError as exc:
                errors.append(f"current.expansion_stack[{index}].started_at: {exc}")
            if expansion.get("completed_at") is not None:
                errors.append(f"current.expansion_stack[{index}] retains completed expansion {parent_id}")
            if index > 0:
                outer_children = stack[index - 1].get("child_atom_ids", []) if isinstance(stack[index - 1], dict) else []
                if parent_id not in outer_children:
                    errors.append(f"current.expansion_stack[{index}] is not nested under the previous frame")

    def _validate_branches(self, errors: list[str]) -> None:
        for atom_id, atom in self.atoms.items():
            branch = atom.get("branch")
            if branch is None:
                continue
            if not isinstance(branch, dict) or set(branch) != BRANCH_KEYS:
                errors.append(f"{atom_id}.branch fields are invalid")
                continue
            if branch.get("kind") not in BRANCH_KINDS:
                errors.append(f"{atom_id}.branch.kind is invalid")
            anchor_id = branch.get("anchor_atom_id")
            anchor = self.atoms.get(anchor_id)
            if anchor is None or anchor.get("status") == "archived":
                errors.append(f"{atom_id}.branch references missing or archived anchor {anchor_id!r}")
            if atom.get("optional") is not True:
                errors.append(f"{atom_id}.branch must be optional")
            expansion = atom.get("expansion")
            effective_prerequisites = (
                expansion.get("base_prerequisite_ids")
                if isinstance(expansion, dict)
                else atom.get("prerequisites", [])
            )
            if not isinstance(effective_prerequisites, list) or anchor_id not in effective_prerequisites:
                errors.append(f"{atom_id}.branch must depend on anchor {anchor_id!r}")
            origin_question_id = branch.get("origin_question_id")
            question_ids = {
                item.get("id") for item in self.questions.get("items", []) if isinstance(item, dict)
            }
            if not isinstance(origin_question_id, str) or origin_question_id not in question_ids:
                errors.append(f"{atom_id}.branch.origin_question_id must reference an existing Question")
            try:
                created_at = branch.get("created_at")
                if not isinstance(created_at, str) or not created_at:
                    raise AtomLearnError("timestamp must be a non-empty string")
                parse_time(created_at)
            except AtomLearnError as exc:
                errors.append(f"{atom_id}.branch.created_at: {exc}")

    def _validate_course_completion(self, errors: list[str]) -> None:
        course_status = self.course.get("status")
        if course_status not in {"completed", "completed_with_skips"}:
            return
        incomplete = [
            atom["id"] for atom in self.atoms.values()
            if atom.get("status") != "archived"
            and not atom.get("optional", False)
            and atom.get("status") not in SATISFIED_STATUSES
        ]
        blocking = [
            item.get("id") for item in self.questions.get("items", [])
            if item.get("classification") == "blocking_prerequisite" and item.get("status") == "open"
        ]
        if incomplete or blocking:
            errors.append(f"course.status is {course_status} while required work remains")
        required_skips = [
            atom["id"] for atom in self.atoms.values()
            if atom.get("status") == "skipped" and not atom.get("optional", False)
        ]
        if course_status == "completed" and required_skips:
            errors.append("course.status is completed despite required provisional skips")
        if course_status == "completed_with_skips" and not required_skips:
            errors.append("course.status is completed_with_skips but no required Atom is skipped")

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
        expected_expansions = [
            {"parent": atom_id, "children": list(atom["expansion"]["child_atom_ids"])}
            for atom_id, atom in self.atoms.items()
            if isinstance(atom.get("expansion"), dict)
        ]
        if self.graph.get("expansions", []) != expected_expansions:
            errors.append("graph.expansions does not match Atom expansion metadata; run expand or rebuild the graph")
        expected_branches = sorted(
            [
                {"anchor": atom["branch"]["anchor_atom_id"], "atom": atom_id, "kind": atom["branch"]["kind"]}
                for atom_id, atom in self.atoms.items()
                if atom.get("status") != "archived"
                and isinstance(atom.get("branch"), dict)
                and set(atom["branch"]) == BRANCH_KEYS
            ],
            key=lambda item: (item["anchor"], item["atom"]),
        )
        if self.graph.get("branches", []) != expected_branches:
            errors.append("graph.branches does not match Atom branch metadata; run route-concept or rebuild the graph")

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
            routing = item.get("routing")
            if routing is not None:
                if not isinstance(routing, dict) or set(routing) != CONCEPT_ROUTING_KEYS:
                    errors.append(f"{item_id}.routing fields are invalid")
                    continue
                if routing.get("relation") not in CONCEPT_RELATIONS:
                    errors.append(f"{item_id}.routing.relation is invalid")
                if routing.get("action") not in CONCEPT_ACTIONS - {"preview"}:
                    errors.append(f"{item_id}.routing.action is invalid")
                for field in ["concept", "impact"]:
                    if not isinstance(routing.get(field), str) or not routing[field].strip():
                        errors.append(f"{item_id}.routing.{field} must be a non-empty string")
                try:
                    routed_at = routing.get("at")
                    if not isinstance(routed_at, str) or not routed_at:
                        raise AtomLearnError("timestamp must be a non-empty string")
                    parse_time(routed_at)
                except AtomLearnError as exc:
                    errors.append(f"{item_id}.routing.at: {exc}")

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
            if item.get("result") == "pending" and item.get("atom_id") != self.current.get("active_atom_id"):
                errors.append(f"{item_id} is pending for an Atom that is not Active")
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
        self.graph["expansions"] = [
            {"parent": atom_id, "children": list(atom["expansion"]["child_atom_ids"])}
            for atom_id, atom in self.atoms.items()
            if isinstance(atom.get("expansion"), dict)
        ]
        self.graph["branches"] = sorted(
            [
                {"anchor": atom["branch"]["anchor_atom_id"], "atom": atom_id, "kind": atom["branch"]["kind"]}
                for atom_id, atom in self.atoms.items()
                if atom.get("status") != "archived" and isinstance(atom.get("branch"), dict)
            ],
            key=lambda item: (item["anchor"], item["atom"]),
        )

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
                    for key in [
                        "status", "attempts", "confidence", "last_reviewed_at", "evidence_ids", "flexibility",
                        "parent_atom_id", "expansion", "branch", "created_at",
                    ]
                }
                if (
                    existing.get("parent_atom_id") is not None
                    or existing.get("expansion") is not None
                    or existing.get("branch") is not None
                ):
                    progress["prerequisites"] = copy.deepcopy(existing.get("prerequisites", []))
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
                "flexibility": None,
                "parent_atom_id": None,
                "expansion": None,
                "branch": None,
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

    def _expansion_frame(self, parent_id: str, backtrack_depth: int | None = None) -> dict[str, Any]:
        expansion = self.atoms[parent_id]["expansion"]
        return {
            "parent_atom_id": parent_id,
            "child_atom_ids": list(expansion["child_atom_ids"]),
            "started_at": expansion["requested_at"],
            "backtrack_depth": (
                len(self.current.get("backtrack_stack", [])) if backtrack_depth is None else backtrack_depth
            ),
        }

    def _expansion_ancestors(self, atom_id: str) -> list[str]:
        ancestors: list[str] = []
        cursor = self.atoms.get(atom_id)
        seen: set[str] = set()
        while cursor and cursor.get("parent_atom_id") is not None:
            parent_id = cursor["parent_atom_id"]
            if parent_id in seen or parent_id not in self.atoms:
                break
            seen.add(parent_id)
            parent = self.atoms[parent_id]
            expansion = parent.get("expansion")
            if isinstance(expansion, dict) and expansion.get("completed_at") is None:
                ancestors.append(parent_id)
            cursor = parent
        return list(reversed(ancestors))

    def _ensure_expansion_context(self, atom_id: str) -> None:
        desired = self._expansion_ancestors(atom_id)
        atom = self.atoms.get(atom_id)
        if atom and isinstance(atom.get("expansion"), dict) and atom["expansion"].get("completed_at") is None:
            desired.append(atom_id)
        existing = {
            frame.get("parent_atom_id"): frame
            for frame in self.current.get("expansion_stack", [])
            if isinstance(frame, dict)
        }
        self.current["expansion_stack"] = [
            copy.deepcopy(existing[parent_id]) if parent_id in existing else self._expansion_frame(parent_id)
            for parent_id in desired
        ]

    def _expansion_next_atom_id(self) -> str | None:
        stack = self.current.get("expansion_stack", [])
        parent_ids = [stack[-1].get("parent_atom_id")] if stack else [
            atom_id for atom_id, atom in self.atoms.items()
            if isinstance(atom.get("expansion"), dict)
            and atom["expansion"].get("completed_at") is None
            and not (
                atom.get("parent_atom_id") in self.atoms
                and isinstance(self.atoms[atom["parent_atom_id"]].get("expansion"), dict)
                and self.atoms[atom["parent_atom_id"]]["expansion"].get("completed_at") is None
            )
        ]

        def next_within(parent_id: str) -> str | None:
            parent = self.atoms.get(parent_id)
            expansion = parent.get("expansion") if parent else None
            if not isinstance(expansion, dict) or expansion.get("completed_at") is not None:
                return None
            for child_id in expansion.get("child_atom_ids", []):
                child = self.atoms.get(child_id, {})
                if child.get("status") in MASTERY_LIKE:
                    continue
                nested = next_within(child_id)
                return nested or child_id
            return parent_id if parent.get("status") not in MASTERY_LIKE else None

        for parent_id in parent_ids:
            if isinstance(parent_id, str) and (candidate := next_within(parent_id)) is not None:
                return candidate
        return None

    def active_expansions(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for parent_id, parent in self.atoms.items():
            expansion = parent.get("expansion")
            if not isinstance(expansion, dict):
                continue
            child_ids = expansion["child_atom_ids"]
            mastered_children = [
                child_id for child_id in child_ids
                if self.atoms.get(child_id, {}).get("status") in MASTERY_LIKE
            ]
            result.append(
                {
                    "parent_atom_id": parent_id,
                    "parent_title": parent.get("title"),
                    "child_atom_ids": list(child_ids),
                    "mastered_child_atom_ids": mastered_children,
                    "children_mastered": len(mastered_children),
                    "children_total": len(child_ids),
                    "integration_status": (
                        "completed" if expansion.get("completed_at") is not None
                        else "ready" if len(mastered_children) == len(child_ids)
                        else "pending"
                    ),
                    "completed_at": expansion.get("completed_at"),
                }
            )
        return result

    def expand_atom(self, atom_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        atom_id = require_id(atom_id, "atom id")
        parent = self.atoms.get(atom_id)
        if parent is None:
            raise AtomLearnError(f"Unknown expansion parent Atom: {atom_id}")
        if parent.get("status") not in {"available", "active"}:
            raise AtomLearnError(
                f"Atom {atom_id} can be expanded only while available or active, not {parent.get('status')}"
            )
        current_active = self.current.get("active_atom_id")
        if current_active not in {None, atom_id}:
            raise AtomLearnError(f"Finish or defer Active Atom {current_active} before expanding {atom_id}")
        if parent.get("expansion") is not None:
            raise AtomLearnError(f"Atom {atom_id} already has a detailed expansion")
        if parent.get("status") == "active" and self.current.get("phase") == "reviewing":
            raise AtomLearnError("A mastered review Atom cannot be expanded without an explicit reopen workflow")
        pending_evidence = [
            item.get("id") for item in self.evidence.get("items", [])
            if item.get("atom_id") == atom_id and item.get("result") == "pending"
        ]
        if pending_evidence:
            raise AtomLearnError(
                f"Assess or replace pending Evidence before expansion: {', '.join(str(item) for item in pending_evidence)}"
            )
        if not isinstance(plan, dict):
            raise AtomLearnError("expansion plan must be a mapping")
        reason_code = plan.get("reason_code", "learner_requested_detail")
        if reason_code not in EXPANSION_REASON_CODES:
            raise AtomLearnError("reason_code must be one of: " + ", ".join(sorted(EXPANSION_REASON_CODES)))
        note = plan.get("note", "")
        if not isinstance(note, str) or len(note.strip()) > 1000:
            raise AtomLearnError("expansion note must be at most 1000 characters")
        candidates = plan.get("child_atoms")
        if not isinstance(candidates, list) or not MIN_EXPANSION_CHILDREN <= len(candidates) <= MAX_EXPANSION_CHILDREN:
            raise AtomLearnError(
                f"Detailed expansion requires {MIN_EXPANSION_CHILDREN}-{MAX_EXPANSION_CHILDREN} child_atoms"
            )
        child_ids: list[str] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise AtomLearnError(f"child_atoms[{index}] must be a mapping")
            if "prerequisites" in candidate and candidate.get("prerequisites") not in (None, []):
                raise AtomLearnError("expand computes child prerequisites from list order; omit prerequisites")
            child_id = require_id(candidate.get("id"), f"child_atoms[{index}].id")
            require_string(candidate.get("title"), f"{child_id}.title")
            require_string(candidate.get("objective"), f"{child_id}.objective")
            if child_id in self.atoms:
                raise AtomLearnError(f"Expanded child Atom already exists: {child_id}")
            child_ids.append(child_id)
        if len(child_ids) != len(set(child_ids)):
            raise AtomLearnError("Expanded child Atom IDs must be unique")

        self._ensure_expansion_context(atom_id)
        base_prerequisites = list(parent.get("prerequisites", []))
        timestamp = iso()
        normalized_candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            normalized = copy.deepcopy(candidate)
            normalized["module"] = parent.get("module", "Uncategorized")
            normalized["optional"] = bool(parent.get("optional", False))
            normalized["prerequisites"] = base_prerequisites if index == 0 else [child_ids[index - 1]]
            normalized_candidates.append(normalized)
        self._add_restructure_atoms(normalized_candidates, inherited_sources=parent.get("sources", []))
        for child_id in child_ids:
            self.atoms[child_id]["parent_atom_id"] = atom_id
        parent["prerequisites"] = [child_ids[-1]]
        parent["expansion"] = {
            "child_atom_ids": child_ids,
            "base_prerequisite_ids": base_prerequisites,
            "reason_code": reason_code,
            "note": note.strip(),
            "requested_at": timestamp,
            "completed_at": None,
        }
        parent["status"] = "locked"
        self.current.setdefault("expansion_stack", []).append(self._expansion_frame(atom_id))
        first = self.atoms[child_ids[0]]
        first["status"] = "active"
        self.current["active_atom_id"] = first["id"]
        self.current["phase"] = "teaching"
        self.current["current_question"] = None
        self.current["learner_confusions"] = []
        self.current["next_action"] = (
            f"Teach only expanded Atom 1/{len(child_ids)}: {first['title']}. "
            f"Do not preview later children."
        )
        self.rebuild_graph()
        return {
            "parent_atom_id": atom_id,
            "created_atom_ids": child_ids,
            "active_atom_id": first["id"],
            "integration_required": True,
            "reason_code": reason_code,
        }

    def _activate_expansion_atom(self, atom_id: str, phase: str, next_action: str) -> None:
        atom = self.atoms.get(atom_id)
        if atom is None or atom.get("status") != "available":
            status = atom.get("status") if atom else "missing"
            raise AtomLearnError(f"Expansion cannot activate {atom_id} from status {status}")
        atom["status"] = "active"
        self.current["active_atom_id"] = atom_id
        self.current["phase"] = phase
        self.current["current_question"] = None
        self.current["learner_confusions"] = []
        self.current["next_action"] = next_action

    def _advance_expansion_after_mastery(self, atom_id: str, at: datetime) -> None:
        completed_id = atom_id
        stack = self.current.get("expansion_stack", [])
        while stack:
            frame = stack[-1]
            if len(self.current.get("backtrack_stack", [])) > frame.get("backtrack_depth", 0):
                self.current["next_action"] = "Resume the saved Atom after prerequisite remediation."
                return
            parent_id = frame["parent_atom_id"]
            child_ids = frame["child_atom_ids"]
            parent = self.atoms[parent_id]
            if completed_id == parent_id:
                parent["expansion"]["completed_at"] = iso(at)
                stack.pop()
                completed_id = parent_id
                self.recalculate_availability()
                continue
            if completed_id not in child_ids:
                return
            self.recalculate_availability()
            remaining = [
                child_id for child_id in child_ids
                if self.atoms[child_id].get("status") not in MASTERY_LIKE
            ]
            if remaining:
                next_id = remaining[0]
                next_atom = self.atoms[next_id]
                if next_atom.get("status") == "deferred":
                    self.current["next_action"] = (
                        f"Restore deferred expanded Atom {next_id} before continuing the detailed branch."
                    )
                    return
                position = child_ids.index(next_id) + 1
                self._activate_expansion_atom(
                    next_id,
                    "teaching",
                    f"Teach only expanded Atom {position}/{len(child_ids)}: {next_atom['title']}. "
                    "Do not preview later children.",
                )
                return
            self._activate_expansion_atom(
                parent_id,
                "integrating",
                f"Run the integration check for {parent['title']} across all mastered child Atoms.",
            )
            return
        if self.current.get("backtrack_stack"):
            self.current["next_action"] = "Resume the saved Atom after prerequisite remediation."

    def status_summary(self) -> dict[str, Any]:
        active_id = self.current.get("active_atom_id")
        active = self.atoms.get(active_id) if active_id else None
        validation_errors = self.validate()
        counts: dict[str, int] = defaultdict(int)
        for atom in self.atoms.values():
            counts[atom.get("status", "unknown")] += 1
        open_questions = [item for item in self.questions.get("items", []) if item.get("status") in {"open", "parked"}]
        due_reviews = [item for item in self.reviews.get("items", []) if item.get("status") == "pending" and self.atoms.get(item.get("atom_id"), {}).get("status") == "review_due"]
        flexibility = [
            {
                "atom_id": atom["id"],
                "title": atom.get("title"),
                "status": atom.get("status"),
                **copy.deepcopy(atom.get("flexibility", {})),
            }
            for atom in self.atoms.values()
            if atom.get("status") in {"skipped", "deferred"}
        ]
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
            "active_flexibility_decisions": flexibility,
            "detailed_expansions": self.active_expansions(),
            "optional_branches": [
                {
                    "anchor_atom_id": atom["branch"]["anchor_atom_id"],
                    "atom_id": atom_id,
                    "title": atom.get("title"),
                    "status": atom.get("status"),
                }
                for atom_id, atom in self.atoms.items()
                if atom.get("status") != "archived" and isinstance(atom.get("branch"), dict)
            ],
            "expansion_focus_atom_id": self._expansion_next_atom_id(),
            "next_candidates": [] if validation_errors else self.suggest_next(),
        }

    def suggest_next(self) -> list[dict[str, Any]]:
        due_ids = {
            item.get("atom_id")
            for item in self.reviews.get("items", [])
            if item.get("status") == "pending" and self.atoms.get(item.get("atom_id"), {}).get("status") == "review_due"
        }
        candidates = [atom for atom in self.atoms.values() if atom.get("status") in {"available", "review_due"}]
        expansion_focus = self._expansion_next_atom_id()
        candidates.sort(
            key=lambda atom: (
                0 if atom["id"] == expansion_focus else 1,
                0 if atom["id"] in due_ids else 1,
                1 if atom.get("optional", False) else 0,
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
        expansion = atom.get("expansion")
        integrating = (
            isinstance(expansion, dict)
            and expansion.get("completed_at") is None
            and all(
                self.atoms.get(child_id, {}).get("status") in MASTERY_LIKE
                for child_id in expansion.get("child_atom_ids", [])
            )
        )
        self._ensure_expansion_context(atom_id)
        atom["status"] = "active"
        self.current["active_atom_id"] = atom_id
        self.current["phase"] = "reviewing" if reviewing else ("integrating" if integrating else "teaching")
        self.current["current_question"] = None
        self.current["learner_confusions"] = []
        self.current["next_action"] = (
            "Run a focused review check."
            if reviewing
            else f"Run the integration check for {atom['title']} across all mastered child Atoms."
            if integrating
            else f"Teach why {atom['title']} matters."
        )
        self.course["status"] = "active"

    def skip_guidance(self, atom_id: str) -> dict[str, Any]:
        atom_id = require_id(atom_id, "atom id")
        atom = self.atoms.get(atom_id)
        if not atom:
            raise AtomLearnError(f"Unknown Atom: {atom_id}")
        if atom.get("status") == "archived":
            raise AtomLearnError(f"Archived Atom {atom_id} cannot be skipped")
        status = atom.get("status")
        if status in MASTERY_LIKE:
            recommendation = "No skip is needed because this Atom already has mastered Evidence."
        elif status == "locked":
            recommendation = (
                "This Atom is locked. Repair its prerequisites before a recorded diagnostic, or explicitly confirm a provisional skip."
            )
        elif status in {"skipped", "deferred"}:
            recommendation = "Run unskip before taking a diagnostic or changing the flexibility decision."
        else:
            recommendation = "Run a short diagnostic covering every required mastery dimension before skipping instruction."
        return {
            "mode": "diagnostic",
            "mutated": False,
            "course_revision": self.revision,
            "atom": {
                "id": atom_id,
                "title": atom.get("title"),
                "objective": atom.get("objective"),
                "status": status,
                "required_dimensions": list(atom.get("mastery", {}).get("required_dimensions", [])),
                "pass_threshold": atom.get("mastery", {}).get("pass_threshold"),
                "minimum_dimension_score": atom.get("mastery", {}).get("minimum_dimension_score"),
                "misconceptions": list(atom.get("misconceptions", [])),
            },
            "can_activate_for_diagnostic": (
                status in {"available", "active", "review_due"}
                and (self.current.get("active_atom_id") in {None, atom_id})
            ),
            "recommendation": recommendation,
        }

    def skip_atom(
        self,
        atom_id: str,
        mode: str,
        reason_code: str | None,
        note: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        atom_id = require_id(atom_id, "atom id")
        if mode not in {"provisional", "defer"}:
            raise AtomLearnError("Mutating skip mode must be provisional or defer")
        atom = self.atoms.get(atom_id)
        if not atom:
            raise AtomLearnError(f"Unknown Atom: {atom_id}")
        status = atom.get("status")
        if status == "archived":
            raise AtomLearnError(f"Archived Atom {atom_id} cannot be skipped")
        if status in MASTERY_LIKE:
            raise AtomLearnError(f"Atom {atom_id} already has mastered Evidence and does not need skipping")
        if status == "skipped":
            raise AtomLearnError(f"Atom {atom_id} is already provisionally skipped")
        if mode == "defer" and status == "deferred":
            raise AtomLearnError(f"Atom {atom_id} is already deferred")
        if status == "active" and self.current.get("phase") == "reviewing":
            raise AtomLearnError(
                f"Active review Atom {atom_id} already has mastered Evidence and cannot be skipped"
            )
        if reason_code not in SKIP_REASON_CODES:
            raise AtomLearnError("reason-code must be one of: " + ", ".join(sorted(SKIP_REASON_CODES)))
        if not isinstance(note, str) or len(note.strip()) > 1000:
            raise AtomLearnError("skip note must be at most 1000 characters")
        policy = self.course.get("settings", {}).get("skip_policy", "diagnostic_first")
        if mode == "provisional" and policy == "strict_mastery":
            raise AtomLearnError("This course uses strict_mastery skip policy; use a diagnostic or defer the Atom")
        if mode == "provisional" and atom.get("parent_atom_id") is not None:
            raise AtomLearnError(
                "Expanded child Atoms require mastered Evidence; use a diagnostic test-out or defer instead"
            )
        expansion = atom.get("expansion")
        if mode == "provisional" and isinstance(expansion, dict) and expansion.get("completed_at") is None:
            raise AtomLearnError("An expanded parent requires child mastery and an integration check before completion")
        if mode == "provisional" and not confirmed:
            raise AtomLearnError(
                "Provisional skip does not prove mastery. Review the diagnostic option and rerun with --confirmed."
            )
        if status == "active":
            if self.current.get("active_atom_id") != atom_id:
                raise AtomLearnError("Active Atom status and current session disagree")
            self.current["active_atom_id"] = None
            self.current["phase"] = "transitioning"
            self.current["current_question"] = None
            self.current["learner_confusions"] = []
            if mode == "defer":
                self.current["expansion_stack"] = []
        timestamp = iso()
        atom["status"] = "skipped" if mode == "provisional" else "deferred"
        atom["flexibility"] = {
            "mode": mode,
            "reason_code": reason_code,
            "note": note.strip(),
            "diagnostic_offered": mode == "provisional",
            "confirmed": bool(confirmed) if mode == "provisional" else False,
            "created_at": timestamp,
            "revoked_at": None,
        }
        if self.current.get("active_atom_id") is None:
            if self.current.get("backtrack_stack"):
                self.current["next_action"] = (
                    "Resume the saved parent after the prerequisite is satisfied."
                    if mode == "provisional"
                    else "The remedial Atom is deferred; restore it or choose another prerequisite before resuming."
                )
            else:
                self.current["next_action"] = "Review the flexibility decision and choose the next available Atom."
        return {
            "atom_id": atom_id,
            "mode": mode,
            "status": atom["status"],
            "reason_code": reason_code,
            "mastery_claimed": False,
            "reversible": True,
        }

    def unskip_atom(self, atom_id: str) -> dict[str, Any]:
        atom_id = require_id(atom_id, "atom id")
        atom = self.atoms.get(atom_id)
        if not atom:
            raise AtomLearnError(f"Unknown Atom: {atom_id}")
        status = atom.get("status")
        if status not in {"skipped", "deferred"}:
            raise AtomLearnError(f"Atom {atom_id} is not skipped or deferred")
        active_id = self.current.get("active_atom_id")
        if active_id and atom_id in self.atoms.get(active_id, {}).get("prerequisites", []):
            raise AtomLearnError(
                f"Cannot restore {atom_id} while dependent Atom {active_id} is active; finish or leave the Active Atom first"
            )
        record = atom.get("flexibility")
        if not isinstance(record, dict) or record.get("revoked_at") is not None:
            raise AtomLearnError(f"Atom {atom_id} has no active flexibility record")
        previous_mode = record.get("mode")
        record["revoked_at"] = iso()
        atom["status"] = "locked"
        self.recalculate_availability()
        return {
            "atom_id": atom_id,
            "restored_from": previous_mode,
            "status": atom["status"],
            "mastery_claimed": False,
        }

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

    def _depends_on(self, atom_id: str, prerequisite_id: str) -> bool:
        """Return whether atom_id transitively depends on prerequisite_id."""
        pending = list(self.atoms.get(atom_id, {}).get("prerequisites", []))
        seen: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate == prerequisite_id:
                return True
            if candidate in seen:
                continue
            seen.add(candidate)
            pending.extend(self.atoms.get(candidate, {}).get("prerequisites", []))
        return False

    def _normalize_concept_route(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AtomLearnError("concept route must be a mapping")
        active_id = self.current.get("active_atom_id")
        if active_id not in self.atoms:
            raise AtomLearnError("Concept routing requires an Active Atom")
        relation = payload.get("relation")
        if relation not in CONCEPT_RELATIONS:
            raise AtomLearnError("relation must be one of: " + ", ".join(sorted(CONCEPT_RELATIONS)))
        normalized = {
            "text": require_string(payload.get("text"), "concept route.text"),
            "concept": require_string(payload.get("concept"), "concept route.concept"),
            "relation": relation,
            "rationale": require_string(payload.get("rationale"), "concept route.rationale"),
            "related_atom_id": payload.get("related_atom_id"),
            "new_atom": copy.deepcopy(payload.get("new_atom")),
            "active_atom_id": active_id,
        }
        related_id = normalized["related_atom_id"]
        new_atom = normalized["new_atom"]
        if related_id is not None:
            related_id = require_id(related_id, "related_atom_id")
            normalized["related_atom_id"] = related_id
            related = self.atoms.get(related_id)
            if related is None or related.get("status") == "archived":
                raise AtomLearnError(f"related_atom_id must name a non-archived Atom: {related_id}")
            if related_id == active_id:
                raise AtomLearnError("Use inside_current when the related concept is the Active Atom")
        if new_atom is not None:
            if not isinstance(new_atom, dict):
                raise AtomLearnError("new_atom must be a mapping")
            if "prerequisites" in new_atom and new_atom.get("prerequisites") not in (None, []):
                raise AtomLearnError("route-concept computes new_atom prerequisites; omit prerequisites")
            new_id = require_id(new_atom.get("id"), "new_atom.id")
            require_string(new_atom.get("title"), f"{new_id}.title")
            require_string(new_atom.get("objective"), f"{new_id}.objective")
            if new_id in self.atoms:
                raise AtomLearnError(f"new_atom already exists: {new_id}")
        has_related = related_id is not None
        has_new = new_atom is not None
        if relation in {"inside_current", "out_of_scope"} and (has_related or has_new):
            raise AtomLearnError(f"{relation} must not include related_atom_id or new_atom")
        if relation == "scheduled_successor" and (not has_related or has_new):
            raise AtomLearnError("scheduled_successor requires related_atom_id and must not include new_atom")
        if relation in {"required_prerequisite", "optional_extension"} and has_related == has_new:
            raise AtomLearnError(f"{relation} requires exactly one of related_atom_id or new_atom")
        if relation == "required_prerequisite" and has_related:
            if self.atoms[related_id].get("optional", False):
                raise AtomLearnError(
                    f"Optional Atom {related_id} cannot silently become required; "
                    "propose a new required Atom or restructure it explicitly"
                )
            if self._depends_on(related_id, active_id):
                raise AtomLearnError(
                    f"{related_id} is downstream of {active_id}; adding it as a prerequisite would create a cycle"
                )
        if relation == "optional_extension" and has_related:
            if not self.atoms[related_id].get("optional", False):
                raise AtomLearnError(f"Optional extension Atom {related_id} must already be optional")
        if relation == "scheduled_successor" and self.atoms[related_id].get("optional", False):
            raise AtomLearnError(f"Use optional_extension for optional scheduled Atom {related_id}")
        return normalized

    def concept_route_guidance(self, payload: dict[str, Any]) -> dict[str, Any]:
        route = self._normalize_concept_route(payload)
        relation = route["relation"]
        labels = {
            "inside_current": "Inside the current Atom",
            "required_prerequisite": "Required prerequisite",
            "scheduled_successor": "Scheduled later",
            "optional_extension": "Optional extension",
            "out_of_scope": "Outside the current goal",
        }
        impacts = {
            "inside_current": "No path change; answer one focused boundary inside the Active Atom.",
            "required_prerequisite": "Blocks current understanding; learn or diagnose it, then resume automatically.",
            "scheduled_successor": "Does not block the current Atom; keep it parked until its planned turn.",
            "optional_extension": "Does not block completion; add it only if the learner chooses the branch.",
            "out_of_scope": "Does not affect the current course unless the learner later changes the goal.",
        }
        contracts = {
            "inside_current": "Explain only the asked boundary; do not introduce a chain of new concepts.",
            "required_prerequisite": "State that it blocks progress, repair one prerequisite Atom, then reconnect and resume.",
            "scheduled_successor": "Name the planned destination and timing; do not teach its mechanism now.",
            "optional_extension": "Give at most a definition and its relation; do not add the branch without confirmation.",
            "out_of_scope": "State the scope boundary and offer park or dismiss without expanding the course.",
        }
        recommended = {
            "inside_current": "explain_now",
            "required_prerequisite": "learn_prerequisite",
            "scheduled_successor": "park",
            "optional_extension": "brief_context",
            "out_of_scope": "park",
        }[relation]
        action_matrix: dict[str, list[tuple[str, str, bool]]] = {
            "inside_current": [("explain_now", "Explain this boundary now", False)],
            "required_prerequisite": [
                ("learn_prerequisite", "Insert and learn the prerequisite", True),
                ("diagnose_prerequisite", "Run a quick prerequisite diagnostic", True),
            ],
            "scheduled_successor": [
                ("park", "Keep it for its planned Atom", False),
                ("brief_context", "Give definition-only context", False),
            ],
            "optional_extension": [
                ("brief_context", "Give definition-only context", False),
                ("add_optional_branch", "Add an optional side branch", True),
                ("park", "Save it for later", False),
                ("dismiss", "Dismiss it", False),
            ],
            "out_of_scope": [
                ("park", "Save it outside the current path", False),
                ("dismiss", "Dismiss it", False),
            ],
        }
        if (
            relation == "optional_extension"
            and route["related_atom_id"] is not None
            and isinstance(self.atoms[route["related_atom_id"]].get("branch"), dict)
        ):
            action_matrix["optional_extension"] = [
                item for item in action_matrix["optional_extension"]
                if item[0] != "add_optional_branch"
            ]
        destination: dict[str, Any] | None = None
        related_id = route["related_atom_id"]
        if related_id:
            related = self.atoms[related_id]
            destination = {
                "atom_id": related_id,
                "title": related.get("title"),
                "module": related.get("module"),
                "status": related.get("status"),
                "prerequisite_ids": list(related.get("prerequisites", [])),
                "new": False,
            }
        elif route["new_atom"]:
            proposed_prerequisites = (
                list(self.atoms[route["active_atom_id"]].get("prerequisites", []))
                if relation == "required_prerequisite"
                else [route["active_atom_id"]]
                if relation == "optional_extension"
                else []
            )
            destination = {
                "atom_id": route["new_atom"]["id"],
                "title": route["new_atom"]["title"],
                "module": route["new_atom"].get("module", self.atoms[route["active_atom_id"]].get("module")),
                "status": "proposed",
                "prerequisite_ids": proposed_prerequisites,
                "new": True,
            }
        choices = [
            {
                "action": action,
                "label": label,
                "recommended": action == recommended,
                "requires_confirmation": requires_confirmation,
            }
            for action, label, requires_confirmation in action_matrix[relation]
        ]
        return {
            "mutated": False,
            "active_atom_id": route["active_atom_id"],
            "card": {
                "concept": route["concept"],
                "relation": relation,
                "label": labels[relation],
                "why": route["rationale"],
                "blocking": relation == "required_prerequisite",
                "impact": impacts[relation],
                "destination": destination,
                "recommended_action": recommended,
                "choices": choices,
                "response_contract": contracts[relation],
            },
        }

    def _record_routed_question(
        self,
        route: dict[str, Any],
        action: str,
        impact: str,
        related_atom_id: str | None = None,
    ) -> str:
        classification = {
            "inside_current": "in_atom",
            "required_prerequisite": "blocking_prerequisite",
            "scheduled_successor": "future_atom",
            "optional_extension": "optional_extension",
            "out_of_scope": "out_of_scope",
        }[route["relation"]]
        question_id = self.record_question(
            {
                "text": route["text"],
                "classification": classification,
                "related_atom_id": related_atom_id,
                "rationale": route["rationale"],
                "priority": "high" if route["relation"] == "required_prerequisite" else "normal",
            }
        )
        question = self.find_record(self.questions["items"], question_id, "Question")
        question["routing"] = {
            "concept": route["concept"],
            "relation": route["relation"],
            "action": action,
            "impact": impact,
            "at": iso(),
        }
        return question_id

    def apply_concept_route(self, payload: dict[str, Any], action: str, confirmed: bool) -> dict[str, Any]:
        if action not in CONCEPT_ACTIONS - {"preview"}:
            raise AtomLearnError("action must be one of: " + ", ".join(sorted(CONCEPT_ACTIONS - {"preview"})))
        guidance = self.concept_route_guidance(payload)
        route = self._normalize_concept_route(payload)
        allowed = {choice["action"] for choice in guidance["card"]["choices"]}
        if action not in allowed:
            raise AtomLearnError(f"Action {action} is not valid for relation {route['relation']}")
        requires_confirmation = next(
            choice["requires_confirmation"] for choice in guidance["card"]["choices"]
            if choice["action"] == action
        )
        if requires_confirmation and not confirmed:
            raise AtomLearnError(f"Action {action} changes the learning path; rerun with --confirmed")

        related_id = route["related_atom_id"]
        created_atom_id: str | None = None
        if action in {"learn_prerequisite", "diagnose_prerequisite"}:
            parent_id = route["active_atom_id"]
            parent = self.atoms[parent_id]
            if related_id is None:
                candidate = copy.deepcopy(route["new_atom"])
                candidate["module"] = candidate.get("module", parent.get("module", "Uncategorized"))
                candidate["optional"] = False
                candidate["prerequisites"] = list(parent.get("prerequisites", []))
                self._add_restructure_atoms([candidate], inherited_sources=parent.get("sources", []))
                related_id = candidate["id"]
                created_atom_id = related_id
            target = self.atoms[related_id]
            if target.get("status") not in {"available", "mastered", "review_due", "skipped", "deferred", "locked"}:
                raise AtomLearnError(f"Prerequisite target {related_id} cannot be opened from {target.get('status')}")
            parent["prerequisites"] = unique(list(parent.get("prerequisites", [])) + [related_id])
            self.rebuild_graph()
            self.recalculate_availability()
            if target.get("status") == "locked":
                unmet = [
                    item for item in target.get("prerequisites", [])
                    if self.atoms.get(item, {}).get("status") not in SATISFIED_STATUSES
                ]
                raise AtomLearnError(
                    f"Prerequisite {related_id} is locked; repair its prerequisites first: {', '.join(unmet)}"
                )
            question_id = self._record_routed_question(
                route, action, guidance["card"]["impact"], related_id
            )
            self.backtrack(related_id, question_id)
            if action == "diagnose_prerequisite":
                self.current["phase"] = "checking"
                self.current["next_action"] = (
                    f"Run a focused diagnostic for {target['title']}; remediate only if needed, then resume {parent['title']}."
                )
        elif action == "add_optional_branch":
            anchor_id = route["active_atom_id"]
            anchor = self.atoms[anchor_id]
            if related_id is not None and self._depends_on(anchor_id, related_id):
                raise AtomLearnError(
                    f"{anchor_id} already depends on {related_id}; making it a branch would create a cycle"
                )
            question_id = self._record_routed_question(
                route, action, guidance["card"]["impact"], related_id
            )
            if related_id is None:
                candidate = copy.deepcopy(route["new_atom"])
                candidate["module"] = candidate.get("module", anchor.get("module", "Uncategorized"))
                candidate["optional"] = True
                candidate["prerequisites"] = [anchor_id]
                self._add_restructure_atoms([candidate], inherited_sources=anchor.get("sources", []))
                related_id = candidate["id"]
                created_atom_id = related_id
            branch_atom = self.atoms[related_id]
            if branch_atom.get("parent_atom_id") is not None or branch_atom.get("expansion") is not None:
                raise AtomLearnError("Attach an optional branch before using detailed expansion on that Atom")
            existing_branch = branch_atom.get("branch")
            if existing_branch is not None:
                raise AtomLearnError(
                    f"Atom {related_id} is already an optional branch anchored to "
                    f"{existing_branch.get('anchor_atom_id')}"
                )
            branch_atom["optional"] = True
            branch_atom["prerequisites"] = unique(list(branch_atom.get("prerequisites", [])) + [anchor_id])
            branch_atom["branch"] = {
                "kind": "optional_extension",
                "anchor_atom_id": anchor_id,
                "origin_question_id": question_id,
                "created_at": iso(),
            }
            question = self.find_record(self.questions["items"], question_id, "Question")
            question["related_atom_id"] = related_id
            self.rebuild_graph()
            self.recalculate_availability()
        else:
            question_id = self._record_routed_question(
                route, action, guidance["card"]["impact"], related_id
            )
            if action == "dismiss":
                self.resolve_question(question_id, "Learner chose not to pursue this related concept.", dismissed=True)

        result = {
            "mutated": True,
            "action": action,
            "question_id": question_id,
            "active_atom_id": self.current.get("active_atom_id"),
            "related_atom_id": related_id,
            "created_atom_id": created_atom_id,
            "card": guidance["card"],
        }
        if action == "brief_context":
            result["response_limit"] = "definition_and_relation_only"
        return result

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
        status = "parked" if classification in {"non_blocking", "future_atom", "optional_extension", "out_of_scope"} else "open"
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
        active_atom_id = self.current.get("active_atom_id")
        atom_id = payload.get("atom_id") or active_atom_id
        if atom_id not in self.atoms:
            raise AtomLearnError(f"Unknown Evidence Atom: {atom_id!r}")
        if atom_id != active_atom_id or self.atoms[atom_id].get("status") != "active":
            raise AtomLearnError(
                f"Evidence can be recorded only for the Active Atom; "
                f"requested {atom_id!r}, Active Atom is {active_atom_id!r}"
            )
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
            self._advance_expansion_after_mastery(atom_id, at or now_utc())
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
        if target.get("status") not in {"available", "mastered", "review_due", "skipped", "deferred"}:
            raise AtomLearnError(f"Backtrack target {target_id} is not available for remediation")
        if target.get("status") in {"skipped", "deferred"}:
            unmet_target_prerequisites = [
                prerequisite
                for prerequisite in target.get("prerequisites", [])
                if self.atoms.get(prerequisite, {}).get("status") not in SATISFIED_STATUSES
            ]
            if unmet_target_prerequisites:
                raise AtomLearnError(
                    f"Cannot reopen {target_id}; repair its prerequisites first: {', '.join(unmet_target_prerequisites)}"
                )
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
        flexibility = target.get("flexibility")
        if isinstance(flexibility, dict) and flexibility.get("revoked_at") is None:
            flexibility["revoked_at"] = iso()
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
            if self.atoms.get(prereq, {}).get("status") not in SATISFIED_STATUSES
        ]
        if unsatisfied:
            raise AtomLearnError(f"Cannot resume; prerequisites remain unmastered: {', '.join(unsatisfied)}")
        stack.pop()
        parent["status"] = "active"
        self.current["active_atom_id"] = parent_id
        self._ensure_expansion_context(parent_id)
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

    def _participates_in_optional_branch(self, atom_id: str) -> bool:
        return isinstance(self.atoms.get(atom_id, {}).get("branch"), dict) or any(
            isinstance(atom.get("branch"), dict) and atom["branch"].get("anchor_atom_id") == atom_id
            for atom in self.atoms.values()
        )

    def _split(self, proposal: dict[str, Any]) -> dict[str, Any]:
        source_id = proposal.get("source_atom_id")
        source = self.atoms.get(source_id)
        if not source:
            raise AtomLearnError(f"Unknown split source Atom: {source_id!r}")
        if source.get("status") == "active":
            raise AtomLearnError("Cannot split the Active Atom")
        if source.get("parent_atom_id") is not None or source.get("expansion") is not None:
            raise AtomLearnError("Cannot split an Atom that participates in a detailed expansion")
        if self._participates_in_optional_branch(source_id):
            raise AtomLearnError("Cannot split an Atom that participates in an optional branch")
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
        if any(
            self.atoms[atom_id].get("parent_atom_id") is not None
            or self.atoms[atom_id].get("expansion") is not None
            for atom_id in source_ids
        ):
            raise AtomLearnError("Cannot merge Atoms that participate in a detailed expansion")
        if any(self._participates_in_optional_branch(atom_id) for atom_id in source_ids):
            raise AtomLearnError("Cannot merge Atoms that participate in an optional branch")
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
                "flexibility": None,
                "parent_atom_id": None,
                "expansion": None,
                "branch": None,
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
            "skipped": "⇥",
            "deferred": "⏸",
            "archived": "—",
        }
        modules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for atom in self.atoms.values():
            modules[atom.get("module", "Uncategorized")].append(atom)
        map_lines = [f"# {self.course.get('title')} Learning Map", "", "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.", ""]
        for module, atoms in modules.items():
            map_lines.extend([f"## {module}", ""])
            atom_ids = {atom["id"] for atom in atoms}
            children: dict[str, list[dict[str, Any]]] = defaultdict(list)
            branch_children: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for atom in atoms:
                if atom.get("parent_atom_id") in atom_ids:
                    children[atom["parent_atom_id"]].append(atom)
                branch = atom.get("branch")
                if isinstance(branch, dict) and branch.get("anchor_atom_id") in atom_ids:
                    branch_children[branch["anchor_atom_id"]].append(atom)
            rendered_ids: set[str] = set()

            def append_tree(atom: dict[str, Any], depth: int) -> None:
                rendered_ids.add(atom["id"])
                indent = "  " * depth
                marker = " ↳" if depth else ""
                branch_label = " [optional branch]" if isinstance(atom.get("branch"), dict) else ""
                map_lines.append(
                    f"{indent}-{marker} {status_icon.get(atom.get('status'), '?')} "
                    f"`{atom['id']}` — {atom['title']} ({atom.get('status')}){branch_label}"
                )
                ordered_children = (
                    atom.get("expansion", {}).get("child_atom_ids", [])
                    if isinstance(atom.get("expansion"), dict)
                    else []
                )
                child_map = {item["id"]: item for item in children.get(atom["id"], [])}
                for child_id in ordered_children:
                    if child_id in child_map:
                        append_tree(child_map[child_id], depth + 1)
                for child in sorted(branch_children.get(atom["id"], []), key=lambda item: item["id"]):
                    append_tree(child, depth + 1)

            for atom in atoms:
                branch = atom.get("branch")
                branch_anchor = branch.get("anchor_atom_id") if isinstance(branch, dict) else None
                if atom.get("parent_atom_id") not in atom_ids and branch_anchor not in atom_ids:
                    append_tree(atom, 0)
            for atom in atoms:
                if atom["id"] not in rendered_ids:
                    append_tree(atom, 0)
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
        current_lines.extend(
            [
                "", "## Next Action", "", str(self.current.get("next_action") or "None"),
                "", "## Backtrack Depth", "", str(len(self.current.get("backtrack_stack", []))),
                "", "## Detailed Expansion Depth", "", str(len(self.current.get("expansion_stack", []))), "",
            ]
        )

        non_archived = [atom for atom in self.atoms.values() if atom.get("status") != "archived"]
        required_atoms = [atom for atom in non_archived if not atom.get("optional", False)]
        optional_atoms = [atom for atom in non_archived if atom.get("optional", False)]
        mastered = [atom for atom in required_atoms if atom.get("status") in MASTERY_LIKE]
        skipped = [atom for atom in required_atoms if atom.get("status") == "skipped"]
        deferred = [atom for atom in required_atoms if atom.get("status") == "deferred"]
        mastery_percent = (100 * len(mastered) / len(required_atoms)) if required_atoms else 0
        path_percent = (100 * (len(mastered) + len(skipped)) / len(required_atoms)) if required_atoms else 0
        progress_lines = [
            "# Learning Progress",
            "",
            "> Generated by AtomLearn. Edit canonical `.atomlearn/` state through the CLI.",
            "",
            "## Overall",
            "",
            f"- Mastered with Evidence: {len(mastered)} / {len(required_atoms)} ({mastery_percent:.1f}%)",
            f"- Provisionally skipped: {len(skipped)}",
            f"- Deferred: {len(deferred)}",
            f"- Path satisfied: {len(mastered) + len(skipped)} / {len(required_atoms)} ({path_percent:.1f}%)",
            f"- Optional Atoms: {len(optional_atoms)}",
            "",
            "## Current",
            "",
            f"`{active_id}` — {active['title']}" if active else "None",
            "",
            "## Available Next",
            "",
        ]
        progress_lines.extend([f"- `{item['id']}` — {item['title']} ({item['status']})" for item in self.suggest_next()] or ["- None"])
        progress_lines.extend(["", "## Flexible Decisions", ""])
        progress_lines.extend(
            [
                f"- `{atom['id']}` — {atom['title']} ({atom['status']}; "
                f"reason: {atom.get('flexibility', {}).get('reason_code', 'unknown')})"
                for atom in skipped + deferred
            ]
            or ["- None"]
        )
        progress_lines.extend(["", "## Optional Branches", ""])
        progress_lines.extend(
            [
                f"- `{atom['id']}` — {atom['title']} ({atom['status']}; "
                f"anchor: `{atom.get('branch', {}).get('anchor_atom_id', 'unanchored')}`)"
                for atom in optional_atoms
                if isinstance(atom.get("branch"), dict)
            ]
            or ["- None"]
        )
        progress_lines.extend(["", "## Detailed Expansions", ""])
        progress_lines.extend(
            [
                f"- `{item['parent_atom_id']}` — {item['children_mastered']}/{item['children_total']} "
                f"children mastered; integration: {item['integration_status']}"
                for item in self.active_expansions()
            ]
            or ["- None"]
        )
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
            zh_filename = filename.replace(".md", ".zh-CN.md")
            atomic_text(self.root / zh_filename, "\n".join(chinese_view(lines)).rstrip() + "\n")


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

    start_parser = sub.add_parser("start", help="Create or resume a course from one request", add_help=False)
    start_parser.add_argument("-h", "--help", action="store_true", dest="start_help")
    start_parser.add_argument("start_args", nargs=argparse.REMAINDER)
    init_parser = sub.add_parser("init", help="Create a course workspace")
    init_parser.add_argument("workspace")
    init_parser.add_argument("--course-id", required=True)
    init_parser.add_argument("--title", required=True)
    init_parser.add_argument("--goal", default="")

    simple_help = {
        "validate": "Validate canonical course and initialized subsystem state",
        "render": "Regenerate English and Chinese learner-facing views",
        "status": "Show current course, Atom, and subsystem status",
        "suggest-next": "Rank currently eligible next Atoms",
    }
    for command in ["validate", "render", "status", "suggest-next"]:
        command_parser = sub.add_parser(command, help=simple_help[command])
        command_parser.add_argument("workspace")
        if command == "status":
            command_parser.add_argument("--json", action="store_true")

    import_parser = sub.add_parser("import-plan", help="Import or update a source-grounded Knowledge Atom plan")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    mutation_args(import_parser)

    activate_parser = sub.add_parser("activate", help="Activate one eligible Atom as the learning focus")
    activate_parser.add_argument("workspace")
    activate_parser.add_argument("atom_id")
    mutation_args(activate_parser)

    skip_parser = sub.add_parser("skip", help="Preview or apply a reversible flexible-progression decision")
    skip_parser.add_argument("workspace")
    skip_parser.add_argument("atom_id")
    skip_parser.add_argument("--mode", choices=sorted(SKIP_MODES), default="diagnostic")
    skip_parser.add_argument("--reason-code", choices=sorted(SKIP_REASON_CODES))
    skip_parser.add_argument("--note", default="")
    skip_parser.add_argument("--confirmed", action="store_true")
    mutation_args(skip_parser)

    unskip_parser = sub.add_parser("unskip", help="Restore a skipped or deferred Atom")
    unskip_parser.add_argument("workspace")
    unskip_parser.add_argument("atom_id")
    mutation_args(unskip_parser)

    expand_parser = sub.add_parser("expand", help="Turn a detailed explanation request into ordered child Atoms")
    expand_parser.add_argument("workspace")
    expand_parser.add_argument("atom_id")
    expand_parser.add_argument("--plan", required=True)
    expand_parser.add_argument("--confirmed", action="store_true")
    mutation_args(expand_parser)

    update_parser = sub.add_parser("update-session", help="Update current learner understanding, confusion, and next action")
    update_parser.add_argument("workspace")
    update_parser.add_argument("--input", required=True)
    mutation_args(update_parser)

    question_parser = sub.add_parser("record-question", help="Record and classify a learner question")
    question_parser.add_argument("workspace")
    question_parser.add_argument("--input", required=True)
    mutation_args(question_parser)

    route_parser = sub.add_parser(
        "route-concept",
        help="Classify an unfamiliar related concept and preview or apply a learner-friendly route",
    )
    route_parser.add_argument("workspace")
    route_parser.add_argument("--input", required=True)
    route_parser.add_argument("--action", choices=sorted(CONCEPT_ACTIONS), default="preview")
    route_parser.add_argument("--confirmed", action="store_true")
    mutation_args(route_parser)

    resolve_parser = sub.add_parser("resolve-question", help="Resolve or dismiss a recorded question")
    resolve_parser.add_argument("workspace")
    resolve_parser.add_argument("question_id")
    resolve_parser.add_argument("--resolution", required=True)
    resolve_parser.add_argument("--dismissed", action="store_true")
    mutation_args(resolve_parser)

    evidence_parser = sub.add_parser("record-evidence", help="Record pending mastery Evidence for the Active Atom")
    evidence_parser.add_argument("workspace")
    evidence_parser.add_argument("--input", required=True)
    mutation_args(evidence_parser)

    assess_parser = sub.add_parser("assess", help="Assess pending Evidence and update Atom mastery")
    assess_parser.add_argument("workspace")
    assess_parser.add_argument("atom_id")
    assess_parser.add_argument("--evidence-id", required=True)
    assess_parser.add_argument("--now")
    mutation_args(assess_parser)

    refresh_parser = sub.add_parser("refresh-reviews", help="Refresh review-due state from scheduled intervals")
    refresh_parser.add_argument("workspace")
    refresh_parser.add_argument("--now")
    mutation_args(refresh_parser)

    pause_parser = sub.add_parser("pause", help="Pause the current learning focus with a reason")
    pause_parser.add_argument("workspace")
    pause_parser.add_argument("--reason", required=True)
    mutation_args(pause_parser)

    backtrack_parser = sub.add_parser("backtrack", help="Temporarily move to a prerequisite Atom")
    backtrack_parser.add_argument("workspace")
    backtrack_parser.add_argument("--to", required=True, dest="target_id")
    backtrack_parser.add_argument("--question-id")
    mutation_args(backtrack_parser)

    resume_parser = sub.add_parser("resume", help="Resume the most recently interrupted Atom")
    resume_parser.add_argument("workspace")
    mutation_args(resume_parser)

    restructure_parser = sub.add_parser("restructure", help="Preview or apply an approved split/merge proposal")
    restructure_parser.add_argument("workspace")
    restructure_parser.add_argument("--proposal", required=True)
    restructure_parser.add_argument("--confirmed", action="store_true")
    mutation_args(restructure_parser)
    evolve_parser = sub.add_parser("evolve", help="Analyze and safely evolve course structure", add_help=False)
    evolve_parser.add_argument("-h", "--help", action="store_true", dest="evolution_help")
    evolve_parser.add_argument("evolution_args", nargs=argparse.REMAINDER)
    research_parser = sub.add_parser("research", help="Map, read, verify, and synthesize research papers", add_help=False)
    research_parser.add_argument("-h", "--help", action="store_true", dest="research_help")
    research_parser.add_argument("research_args", nargs=argparse.REMAINDER)
    intake_parser = sub.add_parser("intake", help="Capture and guide sources, outline, or topic intake", add_help=False)
    intake_parser.add_argument("-h", "--help", action="store_true", dest="intake_help")
    intake_parser.add_argument("intake_args", nargs=argparse.REMAINDER)
    rag_parser = sub.add_parser("rag", help="Index, retrieve, correct, and evaluate grounded evidence", add_help=False)
    rag_parser.add_argument("-h", "--help", action="store_true", dest="rag_help")
    rag_parser.add_argument("rag_args", nargs=argparse.REMAINDER)
    adapt_parser = sub.add_parser("adapt", help="Manage privacy-safe cross-session presentation preferences", add_help=False)
    adapt_parser.add_argument("-h", "--help", action="store_true", dest="adaptation_help")
    adapt_parser.add_argument("adaptation_args", nargs=argparse.REMAINDER)
    exam_parser = sub.add_parser("exam", help="Process exam sources and generate targeted preparation", add_help=False)
    exam_parser.add_argument("-h", "--help", action="store_true", dest="exam_help")
    exam_parser.add_argument("exam_args", nargs=argparse.REMAINDER)
    lineage_parser = sub.add_parser("lineage", help="Build and query multi-lens knowledge lineage", add_help=False)
    lineage_parser.add_argument("-h", "--help", action="store_true", dest="lineage_help")
    lineage_parser.add_argument("lineage_args", nargs=argparse.REMAINDER)
    return parser


def run(args: argparse.Namespace) -> None:
    if args.command == "start":
        from intake import IntakeError
        from rag import RagError
        from wizard import WizardError, run as run_wizard

        try:
            run_wizard(["--help"] if args.start_help else args.start_args)
        except (WizardError, IntakeError, RagError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "lineage":
        from lineage import AtomLearnError as LineageAtomLearnError
        from lineage import LineageError, run as run_lineage

        try:
            run_lineage(["--help"] if args.lineage_help else args.lineage_args)
        except (LineageError, LineageAtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "exam":
        from exam import AtomLearnError as ExamAtomLearnError
        from exam import ExamError, run as run_exam

        try:
            run_exam(["--help"] if args.exam_help else args.exam_args)
        except (ExamError, ExamAtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "adapt":
        from adaptation import AdaptationError, run as run_adaptation
        from adaptation import AtomLearnError as AdaptationAtomLearnError

        try:
            run_adaptation(["--help"] if args.adaptation_help else args.adaptation_args)
        except (AdaptationError, AdaptationAtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "rag":
        from rag import AtomLearnError as RagAtomLearnError
        from rag import RagError, run as run_rag

        try:
            run_rag(["--help"] if args.rag_help else args.rag_args)
        except (RagError, RagAtomLearnError, OSError, sqlite3.Error, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "intake":
        from intake import AtomLearnError as IntakeAtomLearnError
        from intake import IntakeError, run as run_intake

        try:
            run_intake(["--help"] if args.intake_help else args.intake_args)
        except (IntakeError, IntakeAtomLearnError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
    if args.command == "research":
        from research import AtomLearnError as ResearchAtomLearnError
        from research import ResearchError, run as run_research

        try:
            run_research(["--help"] if args.research_help else args.research_args)
        except (ResearchError, ResearchAtomLearnError) as exc:
            raise AtomLearnError(str(exc)) from exc
        return
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
        research_root = workspace.meta / "research"
        if research_root.is_dir():
            from research import AtomLearnError as ResearchAtomLearnError
            from research import ResearchEngine, ResearchError

            try:
                research_engine = ResearchEngine.load(str(workspace.root))
                errors.extend(f"research: {error}" for error in research_engine.validate())
            except (ResearchError, ResearchAtomLearnError) as exc:
                errors.append(f"research: {exc}")
        intake_path = workspace.meta / "intake.yaml"
        if intake_path.exists():
            from intake import AtomLearnError as IntakeAtomLearnError
            from intake import IntakeEngine, IntakeError

            try:
                intake_engine = IntakeEngine.load(str(workspace.root))
                errors.extend(f"intake: {error}" for error in intake_engine.validate())
            except (IntakeError, IntakeAtomLearnError) as exc:
                errors.append(f"intake: {exc}")
        rag_root = workspace.meta / "rag"
        if rag_root.is_dir():
            from rag import AtomLearnError as RagAtomLearnError
            from rag import RagEngine, RagError

            try:
                rag_engine = RagEngine.load(str(workspace.root))
                errors.extend(f"rag: {error}" for error in rag_engine.validate())
            except (RagError, RagAtomLearnError) as exc:
                errors.append(f"rag: {exc}")
        adaptation_root = workspace.meta / "adaptation"
        if adaptation_root.is_dir():
            from adaptation import AdaptationEngine, AdaptationError
            from adaptation import AtomLearnError as AdaptationAtomLearnError

            try:
                adaptation_engine = AdaptationEngine.load(str(workspace.root))
                errors.extend(f"adaptation: {error}" for error in adaptation_engine.validate())
            except (AdaptationError, AdaptationAtomLearnError) as exc:
                errors.append(f"adaptation: {exc}")
        exam_root = workspace.meta / "exam"
        if exam_root.is_dir():
            from exam import AtomLearnError as ExamAtomLearnError
            from exam import ExamEngine, ExamError

            try:
                exam_engine = ExamEngine.load(str(workspace.root))
                errors.extend(f"exam: {error}" for error in exam_engine.validate())
            except (ExamError, ExamAtomLearnError) as exc:
                errors.append(f"exam: {exc}")
        lineage_root = workspace.meta / "lineage"
        if lineage_root.is_dir():
            from lineage import AtomLearnError as LineageAtomLearnError
            from lineage import LineageEngine, LineageError

            try:
                lineage_engine = LineageEngine.load(str(workspace.root))
                errors.extend(f"lineage: {error}" for error in lineage_engine.validate())
            except (LineageError, LineageAtomLearnError, OSError, yaml.YAMLError) as exc:
                errors.append(f"lineage: {exc}")
        if errors:
            raise AtomLearnError("Workspace validation failed:\n- " + "\n- ".join(errors))
        emit({"ok": True, "revision": workspace.revision, "atoms": len(workspace.atoms)})
        return
    if args.command == "render":
        errors = workspace.validate()
        if errors:
            raise AtomLearnError("Cannot render invalid workspace:\n- " + "\n- ".join(errors))
        workspace.render()
        emit({"ok": True, "views": VIEW_FILES + ZH_VIEW_FILES})
        return
    if args.command == "status":
        summary = workspace.status_summary()
        if (workspace.meta / "adaptation").is_dir():
            from adaptation import AdaptationEngine

            phase = workspace.current.get("phase")
            context = "orientation" if phase == "orientation" else ("review" if phase == "reviewing" else "teaching")
            adaptation_engine = AdaptationEngine.load(str(workspace.root))
            adaptation_errors = adaptation_engine.validate()
            if adaptation_errors:
                raise AtomLearnError("Adaptation validation failed:\n- " + "\n- ".join(adaptation_errors))
            summary["adaptation"] = adaptation_engine.guidance(context)
        if (workspace.meta / "exam").is_dir():
            from exam import ExamEngine

            exam_engine = ExamEngine.load(str(workspace.root))
            exam_errors = exam_engine.validate()
            if exam_errors:
                raise AtomLearnError("Exam validation failed:\n- " + "\n- ".join(exam_errors))
            summary["exam"] = exam_engine.status()
        if (workspace.meta / "lineage").is_dir():
            from lineage import LineageEngine, LineageError

            try:
                lineage_engine = LineageEngine.load(str(workspace.root))
                lineage_errors = lineage_engine.validate()
            except (LineageError, AtomLearnError, OSError, yaml.YAMLError) as exc:
                raise AtomLearnError(f"Cannot load lineage status: {exc}") from exc
            if lineage_errors:
                raise AtomLearnError("Lineage validation failed:\n- " + "\n- ".join(lineage_errors))
            summary["lineage"] = lineage_engine.status()
        emit(summary, as_json=args.json)
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
    elif args.command == "skip":
        if args.mode == "diagnostic":
            emit({"ok": True, **workspace.skip_guidance(args.atom_id)})
            return
        result = workspace.skip_atom(
            args.atom_id,
            args.mode,
            args.reason_code,
            args.note,
            args.confirmed,
        )
        workspace.commit(
            "atom.provisionally_skipped" if args.mode == "provisional" else "atom.deferred",
            "Applied a learner-directed flexible progression decision",
            result,
        )
        emit({"ok": True, "revision": workspace.revision, **result})
    elif args.command == "unskip":
        result = workspace.unskip_atom(args.atom_id)
        workspace.commit(
            "atom.flexibility_revoked",
            "Restored a skipped or deferred Atom to the learning path",
            result,
        )
        emit({"ok": True, "revision": workspace.revision, **result})
    elif args.command == "expand":
        plan = read_data(Path(args.plan))
        if not args.confirmed:
            emit(
                {
                    "ok": True,
                    "applied": False,
                    "parent_atom_id": args.atom_id,
                    "plan": plan,
                    "message": (
                        "Review the ordered child Atoms and rerun with --confirmed. "
                        "A learner request for a detailed explanation is explicit confirmation."
                    ),
                }
            )
            return
        result = workspace.expand_atom(args.atom_id, plan)
        workspace.commit(
            "atom.expanded_for_detail",
            "Converted a detailed explanation request into an ordered Atom branch",
            result,
        )
        emit({"ok": True, "revision": workspace.revision, "applied": True, **result})
    elif args.command == "update-session":
        workspace.update_session(read_data(Path(args.input)))
        workspace.commit("session.updated", "Persisted the current teaching state")
        emit({"ok": True, "revision": workspace.revision})
    elif args.command == "record-question":
        question_id = workspace.record_question(read_data(Path(args.input)))
        workspace.commit("question.recorded", "Recorded and routed a learner question", {"question_id": question_id})
        emit({"ok": True, "revision": workspace.revision, "question_id": question_id})
    elif args.command == "route-concept":
        payload = read_data(Path(args.input))
        if args.action == "preview":
            emit({"ok": True, "applied": False, "revision": workspace.revision, **workspace.concept_route_guidance(payload)})
            return
        result = workspace.apply_concept_route(payload, args.action, args.confirmed)
        workspace.commit(
            "concept.routed",
            "Applied a learner-visible relationship decision for an unfamiliar concept",
            {
                "relation": result["card"]["relation"],
                "action": args.action,
                "question_id": result["question_id"],
                "related_atom_id": result["related_atom_id"],
                "created_atom_id": result["created_atom_id"],
            },
        )
        emit({"ok": True, "applied": True, "revision": workspace.revision, **result})
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
