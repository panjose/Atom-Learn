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
CORPUS_ROLES = {"full", "partial", "supplemental", "outline_like", "unknown"}
CORPUS_EXPANSIONS = {"closed_corpus", "correct_gaps", "discover"}


class IntakeError(RuntimeError):
    """A user-correctable intake error."""


def template_dir() -> Path:
    from core_paths import CORE_ROOT

    return CORE_ROOT / "assets" / "templates"


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
        return cls(workspace, cls.upgrade_state(read_data(path), workspace.revision))

    @staticmethod
    def _input_inventory(state: dict[str, Any]) -> dict[str, bool]:
        return {
            "has_sources": bool(state.get("source_materials")),
            "has_outline": bool(state.get("outline_items")),
            "has_topic": bool(state.get("topic_terms")),
        }

    @staticmethod
    def _default_corpus_policy(mode: str, inventory: dict[str, bool]) -> dict[str, Any]:
        supplied = sum(inventory.values())
        if supplied > 1:
            role = "partial"
            expansion = "correct_gaps"
        elif mode == "outline":
            role = "outline_like"
            expansion = "correct_gaps"
        elif mode == "topic":
            role = "unknown"
            expansion = "discover"
        else:
            role = "unknown"
            expansion = "correct_gaps"
        return {"role": role, "expansion": expansion, "user_confirmed": False}

    @staticmethod
    def _normalize_corpus_policy(
        value: Any, mode: str, inventory: dict[str, bool]
    ) -> dict[str, Any]:
        if value is None:
            return IntakeEngine._default_corpus_policy(mode, inventory)
        if not isinstance(value, dict):
            raise IntakeError("corpus_policy must be a mapping")
        unexpected = sorted(set(value) - {"role", "expansion", "user_confirmed"})
        if unexpected:
            raise IntakeError("corpus_policy contains unsupported fields: " + ", ".join(unexpected))
        role = value.get("role")
        expansion = value.get("expansion")
        user_confirmed = value.get("user_confirmed", False)
        if role not in CORPUS_ROLES:
            raise IntakeError("corpus_policy.role must be one of: " + ", ".join(sorted(CORPUS_ROLES)))
        if expansion not in CORPUS_EXPANSIONS:
            raise IntakeError(
                "corpus_policy.expansion must be one of: " + ", ".join(sorted(CORPUS_EXPANSIONS))
            )
        if not isinstance(user_confirmed, bool):
            raise IntakeError("corpus_policy.user_confirmed must be boolean")
        return {"role": role, "expansion": expansion, "user_confirmed": user_confirmed}

    @staticmethod
    def _normalize_mandatory_anchors(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise IntakeError("mandatory_anchors must be a list")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(value, start=1):
            if isinstance(raw, str):
                item: dict[str, Any] = {"id": f"goal.anchor.{index}", "query": raw}
            elif isinstance(raw, dict):
                item = raw
            else:
                raise IntakeError(f"mandatory_anchors[{index - 1}] must be a string or mapping")
            anchor_id = require_id(item.get("id", f"goal.anchor.{index}"), f"mandatory_anchors[{index - 1}].id")
            if anchor_id in seen:
                raise IntakeError(f"duplicate mandatory anchor ID: {anchor_id}")
            seen.add(anchor_id)
            minimum_sources = item.get("minimum_sources", 1)
            if (
                not isinstance(minimum_sources, int)
                or isinstance(minimum_sources, bool)
                or not 1 <= minimum_sources <= 10
            ):
                raise IntakeError(f"{anchor_id}.minimum_sources must be between 1 and 10")
            authoritative = item.get("authoritative", False)
            if not isinstance(authoritative, bool):
                raise IntakeError(f"{anchor_id}.authoritative must be boolean")
            result.append(
                {
                    "id": anchor_id,
                    "query": text(item.get("query"), f"{anchor_id}.query", limit=4000),
                    "minimum_sources": minimum_sources,
                    "authoritative": authoritative,
                    "origin": "explicit",
                }
            )
        return result

    @staticmethod
    def _build_goal_contract(state: dict[str, Any]) -> dict[str, Any]:
        inventory = state["input_inventory"]
        policy = state["corpus_policy"]
        anchors: list[dict[str, Any]] = []
        used: set[str] = set()

        def add(anchor: dict[str, Any]) -> None:
            anchor_id = anchor["id"]
            if anchor_id in used:
                raise IntakeError(f"duplicate Goal Contract anchor ID: {anchor_id}")
            used.add(anchor_id)
            anchors.append(anchor)

        for item in state.get("outline_items", []):
            add(
                {
                    "id": item["id"],
                    "query": " — ".join(value for value in [item["title"], item.get("notes", "")] if value),
                    "minimum_sources": 1,
                    "authoritative": False,
                    "origin": "outline",
                }
            )
        topic_authoritative = (
            inventory["has_topic"]
            and not inventory["has_sources"]
            and policy["expansion"] != "closed_corpus"
        )
        for index, term in enumerate(state.get("topic_terms", []), start=1):
            add(
                {
                    "id": f"topic.{index}",
                    "query": f"{term}: {state['goal']}",
                    "minimum_sources": 1,
                    "authoritative": topic_authoritative,
                    "origin": "topic",
                }
            )
        for item in state.get("mandatory_anchors", []):
            add(copy.deepcopy(item))
        add(
            {
                "id": "scope.goal",
                "query": state["goal"],
                "minimum_sources": 2 if topic_authoritative else 1,
                "authoritative": topic_authoritative,
                "origin": "goal",
            }
        )
        return {
            "target": state["goal"],
            "use_case": state["desired_outcome"],
            "target_depth": state["target_depth"],
            "mandatory_anchors": anchors,
        }

    @classmethod
    def upgrade_state(cls, state: dict[str, Any], course_revision: int) -> dict[str, Any]:
        """Upgrade legacy intake state in memory without mutating a read-only command."""
        if isinstance(state.get("goal_contract"), dict) and isinstance(state.get("corpus_policy"), dict):
            return state
        payload = {
            key: copy.deepcopy(value)
            for key, value in state.items()
            if key
            in {
                "mode",
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
                "mandatory_anchors",
            }
        }
        upgraded = cls._normalized(payload, read_data(template_dir() / "intake.yaml"), course_revision)
        for field in [
            "revision",
            "status",
            "course_revision_at_capture",
            "planned_course_revision",
            "created_at",
            "updated_at",
        ]:
            if field in state:
                upgraded[field] = copy.deepcopy(state[field])
        upgraded["planned_intake_revision"] = None
        upgraded["planned_goal_contract_revision"] = None
        return upgraded

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
        if "mandatory_anchors" in payload:
            state["mandatory_anchors"] = IntakeEngine._normalize_mandatory_anchors(payload["mandatory_anchors"])
        else:
            state["mandatory_anchors"] = IntakeEngine._normalize_mandatory_anchors(
                state.get("mandatory_anchors", [])
            )
        inventory = IntakeEngine._input_inventory(state)
        state["input_inventory"] = inventory
        previous_contract = (
            base.get("goal_contract")
            if base.get("created_at") and isinstance(base.get("goal_contract"), dict)
            else None
        )
        policy_value = payload.get("corpus_policy") if "corpus_policy" in payload else (
            state.get("corpus_policy") if previous_contract else None
        )
        state["corpus_policy"] = IntakeEngine._normalize_corpus_policy(policy_value, mode, inventory)
        contract = IntakeEngine._build_goal_contract(state)
        previous_revision = base.get("goal_contract_revision", 0)
        if not isinstance(previous_revision, int) or isinstance(previous_revision, bool) or previous_revision < 0:
            previous_revision = 0
        state["goal_contract_revision"] = (
            previous_revision if previous_contract is None or previous_contract == contract else previous_revision + 1
        )
        state["goal_contract"] = contract
        state["schema_version"] = SCHEMA_VERSION
        state["course_revision_at_capture"] = state.get("course_revision_at_capture", course_revision)
        state.setdefault("planned_course_revision", None)
        state.setdefault("planned_intake_revision", None)
        state.setdefault("planned_goal_contract_revision", None)
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
            mandatory_anchors = self._normalize_mandatory_anchors(self.state.get("mandatory_anchors", []))
            inventory = self._input_inventory(self.state)
            policy = self._normalize_corpus_policy(self.state.get("corpus_policy"), self.state.get("mode"), inventory)
            expected_contract = self._build_goal_contract(
                {
                    **self.state,
                    "input_inventory": inventory,
                    "corpus_policy": policy,
                    "mandatory_anchors": mandatory_anchors,
                    "outline_items": outline,
                }
            )
        except (AtomLearnError, IntakeError) as exc:
            errors.append(str(exc))
            outline = []
            expected_contract = None
            inventory = None
            policy = None
        if inventory is not None and self.state.get("input_inventory") != inventory:
            errors.append("input_inventory does not match the supplied intake fields")
        if policy is not None and self.state.get("corpus_policy") != policy:
            errors.append("corpus_policy is not canonical")
        if expected_contract is not None and self.state.get("goal_contract") != expected_contract:
            errors.append("goal_contract does not match the current goal and input anchors")
        contract_revision = self.state.get("goal_contract_revision")
        if not isinstance(contract_revision, int) or isinstance(contract_revision, bool) or contract_revision < 0:
            errors.append("goal_contract_revision must be a non-negative integer")
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
        if self.state.get("status") == "planned":
            if self.state.get("planned_course_revision") is None:
                errors.append("planned intake requires planned_course_revision")
            if self.state.get("planned_intake_revision") is None:
                errors.append("planned intake requires planned_intake_revision")
            if self.state.get("planned_goal_contract_revision") is None:
                errors.append("planned intake requires planned_goal_contract_revision")
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
        if not self._coverage_ready():
            return "discovering"
        if self.state.get("status") == "planned":
            return "planned"
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
        expected_intake_revision = (
            self.state.get("planned_intake_revision")
            if self.state.get("status") == "planned"
            else self.revision
        )
        expected_contract_revision = (
            self.state.get("planned_goal_contract_revision")
            if self.state.get("status") == "planned"
            else self.state.get("goal_contract_revision")
        )
        return bool(
            report
            and report.get("gate") == "pass"
            and report.get("intake_revision") == expected_intake_revision
            and report.get("goal_contract_revision") == expected_contract_revision
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
                "Evaluate the Goal Contract against retrieved source candidates before planning.",
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
        if not self._coverage_ready():
            report = self._coverage_report()
            if report is None:
                blockers.append("RAG coverage has not been evaluated for this intake revision.")
            elif report.get("intake_revision") != self.revision:
                blockers.append("RAG coverage is stale for the current intake revision.")
            elif report.get("goal_contract_revision") != self.state.get("goal_contract_revision"):
                blockers.append("RAG coverage is stale for the current Goal Contract revision.")
            elif not self._coverage_ready() and report.get("gate") == "pass":
                blockers.append("RAG coverage is stale for the current retrieval corpus revision.")
            elif self.state.get("corpus_policy", {}).get("expansion") == "closed_corpus":
                blockers.append("The closed corpus does not yet support every Goal Contract requirement.")
            else:
                blockers.append("RAG coverage still has unverified, weak, or missing requirements.")
        return {
            "mode": mode,
            "input_inventory": copy.deepcopy(self.state.get("input_inventory")),
            "corpus_policy": copy.deepcopy(self.state.get("corpus_policy")),
            "goal_contract_revision": self.state.get("goal_contract_revision"),
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
            "mandatory_anchors",
            "corpus_policy",
        ]:
            if field not in payload and field in base:
                payload[field] = copy.deepcopy(base[field])
        updated = self._normalized(payload, base, self.workspace.revision)
        updated["revision"] = self.revision
        updated["created_at"] = self.state.get("created_at")
        updated["status"] = "captured"
        updated["planned_course_revision"] = None
        updated["planned_intake_revision"] = None
        updated["planned_goal_contract_revision"] = None
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
        expected = {item["id"] for item in self.state.get("source_materials", [])}
        expected.update(item["id"] for item in self.state.get("discovery_sources", []))
        if self.state.get("outline_items"):
            expected.add(self.state.get("outline_source_id"))
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
        self.state["planned_intake_revision"] = self.revision
        self.state["planned_goal_contract_revision"] = self.state.get("goal_contract_revision")
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
            "goal_contract_revision": self.state.get("goal_contract_revision"),
            "corpus_policy": copy.deepcopy(self.state.get("corpus_policy")),
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
            f"- Goal Contract revision: `{self.state.get('goal_contract_revision')}`",
            f"- Target depth: {self.state.get('target_depth')}",
            f"- Desired outcome: {self.state.get('desired_outcome')}",
            f"- Corpus role: `{self.state.get('corpus_policy', {}).get('role')}`",
            f"- Corpus expansion: `{self.state.get('corpus_policy', {}).get('expansion')}`",
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
    initialize = sub.add_parser("init", help="Initialize intake from sources, an outline, or a topic")
    initialize.add_argument("workspace")
    initialize.add_argument("--input", required=True)
    simple_help = {
        "status": "Show intake mode, readiness, revisions, and blockers",
        "validate": "Validate canonical intake state",
        "guidance": "Generate mode-specific discovery and planning guidance",
        "render": "Regenerate the course intake view",
    }
    for action in ["status", "validate", "guidance", "render"]:
        command = sub.add_parser(action, help=simple_help[action])
        command.add_argument("workspace")
    update = sub.add_parser("update", help="Update discovery results, assumptions, or intake scope")
    update.add_argument("workspace")
    update.add_argument("--input", required=True)
    add_revision(update)
    complete = sub.add_parser("complete", help="Close intake after coverage and plan traceability pass")
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
