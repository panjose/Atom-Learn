#!/usr/bin/env python3
"""Unified course intake for sources, outlines, and topic-only requests."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from atomlearn import (
    SCHEMA_VERSION,
    AtomLearnError,
    Workspace,
    atomic_text,
    iso,
    load_workspace,
    read_data,
    require_id,
    require_string,
    unique,
    write_yaml,
)


INTAKE_MODES = {"sources", "outline", "topic"}
INTAKE_STATUSES = {"captured", "discovering", "ready_to_plan", "planned"}
DESIRED_OUTCOMES = {"orientation", "working_knowledge", "exam", "project", "research"}
TARGET_DEPTHS = {"overview", "working", "advanced", "expert"}
SOURCE_TYPES = {"pdf", "book", "notes", "documentation", "website", "database", "outline", "exam", "other"}


class IntakeError(RuntimeError):
    """A user-correctable intake error."""


def template_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def text(value: Any, label: str, *, allow_empty: bool = False, limit: int = 4000) -> str:
    result = require_string(value, label, allow_empty=allow_empty)
    if len(result) > limit:
        raise IntakeError(f"{label} must be at most {limit} characters")
    return result


def text_list(value: Any, label: str, *, limit: int = 1000) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise IntakeError(f"{label} must be a string list")
    result = unique(item.strip() for item in value if item.strip())
    if any(len(item) > limit for item in result):
        raise IntakeError(f"{label} entries must be at most {limit} characters")
    return result


def detect_mode(payload: dict[str, Any]) -> str:
    supplied = []
    if payload.get("source_materials"):
        supplied.append("sources")
    if payload.get("outline_items"):
        supplied.append("outline")
    if payload.get("topic_terms"):
        supplied.append("topic")
    explicit = payload.get("mode")
    if explicit is not None:
        if explicit not in INTAKE_MODES:
            raise IntakeError(f"intake.mode must be one of: {', '.join(sorted(INTAKE_MODES))}")
        return explicit
    if len(supplied) != 1:
        raise IntakeError("Set intake.mode when the payload has zero or multiple primary input types")
    return supplied[0]


class IntakeEngine:
    def __init__(self, workspace: Workspace, state: dict[str, Any]):
        self.workspace = workspace
        self.path = workspace.meta / "intake.yaml"
        self.events_path = workspace.meta / "intake-events.ndjson"
        self.state = state

    @classmethod
    def initialize(cls, workspace_path: str, payload: dict[str, Any]) -> "IntakeEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise IntakeError("Cannot initialize intake in an invalid workspace:\n- " + "\n- ".join(errors))
        path = workspace.meta / "intake.yaml"
        if path.exists():
            raise IntakeError("Course intake is already initialized")
        state = cls._normalized(payload, read_data(template_dir() / "intake.yaml"), workspace.revision)
        timestamp = iso()
        state["created_at"] = timestamp
        state["updated_at"] = timestamp
        engine = cls(workspace, state)
        state["status"] = engine.derived_status()
        errors = engine.validate()
        if errors:
            raise IntakeError("Invalid intake:\n- " + "\n- ".join(errors))
        write_yaml(path, state)
        atomic_text(engine.events_path, "")
        engine.render()
        return engine

    @classmethod
    def load(cls, workspace_path: str) -> "IntakeEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise IntakeError("Cannot use intake in an invalid workspace:\n- " + "\n- ".join(errors))
        path = workspace.meta / "intake.yaml"
        if not path.is_file():
            raise IntakeError("Course intake is not initialized; run `intake init` first")
        return cls(workspace, read_data(path))

    @staticmethod
    def _normalized(payload: dict[str, Any], base: dict[str, Any], course_revision: int) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IntakeError("intake payload must be a mapping")
        state = copy.deepcopy(base)
        mode = detect_mode(payload)
        state["mode"] = mode
        for field, label, allow_empty, limit in [
            ("request_summary", "request_summary", False, 4000),
            ("goal", "goal", False, 2000),
        ]:
            if field in payload or not state.get(field):
                state[field] = text(payload.get(field, state.get(field)), label, allow_empty=allow_empty, limit=limit)
        state["desired_outcome"] = payload.get("desired_outcome", state.get("desired_outcome"))
        state["target_depth"] = payload.get("target_depth", state.get("target_depth"))
        for field in ["prior_knowledge", "constraints", "topic_terms", "ambiguities", "assumptions"]:
            if field in payload:
                state[field] = text_list(payload[field], field)
        if "source_materials" in payload:
            state["source_materials"] = IntakeEngine._normalize_sources(payload["source_materials"], "source_materials")
        if "discovery_sources" in payload:
            state["discovery_sources"] = IntakeEngine._normalize_sources(
                payload["discovery_sources"], "discovery_sources"
            )
        if "outline_source_id" in payload:
            state["outline_source_id"] = require_id(payload["outline_source_id"], "outline_source_id")
        if "outline_items" in payload:
            state["outline_items"] = IntakeEngine._normalize_outline(payload["outline_items"])
        state["schema_version"] = SCHEMA_VERSION
        state["course_revision_at_capture"] = state.get("course_revision_at_capture", course_revision)
        state.setdefault("planned_course_revision", None)
        return state

    @staticmethod
    def _normalize_sources(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise IntakeError(f"{label} must be a list")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise IntakeError(f"{label}[{index}] must be a mapping")
            source_id = require_id(item.get("id"), f"{label}[{index}].id")
            if source_id in seen:
                raise IntakeError(f"duplicate source ID: {source_id}")
            seen.add(source_id)
            source_type = item.get("type", "other")
            if source_type not in SOURCE_TYPES:
                raise IntakeError(f"{source_id}.type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
            result.append(
                {
                    "id": source_id,
                    "title": text(item.get("title"), f"{source_id}.title", limit=1000),
                    "type": source_type,
                    "location": text(item.get("location"), f"{source_id}.location", limit=4000),
                    "version": text(
                        item.get("version", ""), f"{source_id}.version", allow_empty=True, limit=1000
                    ),
                }
            )
        return result

    @staticmethod
    def _normalize_outline(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise IntakeError("outline_items must be a list")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise IntakeError(f"outline_items[{index}] must be a mapping")
            item_id = require_id(item.get("id"), f"outline_items[{index}].id")
            if item_id in seen:
                raise IntakeError(f"duplicate outline item ID: {item_id}")
            seen.add(item_id)
            parent_id = item.get("parent_id")
            if parent_id is not None:
                parent_id = require_id(parent_id, f"{item_id}.parent_id")
            result.append(
                {
                    "id": item_id,
                    "title": text(item.get("title"), f"{item_id}.title", limit=1000),
                    "parent_id": parent_id,
                    "notes": text(item.get("notes", ""), f"{item_id}.notes", allow_empty=True),
                }
            )
        return result

    @property
    def revision(self) -> int:
        revision = self.state.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise IntakeError("intake revision must be a non-negative integer")
        return revision

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise IntakeError(f"Stale intake revision: expected {expected}, current is {self.revision}")

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.state.get("schema_version") != SCHEMA_VERSION:
            errors.append("intake has unsupported schema_version")
        if self.state.get("mode") not in INTAKE_MODES:
            errors.append("intake mode is invalid")
        if self.state.get("status") not in INTAKE_STATUSES:
            errors.append("intake status is invalid")
        if self.state.get("desired_outcome") not in DESIRED_OUTCOMES:
            errors.append("desired_outcome is invalid")
        if self.state.get("target_depth") not in TARGET_DEPTHS:
            errors.append("target_depth is invalid")
        try:
            text(self.state.get("request_summary"), "request_summary")
            text(self.state.get("goal"), "goal")
            for field in ["prior_knowledge", "constraints", "topic_terms", "ambiguities", "assumptions"]:
                text_list(self.state.get(field, []), field)
            self._normalize_sources(self.state.get("source_materials", []), "source_materials")
            self._normalize_sources(self.state.get("discovery_sources", []), "discovery_sources")
            outline = self._normalize_outline(self.state.get("outline_items", []))
        except (AtomLearnError, IntakeError) as exc:
            errors.append(str(exc))
            outline = []
        outline_ids = {item["id"] for item in outline}
        for item in outline:
            parent = item.get("parent_id")
            if parent is not None and parent not in outline_ids:
                errors.append(f"{item['id']}: outline parent does not exist: {parent}")
            if parent == item["id"]:
                errors.append(f"{item['id']}: outline item cannot be its own parent")
        errors.extend(self._outline_cycle_errors(outline))
        mode = self.state.get("mode")
        if mode == "sources" and not self.state.get("source_materials"):
            errors.append("sources mode requires source_materials")
        if mode == "outline" and not outline:
            errors.append("outline mode requires outline_items")
        if mode == "topic" and not self.state.get("topic_terms"):
            errors.append("topic mode requires topic_terms")
        if self.state.get("status") == "planned" and self.state.get("planned_course_revision") is None:
            errors.append("planned intake requires planned_course_revision")
        return unique(errors)

    def _outline_cycle_errors(self, outline: list[dict[str, Any]]) -> list[str]:
        parents = {item["id"]: item.get("parent_id") for item in outline}
        errors: list[str] = []
        for item_id in parents:
            trail: list[str] = []
            current: str | None = item_id
            while current is not None and current in parents:
                if current in trail:
                    errors.append("outline hierarchy cycle: " + " -> ".join(trail + [current]))
                    break
                trail.append(current)
                current = parents[current]
        return unique(errors)

    def derived_status(self) -> str:
        if self.state.get("status") == "planned":
            return "planned"
        mode = self.state.get("mode")
        if mode == "topic" and not self.state.get("discovery_sources"):
            return "discovering"
        if mode in {"outline", "topic"} and not self._coverage_ready():
            return "discovering"
        return "ready_to_plan"

    def _coverage_report(self) -> dict[str, Any] | None:
        path = self.workspace.meta / "rag" / "latest-coverage.yaml"
        if not path.is_file():
            return None
        try:
            report = read_data(path)
        except (OSError, AtomLearnError):
            return None
        return report if isinstance(report, dict) else None

    def _coverage_ready(self) -> bool:
        report = self._coverage_report()
        rag_state_path = self.workspace.meta / "rag" / "state.yaml"
        try:
            rag_revision = read_data(rag_state_path).get("revision") if rag_state_path.is_file() else None
        except (OSError, AtomLearnError):
            rag_revision = None
        return bool(
            report
            and report.get("gate") == "pass"
            and report.get("intake_revision") == self.revision
            and report.get("rag_revision") == rag_revision
            and report.get("requirements")
            and all(item.get("status") == "supported" for item in report["requirements"] if isinstance(item, dict))
        )

    def guidance(self) -> dict[str, Any]:
        mode = self.state.get("mode")
        common = [
            "Confirm the learning goal, target depth, prior knowledge, and constraints.",
            "Build a prerequisite DAG rather than copying source order.",
            "Attach every Atom to a source ID and stable locator.",
            "Validate the plan before activating the first Atom.",
        ]
        actions = {
            "sources": [
                "Inventory every supplied source and inspect its structure before atomization.",
                "Create a cross-source concept registry; merge duplicates and record conflicts.",
                "Sample dense or ambiguous sections instead of assuming the table of contents is sufficient.",
                "Use the user's materials as the primary authority and flag uncovered prerequisite gaps.",
            ],
            "outline": [
                "Preserve stable outline item IDs as coverage anchors, not as mandatory Atom boundaries.",
                "Split broad headings, merge duplicates, and infer prerequisite edges across sections.",
                "Register the outline as a source and use outline item IDs as locators.",
                "Run RAG coverage for every outline anchor and use corrective Web Search for weak or missing evidence.",
            ],
            "topic": [
                "Disambiguate the term and choose a practical boundary without demanding a full syllabus from the user.",
                "Discover at least one authoritative overview and one primary or technical source when appropriate.",
                "Ingest bounded Web Search evidence into RAG, rerank it, and pass the explicit coverage gate.",
                "Create a provisional 10-30 Atom map and label uncertain boundaries or dependencies.",
                "Show the learner the orientation map and refine it from their feedback and diagnostic evidence.",
            ],
        }[mode]
        blockers: list[str] = []
        if mode == "topic" and not self.state.get("discovery_sources"):
            blockers.append("Authoritative discovery sources have not been recorded yet.")
        if mode in {"outline", "topic"} and not self._coverage_ready():
            report = self._coverage_report()
            if report is None:
                blockers.append("RAG coverage has not been evaluated for this intake revision.")
            elif report.get("intake_revision") != self.revision:
                blockers.append("RAG coverage is stale for the current intake revision.")
            elif not self._coverage_ready() and report.get("gate") == "pass":
                blockers.append("RAG coverage is stale for the current retrieval corpus revision.")
            else:
                blockers.append("RAG coverage still has unverified, weak, or missing requirements.")
        return {
            "mode": mode,
            "status": self.derived_status(),
            "ready_to_plan": self.derived_status() == "ready_to_plan",
            "blockers": blockers,
            "actions": actions + common,
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        base = copy.deepcopy(self.state)
        payload = copy.deepcopy(payload)
        payload.setdefault("mode", base.get("mode"))
        for field in [
            "request_summary",
            "goal",
            "desired_outcome",
            "target_depth",
            "prior_knowledge",
            "constraints",
            "source_materials",
            "outline_source_id",
            "outline_items",
            "topic_terms",
            "discovery_sources",
            "ambiguities",
            "assumptions",
        ]:
            if field not in payload and field in base:
                payload[field] = copy.deepcopy(base[field])
        updated = self._normalized(payload, base, self.workspace.revision)
        updated["revision"] = self.revision
        updated["created_at"] = self.state.get("created_at")
        updated["planned_course_revision"] = self.state.get("planned_course_revision")
        self.state = updated
        self.state["status"] = self.derived_status()
        return self.guidance()

    def complete(self) -> dict[str, Any]:
        guidance = self.guidance()
        if not guidance["ready_to_plan"]:
            raise IntakeError("Intake is not ready for planning:\n- " + "\n- ".join(guidance["blockers"]))
        if not self.workspace.atoms:
            raise IntakeError("Cannot complete intake before importing a Knowledge Atom plan")
        course_source_ids = {item.get("id") for item in self.workspace.course.get("sources", [])}
        mode = self.state["mode"]
        if mode == "sources":
            expected = {item["id"] for item in self.state.get("source_materials", [])}
        elif mode == "outline":
            expected = {self.state.get("outline_source_id")}
        else:
            expected = {item["id"] for item in self.state.get("discovery_sources", [])}
        missing = sorted(expected - course_source_ids)
        if missing:
            raise IntakeError("Course plan is missing intake source IDs: " + ", ".join(missing))
        ungrounded = sorted(
            atom_id
            for atom_id, atom in self.workspace.atoms.items()
            if atom.get("status") != "archived" and not atom.get("sources")
        )
        if ungrounded:
            raise IntakeError("Atoms without source locators: " + ", ".join(ungrounded))
        self.state["status"] = "planned"
        self.state["planned_course_revision"] = self.workspace.revision
        return {
            "mode": mode,
            "course_revision": self.workspace.revision,
            "atoms": len(self.workspace.atoms),
            "source_ids": sorted(course_source_ids),
        }

    def commit(self, event_type: str, details: dict[str, Any]) -> None:
        new_revision = self.revision + 1
        self.state["revision"] = new_revision
        self.state["updated_at"] = iso()
        errors = self.validate()
        if errors:
            raise IntakeError("Intake mutation would create invalid state:\n- " + "\n- ".join(errors))
        write_yaml(self.path, self.state)
        event = {
            "event_id": f"ievt-{new_revision:06d}",
            "revision": new_revision,
            "type": event_type,
            "at": self.state["updated_at"],
            "course_revision": self.workspace.revision,
            "details": details,
        }
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.render()

    def status_summary(self) -> dict[str, Any]:
        errors = self.validate()
        return {
            "valid": not errors,
            "validation_errors": errors,
            "intake_revision": self.revision,
            "course_revision": self.workspace.revision,
            "mode": self.state.get("mode"),
            "status": self.derived_status(),
            "goal": self.state.get("goal"),
            "guidance": self.guidance(),
        }

    def render(self) -> None:
        guidance = self.guidance()
        lines = [
            "# Course Intake",
            "",
            "> Generated by AtomLearn. Use `atomlearn intake` commands to change canonical intake state.",
            "",
            "## Request",
            "",
            f"- Mode: `{self.state.get('mode')}`",
            f"- Status: `{self.derived_status()}`",
            f"- Goal: {self.state.get('goal')}",
            f"- Target depth: {self.state.get('target_depth')}",
            f"- Desired outcome: {self.state.get('desired_outcome')}",
            "",
            "## Primary Inputs",
            "",
        ]
        mode = self.state.get("mode")
        if mode == "sources":
            lines.extend(
                [f"- `{item['id']}` — {item['title']} ({item['type']})" for item in self.state.get("source_materials", [])]
            )
        elif mode == "outline":
            lines.extend(
                [f"- `{item['id']}` — {item['title']}" for item in self.state.get("outline_items", [])]
            )
        else:
            lines.extend([f"- {item}" for item in self.state.get("topic_terms", [])])
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- {item}" for item in guidance["blockers"]] or ["- None"])
        lines.extend(["", "## Next Actions", ""])
        lines.extend([f"- {item}" for item in guidance["actions"]])
        lines.extend(["", "## Ambiguities", ""])
        lines.extend([f"- {item}" for item in self.state.get("ambiguities", [])] or ["- None"])
        lines.extend(["", "## Assumptions", ""])
        lines.extend([f"- {item}" for item in self.state.get("assumptions", [])] or ["- None"])
        atomic_text(self.workspace.root / "INTAKE.md", "\n".join(lines).rstrip() + "\n")


def add_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-intake-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture and guide AtomLearn course intake")
    sub = parser.add_subparsers(dest="action", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("workspace")
    initialize.add_argument("--input", required=True)
    for action in ["status", "validate", "guidance", "render"]:
        command = sub.add_parser(action)
        command.add_argument("workspace")
    update = sub.add_parser("update")
    update.add_argument("workspace")
    update.add_argument("--input", required=True)
    add_revision(update)
    complete = sub.add_parser("complete")
    complete.add_argument("workspace")
    add_revision(complete)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "init":
        engine = IntakeEngine.initialize(args.workspace, read_data(Path(args.input)))
        print(json.dumps(engine.status_summary(), ensure_ascii=False, indent=2))
        return
    engine = IntakeEngine.load(args.workspace)
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise IntakeError("Intake validation failed:\n- " + "\n- ".join(errors))
        print(json.dumps({"ok": True, "intake_revision": engine.revision}))
        return
    errors = engine.validate()
    if errors:
        raise IntakeError("Refusing to use invalid intake state:\n- " + "\n- ".join(errors))
    if args.action == "status":
        print(json.dumps(engine.status_summary(), ensure_ascii=False, indent=2))
        return
    if args.action == "guidance":
        print(json.dumps(engine.guidance(), ensure_ascii=False, indent=2))
        return
    if args.action == "render":
        engine.render()
        print(json.dumps({"ok": True, "view": "INTAKE.md"}))
        return
    engine.expect_revision(args.expected_intake_revision)
    if args.action == "update":
        result = engine.update(read_data(Path(args.input)))
        event_type = "intake.updated"
    elif args.action == "complete":
        result = engine.complete()
        event_type = "intake.completed"
    else:  # pragma: no cover
        raise IntakeError(f"Unhandled intake action: {args.action}")
    engine.commit(event_type, result)
    print(
        json.dumps(
            {
                "ok": True,
                "intake_revision": engine.revision,
                "course_revision": engine.workspace.revision,
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (IntakeError, AtomLearnError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
