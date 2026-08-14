#!/usr/bin/env python3
"""Multi-lens knowledge-lineage maps for AtomLearn workspaces."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
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
    require_id,
    require_number,
    require_string,
    unique,
    write_yaml,
)


ROLES = {
    "foundation", "motivation", "definition", "principle", "mechanism", "method", "example",
    "application", "boundary", "synthesis", "historical_milestone",
}
RELATION_TYPES = {
    "motivates", "defines", "derives", "generalizes", "specializes", "contrasts", "analogous_to",
    "extends", "refines", "supersedes", "applies_to", "implements", "evaluates", "bridges",
}
SYMMETRIC_RELATIONS = {"contrasts", "analogous_to", "bridges"}
THREAD_KINDS = {
    "learning_spine", "problem_to_solution", "derivation", "comparison", "application", "historical",
    "exam", "research", "custom",
}
LENSES = {"all", "structure", "learning", "conceptual", "exam", "research"}
STATE_KEYS = {"schema_version", "revision", "created_at", "updated_at"}
MAP_KEYS = {"schema_version", "revision", "annotations", "relations", "threads"}
ANNOTATION_KEYS = {"atom_id", "roles", "central_question", "contribution", "boundaries"}
RELATION_KEYS = {
    "id", "from_atom_id", "to_atom_id", "type", "rationale", "confidence", "source_refs",
}
SOURCE_REF_KEYS = {"source_id", "locator"}
THREAD_KEYS = {"id", "title", "kind", "goal", "atom_ids", "narrative", "confidence"}
EVENT_KEYS = {"event_id", "revision", "type", "at", "course_revision", "details"}
LINEAGE_VIEW_FILES = ["KNOWLEDGE_LINEAGE.md"]


class LineageError(RuntimeError):
    """A user-correctable knowledge-lineage error."""


def template_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def limited_text(value: Any, label: str, *, allow_empty: bool = False, limit: int = 2000) -> str:
    result = require_string(value, label, allow_empty=allow_empty).strip()
    if len(result) > limit:
        raise LineageError(f"{label} must be at most {limit} characters")
    return result


def string_list(value: Any, label: str, *, allowed: set[str] | None = None, maximum: int = 50) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(value) > maximum:
        raise LineageError(f"{label} must be a string list with at most {maximum} entries")
    result = unique(item.strip() for item in value if item.strip())
    if allowed is not None and any(item not in allowed for item in result):
        raise LineageError(f"{label} contains unsupported values; choose from: {', '.join(sorted(allowed))}")
    return result


def markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


class LineageEngine:
    def __init__(self, workspace: Workspace, state: dict[str, Any], lineage_map: dict[str, Any]):
        self.workspace = workspace
        self.root = workspace.meta / "lineage"
        self.state = state
        self.lineage_map = lineage_map

    @classmethod
    def initialize(cls, workspace_path: str) -> "LineageEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise LineageError("Cannot initialize lineage in an invalid workspace:\n- " + "\n- ".join(errors))
        if not any(atom.get("status") != "archived" for atom in workspace.atoms.values()):
            raise LineageError("Import a Knowledge Atom course before initializing lineage")
        root = workspace.meta / "lineage"
        if (root / "state.yaml").exists():
            raise LineageError("Knowledge lineage is already initialized")
        root.mkdir(parents=True, exist_ok=True)
        timestamp = iso()
        state = read_data(template_dir() / "lineage-state.yaml")
        state.update({"created_at": timestamp, "updated_at": timestamp})
        lineage_map = read_data(template_dir() / "lineage-map.yaml")
        write_yaml(root / "state.yaml", state)
        write_yaml(root / "map.yaml", lineage_map)
        atomic_text(root / "events.ndjson", "")
        engine = cls(workspace, state, lineage_map)
        engine.render()
        return engine

    @classmethod
    def load(cls, workspace_path: str) -> "LineageEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise LineageError("Cannot use lineage in an invalid workspace:\n- " + "\n- ".join(errors))
        root = workspace.meta / "lineage"
        if not (root / "state.yaml").is_file() or not (root / "map.yaml").is_file():
            raise LineageError("Knowledge lineage is not initialized; run `lineage init` first")
        state = read_data(root / "state.yaml")
        lineage_map = read_data(root / "map.yaml")
        if not isinstance(state, dict) or not isinstance(lineage_map, dict):
            raise LineageError("lineage state.yaml and map.yaml must each contain a mapping")
        return cls(workspace, state, lineage_map)

    @property
    def revision(self) -> int:
        value = self.state.get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise LineageError("lineage revision must be a non-negative integer")
        return value

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise LineageError(
                f"Stale lineage revision: expected {expected}, current is {self.revision}. Reload lineage status."
            )

    def _resolve_atom_id(self, atom_id: str) -> str | None:
        current = atom_id
        seen: set[str] = set()
        aliases = self.workspace.graph.get("aliases", {})
        while current in aliases and current not in seen:
            seen.add(current)
            current = aliases[current]
        atom = self.workspace.atoms.get(current)
        return current if atom and atom.get("status") != "archived" else None

    def _known_source_ids(self) -> set[str]:
        result = {
            source.get("id") for source in self.workspace.course.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("id"), str)
        }
        rag_sources = self.workspace.meta / "rag" / "sources.yaml"
        if rag_sources.is_file():
            registry = read_data(rag_sources)
            if not isinstance(registry, dict) or not isinstance(registry.get("sources"), list):
                raise LineageError("RAG source registry is malformed")
            result.update(
                source.get("id") for source in registry.get("sources", [])
                if isinstance(source, dict) and isinstance(source.get("id"), str)
            )
        result.add("synthesized")
        return result

    def _normalize_annotation(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != ANNOTATION_KEYS:
            raise LineageError(f"annotations[{index}] fields must be exactly: {', '.join(sorted(ANNOTATION_KEYS))}")
        atom_id = require_id(raw.get("atom_id"), f"annotations[{index}].atom_id")
        if atom_id not in self.workspace.atoms:
            raise LineageError(f"annotations[{index}] references unknown Atom: {atom_id}")
        roles = string_list(raw.get("roles"), f"{atom_id}.roles", allowed=ROLES, maximum=10)
        if not roles:
            raise LineageError(f"{atom_id}.roles must not be empty")
        boundaries = string_list(raw.get("boundaries", []), f"{atom_id}.boundaries", maximum=20)
        if any(len(item) > 1000 for item in boundaries):
            raise LineageError(f"{atom_id}.boundaries entries must be at most 1000 characters")
        return {
            "atom_id": atom_id,
            "roles": roles,
            "central_question": limited_text(raw.get("central_question"), f"{atom_id}.central_question", limit=1000),
            "contribution": limited_text(raw.get("contribution"), f"{atom_id}.contribution", limit=1000),
            "boundaries": boundaries,
        }

    def _normalize_relation(self, raw: Any, index: int, known_sources: set[str]) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != RELATION_KEYS:
            raise LineageError(f"relations[{index}] fields must be exactly: {', '.join(sorted(RELATION_KEYS))}")
        relation_id = require_id(raw.get("id"), f"relations[{index}].id")
        source = require_id(raw.get("from_atom_id"), f"{relation_id}.from_atom_id")
        target = require_id(raw.get("to_atom_id"), f"{relation_id}.to_atom_id")
        if source not in self.workspace.atoms or target not in self.workspace.atoms:
            raise LineageError(f"{relation_id} references an unknown Atom")
        if source == target:
            raise LineageError(f"{relation_id} cannot relate an Atom to itself")
        relation_type = raw.get("type")
        if relation_type not in RELATION_TYPES:
            raise LineageError(f"{relation_id}.type must be one of: {', '.join(sorted(RELATION_TYPES))}")
        confidence = round(float(require_number(raw.get("confidence"), f"{relation_id}.confidence", 0.5, 1)), 3)
        refs = raw.get("source_refs")
        if not isinstance(refs, list) or len(refs) > 20:
            raise LineageError(f"{relation_id}.source_refs must be a list with at most 20 entries")
        normalized_refs: list[dict[str, str]] = []
        seen_refs: set[tuple[str, str]] = set()
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict) or set(ref) != SOURCE_REF_KEYS:
                raise LineageError(f"{relation_id}.source_refs[{ref_index}] fields are invalid")
            source_id = require_id(ref.get("source_id"), f"{relation_id}.source_refs[{ref_index}].source_id")
            if source_id not in known_sources:
                raise LineageError(f"{relation_id} references unknown source: {source_id}")
            locator = limited_text(
                ref.get("locator"), f"{relation_id}.source_refs[{ref_index}].locator", limit=1000
            )
            signature = (source_id, locator)
            if signature not in seen_refs:
                normalized_refs.append({"source_id": source_id, "locator": locator})
                seen_refs.add(signature)
        if confidence > 0.7 and not normalized_refs:
            raise LineageError(f"{relation_id} needs a source reference when confidence exceeds 0.7")
        return {
            "id": relation_id,
            "from_atom_id": source,
            "to_atom_id": target,
            "type": relation_type,
            "rationale": limited_text(raw.get("rationale"), f"{relation_id}.rationale", limit=1500),
            "confidence": confidence,
            "source_refs": normalized_refs,
        }

    def _normalize_thread(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != THREAD_KEYS:
            raise LineageError(f"threads[{index}] fields must be exactly: {', '.join(sorted(THREAD_KEYS))}")
        thread_id = require_id(raw.get("id"), f"threads[{index}].id")
        kind = raw.get("kind")
        if kind not in THREAD_KINDS:
            raise LineageError(f"{thread_id}.kind must be one of: {', '.join(sorted(THREAD_KINDS))}")
        atom_ids = string_list(raw.get("atom_ids"), f"{thread_id}.atom_ids", maximum=100)
        if len(atom_ids) < 2:
            raise LineageError(f"{thread_id}.atom_ids must contain at least two unique Atoms")
        for atom_id in atom_ids:
            require_id(atom_id, f"{thread_id}.atom_ids")
            if atom_id not in self.workspace.atoms:
                raise LineageError(f"{thread_id} references unknown Atom: {atom_id}")
        return {
            "id": thread_id,
            "title": limited_text(raw.get("title"), f"{thread_id}.title", limit=500),
            "kind": kind,
            "goal": limited_text(raw.get("goal"), f"{thread_id}.goal", limit=1000),
            "atom_ids": atom_ids,
            "narrative": limited_text(raw.get("narrative"), f"{thread_id}.narrative", limit=2000),
            "confidence": round(float(require_number(raw.get("confidence"), f"{thread_id}.confidence", 0.5, 1)), 3),
        }

    def import_map(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"annotations", "relations", "threads"}:
            raise LineageError("lineage import payload must contain exactly annotations, relations, and threads")
        if any(not isinstance(payload.get(field), list) for field in ["annotations", "relations", "threads"]):
            raise LineageError("annotations, relations, and threads must be lists")
        if not any(payload[field] for field in ["annotations", "relations", "threads"]):
            raise LineageError("lineage import must contain at least one annotation, relation, or thread")
        known_sources = self._known_source_ids()
        annotations = [self._normalize_annotation(item, index) for index, item in enumerate(payload["annotations"])]
        relations = [
            self._normalize_relation(item, index, known_sources) for index, item in enumerate(payload["relations"])
        ]
        threads = [self._normalize_thread(item, index) for index, item in enumerate(payload["threads"])]
        annotation_ids = [item["atom_id"] for item in annotations]
        relation_ids = [item["id"] for item in relations]
        thread_ids = [item["id"] for item in threads]
        for label, values in [("annotation Atom", annotation_ids), ("relation", relation_ids), ("thread", thread_ids)]:
            if len(values) != len(set(values)):
                raise LineageError(f"lineage import contains duplicate {label} IDs")
        existing_relation_ids = {item.get("id") for item in self.lineage_map.get("relations", [])}
        existing_thread_ids = {item.get("id") for item in self.lineage_map.get("threads", [])}
        if set(relation_ids) & existing_relation_ids:
            raise LineageError("lineage import contains an already imported relation ID")
        if set(thread_ids) & existing_thread_ids:
            raise LineageError("lineage import contains an already imported thread ID")
        signatures = {
            self._relation_signature(item) for item in self.lineage_map.get("relations", []) if isinstance(item, dict)
        }
        for relation in relations:
            signature = self._relation_signature(relation)
            if signature in signatures:
                raise LineageError(f"duplicate semantic relation: {relation['id']}")
            signatures.add(signature)
        by_atom = {item["atom_id"]: item for item in self.lineage_map.get("annotations", [])}
        by_atom.update({item["atom_id"]: item for item in annotations})
        self.lineage_map["annotations"] = list(by_atom.values())
        self.lineage_map["relations"].extend(relations)
        self.lineage_map["threads"].extend(threads)
        self._commit(
            "lineage.map_imported",
            {"annotation_atom_ids": annotation_ids, "relation_ids": relation_ids, "thread_ids": thread_ids},
        )
        return {
            "updated_annotations": annotation_ids,
            "imported_relations": relation_ids,
            "imported_threads": thread_ids,
            "overview": self.overview("all"),
        }

    @staticmethod
    def _relation_signature(relation: dict[str, Any]) -> tuple[str, str, str]:
        source = str(relation.get("from_atom_id"))
        target = str(relation.get("to_atom_id"))
        relation_type = str(relation.get("type"))
        if relation_type in SYMMETRIC_RELATIONS and source > target:
            source, target = target, source
        return source, target, relation_type

    def _commit(self, event_type: str, details: dict[str, Any]) -> None:
        new_revision = self.revision + 1
        timestamp = iso()
        self.state["revision"] = new_revision
        self.state["updated_at"] = timestamp
        self.lineage_map["revision"] = new_revision
        write_yaml(self.root / "map.yaml", self.lineage_map)
        write_yaml(self.root / "state.yaml", self.state)
        event = {
            "event_id": f"levt-{new_revision:06d}",
            "revision": new_revision,
            "type": event_type,
            "at": timestamp,
            "course_revision": self.workspace.revision,
            "details": details,
        }
        with (self.root / "events.ndjson").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
        self.render()

    def events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        event_path = self.root / "events.ndjson"
        if not event_path.is_file():
            raise LineageError("lineage events.ndjson is missing")
        for line_number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LineageError(f"events.ndjson line {line_number} is invalid JSON") from exc
            if not isinstance(event, dict):
                raise LineageError(f"events.ndjson line {line_number} must be an object")
            events.append(event)
        return events

    def _active_graph(self) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
        active = [atom_id for atom_id, atom in self.workspace.atoms.items() if atom.get("status") != "archived"]
        active_set = set(active)
        predecessors = {atom_id: [] for atom_id in active}
        successors = {atom_id: [] for atom_id in active}
        indegree = {atom_id: 0 for atom_id in active}
        for atom_id in active:
            for prerequisite in self.workspace.atoms[atom_id].get("prerequisites", []):
                if prerequisite in active_set:
                    predecessors[atom_id].append(prerequisite)
                    successors[prerequisite].append(atom_id)
                    indegree[atom_id] += 1
        queue = deque(atom_id for atom_id in active if indegree[atom_id] == 0)
        order: list[str] = []
        while queue:
            atom_id = queue.popleft()
            order.append(atom_id)
            for successor in successors[atom_id]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        if len(order) != len(active):
            raise LineageError("Cannot derive lineage from a cyclic course graph")
        return order, predecessors, successors

    @staticmethod
    def _longest_path(
        order: list[str], predecessors: dict[str, list[str]], target: str | None = None
    ) -> list[str]:
        best: dict[str, list[str]] = {}
        for atom_id in order:
            candidates = [best[item] + [atom_id] for item in predecessors[atom_id]]
            best[atom_id] = max(candidates, key=lambda item: (len(item), tuple(item))) if candidates else [atom_id]
        if target is not None:
            return best.get(target, [])
        return max(best.values(), key=lambda item: (len(item), tuple(item))) if best else []

    def _annotation_index(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for annotation in self.lineage_map.get("annotations", []):
            resolved = self._resolve_atom_id(annotation["atom_id"])
            if resolved is None:
                continue
            if resolved not in result:
                result[resolved] = {**annotation, "atom_id": resolved}
            else:
                current = result[resolved]
                current["roles"] = unique(current["roles"] + annotation["roles"])
                current["boundaries"] = unique(current["boundaries"] + annotation["boundaries"])
                current["central_question"] = annotation["central_question"]
                current["contribution"] = annotation["contribution"]
        return result

    def _resolved_relations(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for relation in self.lineage_map.get("relations", []):
            source = self._resolve_atom_id(relation["from_atom_id"])
            target = self._resolve_atom_id(relation["to_atom_id"])
            if source is None or target is None or source == target:
                continue
            result.append({**relation, "from_atom_id": source, "to_atom_id": target})
        return result

    def _structure(self) -> dict[str, Any]:
        order, predecessors, successors = self._active_graph()
        roots = [item for item in order if not predecessors[item]]
        leaves = [item for item in order if not successors[item]]
        hubs = sorted(
            [
                {
                    "atom_id": atom_id,
                    "title": self.workspace.atoms[atom_id]["title"],
                    "incoming": len(predecessors[atom_id]),
                    "outgoing": len(successors[atom_id]),
                    "degree": len(predecessors[atom_id]) + len(successors[atom_id]),
                }
                for atom_id in order
                if len(predecessors[atom_id]) + len(successors[atom_id]) > 1
            ],
            key=lambda item: (-item["degree"], item["atom_id"]),
        )
        bridges: list[dict[str, str]] = []
        for source in order:
            for target in successors[source]:
                source_module = self.workspace.atoms[source].get("module", "Uncategorized")
                target_module = self.workspace.atoms[target].get("module", "Uncategorized")
                if source_module != target_module:
                    bridges.append(
                        {
                            "from_atom_id": source,
                            "to_atom_id": target,
                            "from_module": source_module,
                            "to_module": target_module,
                            "type": "prerequisite_for",
                        }
                    )
        for relation in self._resolved_relations():
            source = relation["from_atom_id"]
            target = relation["to_atom_id"]
            source_module = self.workspace.atoms[source].get("module", "Uncategorized")
            target_module = self.workspace.atoms[target].get("module", "Uncategorized")
            if relation["type"] == "bridges" or source_module != target_module:
                bridges.append(
                    {
                        "from_atom_id": source,
                        "to_atom_id": target,
                        "from_module": source_module,
                        "to_module": target_module,
                        "type": relation["type"],
                    }
                )
        modules: dict[str, dict[str, Any]] = {}
        for atom_id in order:
            atom = self.workspace.atoms[atom_id]
            module = atom.get("module", "Uncategorized")
            item = modules.setdefault(module, {"atom_ids": [], "status_counts": Counter()})
            item["atom_ids"].append(atom_id)
            item["status_counts"][atom.get("status")] += 1
        return {
            "atom_count": len(order),
            "edge_count": sum(len(values) for values in successors.values()),
            "root_atom_ids": roots,
            "leaf_atom_ids": leaves,
            "main_learning_spine": self._longest_path(order, predecessors),
            "hubs": hubs[:10],
            "branch_points": [item for item in hubs if item["outgoing"] > 1][:10],
            "convergence_points": [item for item in hubs if item["incoming"] > 1][:10],
            "bridges": bridges,
            "detailed_expansions": [
                {
                    "parent_atom_id": atom_id,
                    "child_atom_ids": list(atom["expansion"]["child_atom_ids"]),
                    "completed": atom["expansion"].get("completed_at") is not None,
                }
                for atom_id, atom in self.workspace.atoms.items()
                if atom.get("status") != "archived" and isinstance(atom.get("expansion"), dict)
            ],
            "modules": [
                {
                    "module": module,
                    "atom_count": len(item["atom_ids"]),
                    "atom_ids": item["atom_ids"],
                    "status_counts": dict(sorted(item["status_counts"].items())),
                }
                for module, item in modules.items()
            ],
            "topological_order": order,
        }

    def _learning_overlay(self, structure: dict[str, Any]) -> dict[str, Any]:
        status_counts = Counter(
            self.workspace.atoms[atom_id].get("status") for atom_id in structure["topological_order"]
        )
        return {
            "status_counts": dict(sorted(status_counts.items())),
            "active_atom_id": self.workspace.current.get("active_atom_id"),
            "available_atom_ids": [
                atom_id for atom_id in structure["topological_order"]
                if self.workspace.atoms[atom_id].get("status") == "available"
            ],
            "review_due_atom_ids": [
                atom_id for atom_id in structure["topological_order"]
                if self.workspace.atoms[atom_id].get("status") == "review_due"
            ],
            "skipped_atom_ids": [
                atom_id for atom_id in structure["topological_order"]
                if self.workspace.atoms[atom_id].get("status") == "skipped"
            ],
            "deferred_atom_ids": [
                atom_id for atom_id in structure["topological_order"]
                if self.workspace.atoms[atom_id].get("status") == "deferred"
            ],
            "expansion_focus_atom_id": self.workspace._expansion_next_atom_id(),
            "spine_status": [
                {"atom_id": atom_id, "status": self.workspace.atoms[atom_id].get("status")}
                for atom_id in structure["main_learning_spine"]
            ],
        }

    def _conceptual_overlay(self) -> dict[str, Any]:
        annotations = self._annotation_index()
        active_count = sum(atom.get("status") != "archived" for atom in self.workspace.atoms.values())
        resolved_threads: list[dict[str, Any]] = []
        for thread in self.lineage_map.get("threads", []):
            atom_ids = unique(
                resolved for atom_id in thread["atom_ids"]
                if (resolved := self._resolve_atom_id(atom_id)) is not None
            )
            if len(atom_ids) >= 2:
                resolved_threads.append({**thread, "atom_ids": atom_ids})
        return {
            "annotation_coverage": round(len(annotations) / max(1, active_count), 3),
            "annotations": list(annotations.values()),
            "relation_type_counts": dict(Counter(item["type"] for item in self._resolved_relations())),
            "relations": self._resolved_relations(),
            "threads": resolved_threads,
        }

    def _exam_overlay(self) -> dict[str, Any]:
        if not (self.workspace.meta / "exam" / "state.yaml").is_file():
            return {"enabled": False, "top_atoms": [], "coverage_gaps": []}
        from exam import ExamEngine, ExamError

        try:
            engine = ExamEngine.load(str(self.workspace.root))
            errors = engine.validate()
        except (ExamError, AtomLearnError) as exc:
            raise LineageError(f"Cannot load exam overlay: {exc}") from exc
        if errors:
            raise LineageError("Cannot use invalid exam state:\n- " + "\n- ".join(errors))
        if not engine.bank.get("questions"):
            return {"enabled": True, "top_atoms": [], "coverage_gaps": []}
        try:
            analysis = engine.analyze()
        except (ExamError, AtomLearnError) as exc:
            raise LineageError(f"Cannot analyze exam overlay: {exc}") from exc
        return {
            "enabled": True,
            "exam_revision": engine.revision,
            "top_atoms": analysis["atoms"][:10],
            "coverage_gaps": analysis["coverage_gaps"],
        }

    def _research_overlay(self) -> dict[str, Any]:
        if not (self.workspace.meta / "research" / "state.yaml").is_file():
            return {"enabled": False, "atom_demand": []}
        from research import ResearchEngine, ResearchError

        try:
            engine = ResearchEngine.load(str(self.workspace.root))
            errors = engine.validate()
        except (ResearchError, AtomLearnError) as exc:
            raise LineageError(f"Cannot load research overlay: {exc}") from exc
        if errors:
            raise LineageError("Cannot use invalid research state:\n- " + "\n- ".join(errors))
        demand: dict[str, dict[str, Any]] = {}
        for paper_id, paper in engine.papers.items():
            for source_atom_id in paper.get("concept_atom_ids", []):
                atom_id = self._resolve_atom_id(source_atom_id)
                if atom_id is None:
                    continue
                item = demand.setdefault(atom_id, {"atom_id": atom_id, "paper_ids": [], "paper_roles": Counter()})
                item["paper_ids"].append(paper_id)
                item["paper_roles"][paper.get("role")] += 1
        return {
            "enabled": True,
            "research_revision": engine.revision,
            "atom_demand": sorted(
                [
                    {
                        "atom_id": item["atom_id"],
                        "paper_count": len(unique(item["paper_ids"])),
                        "paper_ids": unique(item["paper_ids"]),
                        "paper_roles": dict(sorted(item["paper_roles"].items())),
                    }
                    for item in demand.values()
                ],
                key=lambda item: (-item["paper_count"], item["atom_id"]),
            ),
        }

    def overview(self, lens: str) -> dict[str, Any]:
        if lens not in LENSES:
            raise LineageError(f"lens must be one of: {', '.join(sorted(LENSES))}")
        structure = self._structure()
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "lineage_revision": self.revision,
            "course_revision": self.workspace.revision,
            "lens": lens,
            "generated_at": iso(),
        }
        if lens in {"all", "structure"}:
            result["structure"] = structure
        if lens in {"all", "learning"}:
            result["learning"] = self._learning_overlay(structure)
        if lens in {"all", "conceptual"}:
            result["conceptual"] = self._conceptual_overlay()
        if lens in {"all", "exam"}:
            result["exam"] = self._exam_overlay()
        if lens in {"all", "research"}:
            result["research"] = self._research_overlay()
        return result

    def trace(self, atom_id: str, depth: int) -> dict[str, Any]:
        require_id(atom_id, "atom_id")
        resolved = self._resolve_atom_id(atom_id)
        if resolved is None:
            raise LineageError(f"Unknown or unresolved archived Atom: {atom_id}")
        if depth < 1 or depth > 10:
            raise LineageError("depth must be from 1 through 10")
        order, predecessors, successors = self._active_graph()
        order_index = {item: index for index, item in enumerate(order)}

        def walk(adjacency: dict[str, list[str]]) -> list[dict[str, Any]]:
            seen = {resolved}
            queue = deque([(resolved, 0)])
            found: list[dict[str, Any]] = []
            while queue:
                current, distance = queue.popleft()
                if distance >= depth:
                    continue
                for neighbor in adjacency[current]:
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    found.append(
                        {
                            "atom_id": neighbor,
                            "title": self.workspace.atoms[neighbor]["title"],
                            "distance": distance + 1,
                            "status": self.workspace.atoms[neighbor].get("status"),
                        }
                    )
                    queue.append((neighbor, distance + 1))
            return sorted(found, key=lambda item: (item["distance"], order_index[item["atom_id"]]))

        ancestors = {item["atom_id"] for item in walk(predecessors)} | {resolved}
        target_order = [item for item in order if item in ancestors]
        target_predecessors = {
            item: [parent for parent in predecessors[item] if parent in ancestors] for item in target_order
        }
        main_path = self._longest_path(target_order, target_predecessors, resolved)
        relations = [
            relation for relation in self._resolved_relations()
            if resolved in {relation["from_atom_id"], relation["to_atom_id"]}
        ]
        threads = [
            thread for thread in self._conceptual_overlay()["threads"] if resolved in thread["atom_ids"]
        ]
        exam = self._exam_overlay()
        exam_atom = next((item for item in exam.get("top_atoms", []) if item["id"] == resolved), None)
        research = self._research_overlay()
        research_atom = next(
            (item for item in research.get("atom_demand", []) if item["atom_id"] == resolved), None
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "lineage_revision": self.revision,
            "course_revision": self.workspace.revision,
            "atom": {
                "atom_id": resolved,
                "title": self.workspace.atoms[resolved]["title"],
                "module": self.workspace.atoms[resolved].get("module"),
                "objective": self.workspace.atoms[resolved].get("objective"),
                "status": self.workspace.atoms[resolved].get("status"),
            },
            "main_prerequisite_path": main_path,
            "upstream": walk(predecessors),
            "downstream": walk(successors),
            "annotation": self._annotation_index().get(resolved),
            "semantic_relations": relations,
            "threads": threads,
            "exam_overlay": exam_atom,
            "research_overlay": research_atom,
        }

    def route(self, source_atom_id: str, target_atom_id: str) -> dict[str, Any]:
        source = self._resolve_atom_id(require_id(source_atom_id, "from_atom_id"))
        target = self._resolve_atom_id(require_id(target_atom_id, "to_atom_id"))
        if source is None or target is None:
            raise LineageError("route endpoints must resolve to active Knowledge Atoms")
        adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        order, _, successors = self._active_graph()
        # Prefer an explanatory semantic edge when it connects the same pair in the same number of steps.
        for relation in self._resolved_relations():
            from_atom = relation["from_atom_id"]
            to_atom = relation["to_atom_id"]
            edge = {
                "from_atom_id": from_atom,
                "to_atom_id": to_atom,
                "type": relation["type"],
                "relation_id": relation["id"],
            }
            adjacency[from_atom].append((to_atom, {**edge, "traversal": "forward"}))
            adjacency[to_atom].append((from_atom, {**edge, "traversal": "reverse"}))
        for from_atom in order:
            for to_atom in successors[from_atom]:
                edge = {"from_atom_id": from_atom, "to_atom_id": to_atom, "type": "prerequisite_for"}
                adjacency[from_atom].append((to_atom, {**edge, "traversal": "forward"}))
                adjacency[to_atom].append((from_atom, {**edge, "traversal": "reverse"}))
        queue = deque([source])
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        seen = {source}
        while queue and target not in seen:
            current = queue.popleft()
            for neighbor, edge in adjacency[current]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                previous[neighbor] = (current, edge)
                queue.append(neighbor)
        if target not in seen:
            return {
                "connected": False,
                "from_atom_id": source,
                "to_atom_id": target,
                "atom_ids": [],
                "steps": [],
            }
        nodes = [target]
        steps: list[dict[str, Any]] = []
        current = target
        while current != source:
            parent, edge = previous[current]
            steps.append({"from": parent, "to": current, **edge})
            nodes.append(parent)
            current = parent
        nodes.reverse()
        steps.reverse()
        return {
            "connected": True,
            "from_atom_id": source,
            "to_atom_id": target,
            "atom_ids": nodes,
            "steps": steps,
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        if set(self.state) != STATE_KEYS:
            errors.append("lineage state fields are invalid")
        if set(self.lineage_map) != MAP_KEYS:
            errors.append("lineage map fields are invalid")
        if self.state.get("schema_version") != SCHEMA_VERSION or self.lineage_map.get("schema_version") != SCHEMA_VERSION:
            errors.append("lineage state or map has unsupported schema_version")
        try:
            revision = self.revision
        except LineageError as exc:
            errors.append(str(exc))
            revision = -1
        map_revision = self.lineage_map.get("revision")
        if not isinstance(map_revision, int) or isinstance(map_revision, bool) or map_revision != revision:
            errors.append("lineage map revision does not match state revision")
        for field in ["created_at", "updated_at"]:
            try:
                value = self.state.get(field)
                if not isinstance(value, str):
                    raise AtomLearnError("timestamp must be a string")
                parse_time(value)
            except AtomLearnError:
                errors.append(f"lineage state {field} is invalid")
        annotations = self.lineage_map.get("annotations")
        relations = self.lineage_map.get("relations")
        threads = self.lineage_map.get("threads")
        if any(not isinstance(value, list) for value in [annotations, relations, threads]):
            errors.append("lineage annotations, relations, and threads must be lists")
            annotations, relations, threads = [], [], []
        normalized_annotations: list[dict[str, Any]] = []
        for index, annotation in enumerate(annotations):
            try:
                normalized = self._normalize_annotation(annotation, index)
                normalized_annotations.append(normalized)
                if normalized != annotation:
                    errors.append(f"annotation {index} does not match normalized schema")
            except (AtomLearnError, LineageError) as exc:
                errors.append(str(exc))
        annotation_ids = [item["atom_id"] for item in normalized_annotations]
        if len(annotation_ids) != len(set(annotation_ids)):
            errors.append("lineage contains duplicate annotation Atom IDs")
        known_sources = self._known_source_ids()
        normalized_relations: list[dict[str, Any]] = []
        for index, relation in enumerate(relations):
            try:
                normalized = self._normalize_relation(relation, index, known_sources)
                normalized_relations.append(normalized)
                if normalized != relation:
                    errors.append(f"relation {index} does not match normalized schema")
            except (AtomLearnError, LineageError) as exc:
                errors.append(str(exc))
        relation_ids = [item["id"] for item in normalized_relations]
        if len(relation_ids) != len(set(relation_ids)):
            errors.append("lineage contains duplicate relation IDs")
        signatures = [self._relation_signature(item) for item in normalized_relations]
        if len(signatures) != len(set(signatures)):
            errors.append("lineage contains duplicate semantic relations")
        normalized_threads: list[dict[str, Any]] = []
        for index, thread in enumerate(threads):
            try:
                normalized = self._normalize_thread(thread, index)
                normalized_threads.append(normalized)
                if normalized != thread:
                    errors.append(f"thread {index} does not match normalized schema")
            except (AtomLearnError, LineageError) as exc:
                errors.append(str(exc))
        thread_ids = [item["id"] for item in normalized_threads]
        if len(thread_ids) != len(set(thread_ids)):
            errors.append("lineage contains duplicate thread IDs")
        for label, records in [("annotation", normalized_annotations), ("relation", normalized_relations)]:
            atom_ids = (
                [item["atom_id"] for item in records]
                if label == "annotation"
                else [value for item in records for value in [item["from_atom_id"], item["to_atom_id"]]]
            )
            unresolved = sorted({item for item in atom_ids if self._resolve_atom_id(item) is None})
            if unresolved:
                errors.append(f"lineage {label} records reference unresolved archived Atoms: {', '.join(unresolved)}")
        unresolved_thread_atoms = sorted(
            {
                atom_id
                for thread in normalized_threads
                for atom_id in thread["atom_ids"]
                if self._resolve_atom_id(atom_id) is None
            }
        )
        if unresolved_thread_atoms:
            errors.append(
                "lineage thread records reference unresolved archived Atoms: "
                + ", ".join(unresolved_thread_atoms)
            )
        try:
            events = self.events()
        except LineageError as exc:
            errors.append(str(exc))
            events = []
        for index, event in enumerate(events, start=1):
            event_id = event.get("event_id")
            if set(event) != EVENT_KEYS:
                errors.append(f"lineage event {index} fields are invalid")
            event_revision = event.get("revision")
            if (
                event_id != f"levt-{index:06d}"
                or not isinstance(event_revision, int)
                or isinstance(event_revision, bool)
                or event_revision != index
            ):
                errors.append(f"lineage event {index} has an invalid ID or revision")
            if event.get("type") != "lineage.map_imported":
                errors.append(f"{event_id}: invalid lineage event type")
            try:
                value = event.get("at")
                if not isinstance(value, str):
                    raise AtomLearnError("timestamp must be a string")
                parse_time(value)
            except AtomLearnError:
                errors.append(f"{event_id}: invalid event timestamp")
            course_revision = event.get("course_revision")
            if (
                not isinstance(course_revision, int)
                or isinstance(course_revision, bool)
                or course_revision < 0
                or course_revision > self.workspace.revision
            ):
                errors.append(f"{event_id}: invalid course revision")
            details = event.get("details")
            expected_detail_keys = {"annotation_atom_ids", "relation_ids", "thread_ids"}
            if not isinstance(details, dict) or set(details) != expected_detail_keys:
                errors.append(f"{event_id}: invalid event details")
                continue
            for field in sorted(expected_detail_keys):
                values = details.get(field)
                if not isinstance(values, list) or any(not isinstance(item, str) for item in values) or len(values) != len(set(values)):
                    errors.append(f"{event_id}: invalid {field}")
        if len(events) != revision:
            errors.append("lineage event count does not match state revision")
        return unique(errors)

    def status(self) -> dict[str, Any]:
        errors = self.validate()
        structure = self._structure() if not errors else None
        return {
            "valid": not errors,
            "validation_errors": errors,
            "lineage_revision": self.revision,
            "course_revision": self.workspace.revision,
            "annotations": len(self.lineage_map.get("annotations", [])),
            "relations": len(self.lineage_map.get("relations", [])),
            "threads": len(self.lineage_map.get("threads", [])),
            "annotation_coverage": self._conceptual_overlay()["annotation_coverage"] if structure else 0.0,
            "root_atom_ids": structure["root_atom_ids"] if structure else [],
            "leaf_atom_ids": structure["leaf_atom_ids"] if structure else [],
        }

    def _diagram(self, structure: dict[str, Any]) -> list[str]:
        order = structure["topological_order"]
        selected = order if len(order) <= 40 else structure["main_learning_spine"]
        selected_set = set(selected)
        node_ids = {atom_id: f"n{index}" for index, atom_id in enumerate(selected, start=1)}
        lines = ["```mermaid", "flowchart TD"]
        for atom_id in selected:
            title = markdown(self.workspace.atoms[atom_id]["title"]).replace('"', "'")
            lines.append(f'    {node_ids[atom_id]}["{title}"]')
        for target in selected:
            for source in self.workspace.atoms[target].get("prerequisites", []):
                if source in selected_set:
                    lines.append(f"    {node_ids[source]} --> {node_ids[target]}")
        lines.append("```")
        if len(order) > 40:
            lines.extend(["", "_Diagram is limited to the main learning spine because the course has more than 40 Atoms._"])
        return lines

    def render(self) -> None:
        overview = self.overview("all")
        structure = overview["structure"]
        conceptual = overview["conceptual"]
        learning = overview["learning"]
        lines = [
            "# Knowledge Lineage", "", "> Generated by AtomLearn. Prerequisite edges remain canonical in the course DAG.", "",
            f"- Lineage revision: `{self.revision}`", f"- Course revision: `{self.workspace.revision}`",
            f"- Atoms: `{structure['atom_count']}`", f"- Prerequisite edges: `{structure['edge_count']}`",
            f"- Semantic annotation coverage: `{conceptual['annotation_coverage']}`", "", "## Structural Map", "",
        ]
        lines.extend(self._diagram(structure))
        lines.extend(["", "## Main Learning Spine", ""])
        lines.append(
            " -> ".join(
                f"`{atom_id}` ({self.workspace.atoms[atom_id].get('status')})"
                for atom_id in structure["main_learning_spine"]
            ) or "None"
        )
        lines.extend(["", "## Detailed Expansion Trees", ""])
        for expansion in structure["detailed_expansions"]:
            state = "completed" if expansion["completed"] else "in progress"
            lines.append(f"- `{expansion['parent_atom_id']}` ({state})")
            lines.extend(
                f"  {index}. `{child_id}`"
                for index, child_id in enumerate(expansion["child_atom_ids"], start=1)
            )
        if not structure["detailed_expansions"]:
            lines.append("- None")
        lines.extend(["", "## Modules", "", "| Module | Atoms | Status counts |", "| --- | ---: | --- |"])
        for module in structure["modules"]:
            status = ", ".join(f"{key}: {value}" for key, value in module["status_counts"].items())
            lines.append(f"| {markdown(module['module'])} | {module['atom_count']} | {markdown(status)} |")
        lines.extend(["", "## Hubs", ""])
        lines.extend(
            [
                f"- `{item['atom_id']}` {markdown(item['title'])} — incoming {item['incoming']}, outgoing {item['outgoing']}"
                for item in structure["hubs"]
            ] or ["- None"]
        )
        lines.extend(["", "## Cross-Module Bridges", ""])
        lines.extend(
            [
                f"- `{item['from_atom_id']}` --{item['type']}--> `{item['to_atom_id']}` "
                f"({markdown(item['from_module'])} -> {markdown(item['to_module'])})"
                for item in structure["bridges"]
            ] or ["- None"]
        )
        lines.extend(["", "## Curated Conceptual Threads", ""])
        for thread in conceptual["threads"]:
            lines.extend(
                [
                    f"### {markdown(thread['title'])}", "",
                    f"- Kind: `{thread['kind']}`", f"- Goal: {markdown(thread['goal'])}",
                    f"- Path: {' -> '.join(f'`{atom_id}`' for atom_id in thread['atom_ids'])}",
                    f"- Narrative: {markdown(thread['narrative'])}", "",
                ]
            )
        if not conceptual["threads"]:
            lines.append("- None; structural lineage is still available from the course DAG.")
        lines.extend(["", "## Semantic Relations", ""])
        lines.extend(
            [
                f"- `{item['from_atom_id']}` --{item['type']}--> `{item['to_atom_id']}`: {markdown(item['rationale'])}"
                for item in conceptual["relations"]
            ] or ["- None"]
        )
        lines.extend(["", "## Learning Overlay", ""])
        lines.append("- Status counts: " + markdown(learning["status_counts"]))
        lines.append(f"- Active Atom: `{learning['active_atom_id'] or 'none'}`")
        lines.append("- Provisionally skipped: " + markdown(learning["skipped_atom_ids"] or "None"))
        lines.append("- Deferred: " + markdown(learning["deferred_atom_ids"] or "None"))
        exam = overview["exam"]
        lines.extend(["", "## Exam Overlay", ""])
        lines.extend(
            [
                f"- `{item['id']}` — emphasis {item['emphasis_score']}, tier {item['corpus_tier']}"
                for item in exam.get("top_atoms", [])
            ] or ["- Not initialized or no mapped questions"]
        )
        research = overview["research"]
        lines.extend(["", "## Research Overlay", ""])
        lines.extend(
            [
                f"- `{item['atom_id']}` — required by {item['paper_count']} mapped papers"
                for item in research.get("atom_demand", [])
            ] or ["- Not initialized or no mapped papers"]
        )
        atomic_text(self.workspace.root / "KNOWLEDGE_LINEAGE.md", "\n".join(lines).rstrip() + "\n")


def add_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-lineage-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query multi-lens Knowledge Atom lineage maps")
    sub = parser.add_subparsers(dest="action", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("workspace")
    import_parser = sub.add_parser("import")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    add_revision(import_parser)
    for action in ["status", "validate", "render"]:
        command = sub.add_parser(action)
        command.add_argument("workspace")
    overview = sub.add_parser("overview")
    overview.add_argument("workspace")
    overview.add_argument("--lens", choices=sorted(LENSES), default="all")
    trace = sub.add_parser("trace")
    trace.add_argument("workspace")
    trace.add_argument("atom_id")
    trace.add_argument("--depth", type=int, default=3)
    route = sub.add_parser("route")
    route.add_argument("workspace")
    route.add_argument("from_atom_id")
    route.add_argument("to_atom_id")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "init":
        engine = LineageEngine.initialize(args.workspace)
        print(json.dumps({"ok": True, **engine.status()}, ensure_ascii=False, indent=2))
        return
    engine = LineageEngine.load(args.workspace)
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise LineageError("Lineage validation failed:\n- " + "\n- ".join(errors))
        print(json.dumps({"ok": True, "lineage_revision": engine.revision}))
        return
    errors = engine.validate()
    if errors:
        raise LineageError("Refusing to use invalid lineage state:\n- " + "\n- ".join(errors))
    if args.action == "import":
        engine.expect_revision(args.expected_lineage_revision)
        result = engine.import_map(read_data(Path(args.input)))
        print(json.dumps({"ok": True, "lineage_revision": engine.revision, "result": result}, ensure_ascii=False, indent=2))
    elif args.action == "status":
        print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
    elif args.action == "overview":
        print(json.dumps(engine.overview(args.lens), ensure_ascii=False, indent=2))
    elif args.action == "trace":
        print(json.dumps(engine.trace(args.atom_id, args.depth), ensure_ascii=False, indent=2))
    elif args.action == "route":
        print(json.dumps(engine.route(args.from_atom_id, args.to_atom_id), ensure_ascii=False, indent=2))
    elif args.action == "render":
        engine.render()
        print(json.dumps({"ok": True, "views": LINEAGE_VIEW_FILES}))
    else:  # pragma: no cover
        raise LineageError(f"Unhandled lineage action: {args.action}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (LineageError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
