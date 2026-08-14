#!/usr/bin/env python3
"""Source-traceable exam-question analysis and targeted preparation for AtomLearn."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from atomlearn import (
    MASTERY_LIKE,
    SATISFIED_STATUSES,
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


PAPER_KINDS = {"official_past_exam", "sample_exam", "mock_exam", "question_bank", "practice_set"}
QUESTION_TYPES = {
    "single_choice", "multiple_choice", "true_false", "short_answer", "calculation", "proof",
    "essay", "programming", "case_analysis", "other",
}
COGNITIVE_LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
MAPPING_BASES = {"direct", "solution_step", "prerequisite", "inferred"}
DIFFICULTY_BASES = {"official", "rubric", "estimated"}
PREPARATION_MODES = {"learning", "review", "mixed"}
DIFFICULTY_FACTORS = {
    "conceptual_load": 0.24,
    "reasoning_depth": 0.28,
    "knowledge_integration": 0.20,
    "execution_load": 0.18,
    "time_pressure": 0.10,
}
STATE_KEYS = {"schema_version", "revision", "title", "target_date", "created_at", "updated_at"}
BANK_KEYS = {"schema_version", "revision", "papers", "questions"}
PAPER_KEYS = {"id", "title", "year", "session", "kind", "total_points", "source_id", "locator"}
QUESTION_INPUT_KEYS = {
    "id", "paper_id", "number", "type", "points", "stem_summary", "source_locator", "family_id",
    "cognitive_levels", "tags", "difficulty", "knowledge_points",
}
KNOWLEDGE_POINT_KEYS = {"id", "label", "atom_id", "weight", "confidence", "basis"}
DIFFICULTY_INPUT_KEYS = set(DIFFICULTY_FACTORS) | {"basis", "confidence", "official_level"}
EVENT_KEYS = {"event_id", "revision", "type", "at", "course_revision", "details"}
EXAM_VIEW_FILES = ["EXAM_BLUEPRINT.md", "EXAM_STUDY_PLAN.md"]


class ExamError(RuntimeError):
    """A user-correctable exam analysis error."""


def template_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def limited_text(value: Any, label: str, *, allow_empty: bool = False, limit: int = 2000) -> str:
    result = require_string(value, label, allow_empty=allow_empty).strip()
    if len(result) > limit:
        raise ExamError(f"{label} must be at most {limit} characters; store a summary and source locator, not full text")
    return result


def optional_number(value: Any, label: str, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    return round(float(require_number(value, label, minimum, maximum)), 3)


def markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def difficulty_band(level: float) -> str:
    if level <= 1.8:
        return "foundation"
    if level <= 2.6:
        return "standard"
    if level <= 3.4:
        return "intermediate"
    if level <= 4.2:
        return "advanced"
    return "challenge"


class ExamEngine:
    def __init__(self, workspace: Workspace, state: dict[str, Any], bank: dict[str, Any]):
        self.workspace = workspace
        self.root = workspace.meta / "exam"
        self.state = state
        self.bank = bank

    @classmethod
    def initialize(cls, workspace_path: str, title: str, target_date: str | None) -> "ExamEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise ExamError("Cannot initialize exam analysis in an invalid workspace:\n- " + "\n- ".join(errors))
        root = workspace.meta / "exam"
        if (root / "state.yaml").exists():
            raise ExamError("Exam analysis is already initialized")
        root.mkdir(parents=True, exist_ok=True)
        state = read_data(template_dir() / "exam-state.yaml")
        timestamp = iso()
        state.update(
            {
                "title": limited_text(title, "exam title", limit=500),
                "target_date": cls._target_date(target_date),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        bank = read_data(template_dir() / "exam-bank.yaml")
        write_yaml(root / "state.yaml", state)
        write_yaml(root / "bank.yaml", bank)
        atomic_text(root / "events.ndjson", "")
        engine = cls(workspace, state, bank)
        engine.render()
        return engine

    @classmethod
    def load(cls, workspace_path: str) -> "ExamEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise ExamError("Cannot use exam analysis in an invalid workspace:\n- " + "\n- ".join(errors))
        root = workspace.meta / "exam"
        if not (root / "state.yaml").is_file() or not (root / "bank.yaml").is_file():
            raise ExamError("Exam analysis is not initialized; run `exam init` first")
        return cls(workspace, read_data(root / "state.yaml"), read_data(root / "bank.yaml"))

    @staticmethod
    def _target_date(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except (TypeError, ValueError) as exc:
            raise ExamError("target date must use YYYY-MM-DD") from exc

    @property
    def revision(self) -> int:
        value = self.state.get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ExamError("exam revision must be a non-negative integer")
        return value

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise ExamError(f"Stale exam revision: expected {expected}, current is {self.revision}. Reload exam status.")

    def events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        path = self.root / "events.ndjson"
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExamError(f"events.ndjson line {line_number} is invalid JSON") from exc
            if not isinstance(event, dict):
                raise ExamError(f"events.ndjson line {line_number} must be an object")
            result.append(event)
        return result

    def _normalize_paper(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ExamError(f"papers[{index}] must be a mapping")
        extra = sorted(set(raw) - PAPER_KEYS)
        if extra:
            raise ExamError(f"papers[{index}] contains unsupported fields: {', '.join(extra)}")
        paper_id = require_id(raw.get("id"), f"papers[{index}].id")
        year = raw.get("year")
        if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2200):
            raise ExamError(f"{paper_id}.year must be an integer from 1900 through 2200 or null")
        kind = raw.get("kind")
        if kind not in PAPER_KINDS:
            raise ExamError(f"{paper_id}.kind must be one of: {', '.join(sorted(PAPER_KINDS))}")
        return {
            "id": paper_id,
            "title": limited_text(raw.get("title"), f"{paper_id}.title", limit=500),
            "year": year,
            "session": limited_text(raw.get("session", ""), f"{paper_id}.session", allow_empty=True, limit=100),
            "kind": kind,
            "total_points": optional_number(raw.get("total_points"), f"{paper_id}.total_points", 0.01, 100000),
            "source_id": require_id(raw.get("source_id"), f"{paper_id}.source_id"),
            "locator": limited_text(raw.get("locator"), f"{paper_id}.locator", limit=1000),
        }

    def _normalize_mapping(self, raw: Any, label: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ExamError(f"{label} must be a mapping")
        if set(raw) != KNOWLEDGE_POINT_KEYS:
            raise ExamError(f"{label} fields must be exactly: {', '.join(sorted(KNOWLEDGE_POINT_KEYS))}")
        point_id = require_id(raw.get("id"), f"{label}.id")
        atom_id = raw.get("atom_id")
        if atom_id is not None:
            atom_id = require_id(atom_id, f"{label}.atom_id")
            if atom_id not in self.workspace.atoms:
                raise ExamError(f"{label}.atom_id is not in the course graph: {atom_id}")
        basis = raw.get("basis")
        if basis not in MAPPING_BASES:
            raise ExamError(f"{label}.basis must be one of: {', '.join(sorted(MAPPING_BASES))}")
        return {
            "id": point_id,
            "label": limited_text(raw.get("label"), f"{label}.label", limit=300),
            "atom_id": atom_id,
            "weight": round(float(require_number(raw.get("weight"), f"{label}.weight", 0.001, 1.0)), 3),
            "confidence": round(float(require_number(raw.get("confidence"), f"{label}.confidence", 0.5, 1.0)), 3),
            "basis": basis,
        }

    def _normalize_difficulty(self, raw: Any, label: str) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != DIFFICULTY_INPUT_KEYS:
            raise ExamError(f"{label} must contain exactly the rubric factors, basis, confidence, and official_level")
        basis = raw.get("basis")
        if basis not in DIFFICULTY_BASES:
            raise ExamError(f"{label}.basis must be one of: {', '.join(sorted(DIFFICULTY_BASES))}")
        factors = {
            key: round(float(require_number(raw.get(key), f"{label}.{key}", 1, 5)), 3)
            for key in DIFFICULTY_FACTORS
        }
        official = optional_number(raw.get("official_level"), f"{label}.official_level", 1, 5)
        if basis == "official" and official is None:
            raise ExamError(f"{label}.official_level is required when basis is official")
        estimated = round(sum(factors[key] * weight for key, weight in DIFFICULTY_FACTORS.items()), 3)
        effective = official if official is not None else estimated
        return {
            "basis": basis,
            **factors,
            "confidence": round(float(require_number(raw.get("confidence"), f"{label}.confidence", 0.5, 1)), 3),
            "official_level": official,
            "estimated_level": estimated,
            "effective_level": effective,
            "band": difficulty_band(effective),
        }

    def _normalize_question(self, raw: Any, index: int, paper_ids: set[str]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ExamError(f"questions[{index}] must be a mapping")
        extra = sorted(set(raw) - QUESTION_INPUT_KEYS)
        if extra:
            hint = "; store a concise stem_summary and source_locator, not full question text" if any(
                "text" in field or "stem" in field for field in extra
            ) else ""
            raise ExamError(f"questions[{index}] contains unsupported fields: {', '.join(extra)}{hint}")
        question_id = require_id(raw.get("id"), f"questions[{index}].id")
        paper_id = require_id(raw.get("paper_id"), f"{question_id}.paper_id")
        if paper_id not in paper_ids:
            raise ExamError(f"{question_id}.paper_id is not in the exam bank: {paper_id}")
        question_type = raw.get("type")
        if question_type not in QUESTION_TYPES:
            raise ExamError(f"{question_id}.type must be one of: {', '.join(sorted(QUESTION_TYPES))}")
        cognitive = raw.get("cognitive_levels")
        if not isinstance(cognitive, list) or not cognitive or any(item not in COGNITIVE_LEVELS for item in cognitive):
            raise ExamError(f"{question_id}.cognitive_levels must be a non-empty list of allowed levels")
        tags = raw.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags) or len(tags) > 20:
            raise ExamError(f"{question_id}.tags must be a string list with at most 20 entries")
        mappings = raw.get("knowledge_points")
        if not isinstance(mappings, list) or not mappings:
            raise ExamError(f"{question_id}.knowledge_points must be a non-empty list")
        normalized_mappings = [
            self._normalize_mapping(item, f"{question_id}.knowledge_points[{mapping_index}]")
            for mapping_index, item in enumerate(mappings)
        ]
        point_ids = [item["id"] for item in normalized_mappings]
        if len(point_ids) != len(set(point_ids)):
            raise ExamError(f"{question_id} contains duplicate knowledge-point IDs")
        if abs(sum(item["weight"] for item in normalized_mappings) - 1.0) > 0.001:
            raise ExamError(f"{question_id} knowledge-point weights must sum to 1.0")
        family_id = raw.get("family_id")
        if family_id is not None:
            family_id = require_id(family_id, f"{question_id}.family_id")
        return {
            "id": question_id,
            "paper_id": paper_id,
            "number": limited_text(raw.get("number"), f"{question_id}.number", limit=100),
            "type": question_type,
            "points": optional_number(raw.get("points"), f"{question_id}.points", 0.01, 100000),
            "stem_summary": limited_text(raw.get("stem_summary"), f"{question_id}.stem_summary", limit=1000),
            "source_locator": limited_text(raw.get("source_locator"), f"{question_id}.source_locator", limit=1000),
            "family_id": family_id,
            "cognitive_levels": unique(cognitive),
            "tags": unique(item.strip() for item in tags if item.strip()),
            "difficulty": self._normalize_difficulty(raw.get("difficulty"), f"{question_id}.difficulty"),
            "knowledge_points": normalized_mappings,
        }

    def import_bundle(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"papers", "questions"}:
            raise ExamError("exam import payload must contain exactly papers and questions")
        raw_papers = payload.get("papers")
        raw_questions = payload.get("questions")
        if not isinstance(raw_papers, list) or not isinstance(raw_questions, list) or not raw_questions:
            raise ExamError("papers must be a list and questions must be a non-empty list")
        existing_paper_ids = {item.get("id") for item in self.bank.get("papers", [])}
        existing_question_ids = {item.get("id") for item in self.bank.get("questions", [])}
        papers = [self._normalize_paper(item, index) for index, item in enumerate(raw_papers)]
        paper_ids = [item["id"] for item in papers]
        if len(paper_ids) != len(set(paper_ids)) or set(paper_ids) & existing_paper_ids:
            raise ExamError("exam import contains a duplicate or already imported paper ID")
        all_paper_ids = existing_paper_ids | set(paper_ids)
        questions = [self._normalize_question(item, index, all_paper_ids) for index, item in enumerate(raw_questions)]
        question_ids = [item["id"] for item in questions]
        if len(question_ids) != len(set(question_ids)) or set(question_ids) & existing_question_ids:
            raise ExamError("exam import contains a duplicate or already imported question ID")
        registry: dict[str, tuple[str, str | None]] = {}
        for question in [*self.bank.get("questions", []), *questions]:
            for mapping in question.get("knowledge_points", []):
                signature = (mapping.get("label"), mapping.get("atom_id"))
                previous = registry.setdefault(mapping.get("id"), signature)
                if previous != signature:
                    raise ExamError(
                        f"knowledge point {mapping.get('id')} must use one stable label and Atom mapping across the corpus"
                    )
        combined_questions = [*self.bank.get("questions", []), *questions]
        combined_papers = [*self.bank.get("papers", []), *papers]
        referenced_papers = {item["paper_id"] for item in combined_questions}
        unreferenced_papers = sorted(item["id"] for item in combined_papers if item["id"] not in referenced_papers)
        if unreferenced_papers:
            raise ExamError("papers without imported questions: " + ", ".join(unreferenced_papers))
        totals = {paper["id"]: paper.get("total_points") for paper in combined_papers}
        for paper_id, total in totals.items():
            known = sum(item.get("points") or 0 for item in combined_questions if item.get("paper_id") == paper_id)
            if total is not None and known > total + 0.001:
                raise ExamError(f"question points for {paper_id} exceed the paper total")
        self.bank["papers"] = combined_papers
        self.bank["questions"] = combined_questions
        self._commit("exam.questions_imported", {"paper_ids": paper_ids, "question_ids": question_ids})
        return {
            "imported_papers": paper_ids,
            "imported_questions": question_ids,
            "analysis": self.analyze(),
        }

    def _commit(self, event_type: str, details: dict[str, Any]) -> None:
        new_revision = self.revision + 1
        timestamp = iso()
        self.state["revision"] = new_revision
        self.state["updated_at"] = timestamp
        self.bank["revision"] = new_revision
        write_yaml(self.root / "bank.yaml", self.bank)
        write_yaml(self.root / "state.yaml", self.state)
        event = {
            "event_id": f"xevt-{new_revision:06d}",
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

    def _resolve_atom_id(self, atom_id: str | None) -> str | None:
        if atom_id is None:
            return None
        seen: set[str] = set()
        current = atom_id
        aliases = self.workspace.graph.get("aliases", {})
        while current in aliases and current not in seen:
            seen.add(current)
            current = aliases[current]
        atom = self.workspace.atoms.get(current)
        return current if atom and atom.get("status") != "archived" else None

    @staticmethod
    def _bucket(label: str, atom_id: str | None = None) -> dict[str, Any]:
        return {
            "label": label,
            "atom_id": atom_id,
            "question_ids": set(),
            "paper_ids": set(),
            "years": set(),
            "knowledge_point_ids": set(),
            "occurrence_mass": 0.0,
            "assigned_points": 0.0,
            "difficulty_sum": 0.0,
            "difficulty_weight": 0.0,
            "confidence_sum": 0.0,
            "confidence_weight": 0.0,
            "question_types": Counter(),
            "cognitive_levels": Counter(),
        }

    @staticmethod
    def _add_to_bucket(
        bucket: dict[str, Any], question: dict[str, Any], paper: dict[str, Any], mapping: dict[str, Any]
    ) -> None:
        weight = float(mapping["weight"])
        bucket["question_ids"].add(question["id"])
        bucket["paper_ids"].add(question["paper_id"])
        if paper.get("year") is not None:
            bucket["years"].add(paper["year"])
        bucket["knowledge_point_ids"].add(mapping["id"])
        bucket["occurrence_mass"] += weight
        if question.get("points") is not None:
            bucket["assigned_points"] += float(question["points"]) * weight
        bucket["difficulty_sum"] += float(question["difficulty"]["effective_level"]) * weight
        bucket["difficulty_weight"] += weight
        bucket["confidence_sum"] += float(mapping["confidence"]) * weight
        bucket["confidence_weight"] += weight
        bucket["question_types"][question["type"]] += weight
        for level in question["cognitive_levels"]:
            bucket["cognitive_levels"][level] += weight

    @staticmethod
    def _finalize_buckets(raw: dict[str, dict[str, Any]], paper_count: int, kind: str) -> list[dict[str, Any]]:
        total_mass = sum(item["occurrence_mass"] for item in raw.values()) or 1.0
        total_points = sum(item["assigned_points"] for item in raw.values())
        max_mass = max((item["occurrence_mass"] for item in raw.values()), default=1.0) or 1.0
        max_points = max((item["assigned_points"] for item in raw.values()), default=0.0)
        result: list[dict[str, Any]] = []
        for key, item in raw.items():
            frequency_relative = item["occurrence_mass"] / max_mass
            score_relative = item["assigned_points"] / max_points if max_points else frequency_relative
            paper_coverage = len(item["paper_ids"]) / max(1, paper_count)
            emphasis = min(1.0, 0.45 * paper_coverage + 0.30 * frequency_relative + 0.25 * score_relative)
            average_mapping_confidence = item["confidence_sum"] / max(item["confidence_weight"], 0.001)
            corpus_confidence = 0.6 * average_mapping_confidence + 0.4 * min(1.0, len(item["paper_ids"]) / 3)
            confidence_tier = (
                "high" if len(item["paper_ids"]) >= 3 and corpus_confidence >= 0.8
                else "medium" if len(item["paper_ids"]) >= 2 and corpus_confidence >= 0.65
                else "low"
            )
            commonness = "core" if emphasis >= 0.7 else "frequent" if emphasis >= 0.45 else "recurring" if emphasis >= 0.25 else "limited"
            record = {
                "id": key,
                "label": item["label"],
                "question_count": len(item["question_ids"]),
                "question_ids": sorted(item["question_ids"]),
                "paper_count": len(item["paper_ids"]),
                "paper_ids": sorted(item["paper_ids"]),
                "years": sorted(item["years"]),
                "frequency_share": round(item["occurrence_mass"] / total_mass, 3),
                "assigned_points": round(item["assigned_points"], 3),
                "score_share": round(item["assigned_points"] / total_points, 3) if total_points else None,
                "paper_coverage": round(paper_coverage, 3),
                "emphasis_score": round(emphasis, 3),
                "corpus_tier": commonness,
                "average_difficulty": round(item["difficulty_sum"] / max(item["difficulty_weight"], 0.001), 3),
                "mapping_confidence": round(average_mapping_confidence, 3),
                "corpus_confidence": round(corpus_confidence, 3),
                "confidence_tier": confidence_tier,
                "question_types": dict(sorted(item["question_types"].items())),
                "cognitive_levels": dict(sorted(item["cognitive_levels"].items())),
            }
            if kind == "knowledge_point":
                record["atom_id"] = item["atom_id"]
            else:
                record["knowledge_point_ids"] = sorted(item["knowledge_point_ids"])
            result.append(record)
        return sorted(result, key=lambda item: (-item["emphasis_score"], -item["paper_count"], item["id"]))

    def analyze(self) -> dict[str, Any]:
        questions = self.bank.get("questions", [])
        papers = self.bank.get("papers", [])
        if not questions:
            raise ExamError("No exam questions have been imported")
        paper_by_id = {item["id"]: item for item in papers}
        point_raw: dict[str, dict[str, Any]] = {}
        atom_raw: dict[str, dict[str, Any]] = {}
        mapped_weight = 0.0
        fully_mapped = 0
        partially_mapped = 0
        question_types: Counter[str] = Counter()
        cognitive_levels: Counter[str] = Counter()
        difficulty_distribution: Counter[str] = Counter()
        for question in questions:
            paper = paper_by_id[question["paper_id"]]
            question_types[question["type"]] += 1
            difficulty_distribution[question["difficulty"]["band"]] += 1
            cognitive_levels.update(question["cognitive_levels"])
            resolved_for_question: list[str | None] = []
            for mapping in question["knowledge_points"]:
                resolved = self._resolve_atom_id(mapping.get("atom_id"))
                resolved_for_question.append(resolved)
                point = point_raw.setdefault(mapping["id"], self._bucket(mapping["label"], resolved))
                if point["atom_id"] != resolved:
                    point["atom_id"] = None
                self._add_to_bucket(point, question, paper, mapping)
                if resolved is not None:
                    mapped_weight += float(mapping["weight"])
                    atom = self.workspace.atoms[resolved]
                    bucket = atom_raw.setdefault(resolved, self._bucket(atom["title"], resolved))
                    self._add_to_bucket(bucket, question, paper, mapping)
            if all(value is not None for value in resolved_for_question):
                fully_mapped += 1
            elif any(value is not None for value in resolved_for_question):
                partially_mapped += 1
        knowledge_points = self._finalize_buckets(point_raw, len(papers), "knowledge_point")
        atoms = self._finalize_buckets(atom_raw, len(papers), "atom")
        unmapped = [
            {"id": item["id"], "label": item["label"], "question_ids": item["question_ids"]}
            for item in knowledge_points
            if item.get("atom_id") is None
        ]
        rag_sources_path = self.workspace.meta / "rag" / "sources.yaml"
        rag_source_ids: set[str] = set()
        if rag_sources_path.is_file():
            registry = read_data(rag_sources_path)
            rag_source_ids = {
                item.get("id") for item in registry.get("sources", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        course_source_ids = {
            item.get("id") for item in self.workspace.course.get("sources", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        known_source_ids = rag_source_ids | course_source_ids
        unlinked_sources = sorted({item["source_id"] for item in papers if item["source_id"] not in known_source_ids})
        limitations = ["Corpus emphasis is descriptive and must not be presented as a prediction of future exam content."]
        if len(papers) < 3:
            limitations.append("Fewer than three papers were supplied; recurrence and trend confidence is low.")
        if any(item.get("year") is None for item in papers):
            limitations.append("Some papers have no year, so year-based recurrence cannot be interpreted reliably.")
        if any(item.get("points") is None for item in questions):
            limitations.append("Some questions have no points; score-share analysis is incomplete.")
        if unmapped:
            limitations.append("Unmapped knowledge points require course-graph repair before targeted preparation is complete.")
        if any(item["mapping_confidence"] < 0.7 for item in knowledge_points):
            limitations.append("Some knowledge-point mappings have low confidence and need source or marking-scheme review.")
        if unlinked_sources:
            limitations.append("Some exam sources are locator-only and have not been linked to the course or RAG source registry.")
        return {
            "schema_version": SCHEMA_VERSION,
            "exam_revision": self.revision,
            "course_revision": self.workspace.revision,
            "generated_at": iso(),
            "corpus": {
                "paper_count": len(papers),
                "question_count": len(questions),
                "years": sorted({item["year"] for item in papers if item.get("year") is not None}),
                "known_question_points": round(sum(item.get("points") or 0 for item in questions), 3),
                "fully_mapped_questions": fully_mapped,
                "partially_mapped_questions": partially_mapped,
                "unmapped_questions": len(questions) - fully_mapped - partially_mapped,
                "atom_mapping_coverage": round(mapped_weight / len(questions), 3),
            },
            "question_type_distribution": dict(sorted(question_types.items())),
            "cognitive_level_distribution": dict(sorted(cognitive_levels.items())),
            "difficulty_distribution": dict(sorted(difficulty_distribution.items())),
            "source_traceability": {
                "rag_linked_papers": sum(item["source_id"] in rag_source_ids for item in papers),
                "course_linked_papers": sum(item["source_id"] in course_source_ids for item in papers),
                "unlinked_source_ids": unlinked_sources,
            },
            "knowledge_points": knowledge_points,
            "atoms": atoms,
            "coverage_gaps": unmapped,
            "limitations": limitations,
        }

    def _gap_score(self, atom_id: str) -> tuple[float, str | None]:
        atom = self.workspace.atoms[atom_id]
        status = atom.get("status")
        confidence = float(atom.get("confidence") or 0.0)
        base = {
            "locked": 0.95,
            "available": 0.85,
            "active": max(0.45, 1 - confidence),
            "mastered": max(0.1, 1 - confidence),
            "review_due": max(0.45, 1 - confidence),
            "skipped": 0.55,
            "deferred": 0.9,
        }.get(status, 0.5)
        assessed = [
            item for item in self.workspace.evidence.get("items", [])
            if item.get("atom_id") == atom_id and item.get("result") in {"mastered", "partial", "not_mastered"}
        ]
        latest_result = assessed[-1].get("result") if assessed else None
        if latest_result == "not_mastered":
            base = max(base, 0.95)
        elif latest_result == "partial":
            base = max(base, 0.7)
        return round(min(1.0, base), 3), latest_result

    def _unmet_prerequisites(self, atom_id: str) -> list[str]:
        required: set[str] = set()
        ordered: list[str] = []

        def visit(current: str) -> None:
            for prerequisite in self.workspace.atoms[current].get("prerequisites", []):
                if self.workspace.atoms[prerequisite].get("status") not in SATISFIED_STATUSES and prerequisite not in required:
                    required.add(prerequisite)
                    visit(prerequisite)
                    ordered.append(prerequisite)

        visit(atom_id)
        return unique(ordered)

    def plan(self, mode: str, limit: int) -> dict[str, Any]:
        if mode not in PREPARATION_MODES:
            raise ExamError(f"mode must be one of: {', '.join(sorted(PREPARATION_MODES))}")
        if limit < 1 or limit > 100:
            raise ExamError("limit must be from 1 through 100")
        analysis = self.analyze()
        question_by_id = {item["id"]: item for item in self.bank["questions"]}
        candidates: list[dict[str, Any]] = []
        for item in analysis["atoms"]:
            atom_id = item["id"]
            atom = self.workspace.atoms[atom_id]
            status = atom.get("status")
            if status == "deferred":
                continue
            if mode == "learning" and status in SATISFIED_STATUSES:
                continue
            if mode == "review" and status not in {"active", "mastered", "review_due", "skipped"}:
                continue
            gap, latest_result = self._gap_score(atom_id)
            difficulty_component = max(0.0, min(1.0, (float(item["average_difficulty"]) - 1) / 4))
            priority = 0.50 * float(item["emphasis_score"]) + 0.35 * gap + 0.15 * difficulty_component
            if mode == "learning" and status not in SATISFIED_STATUSES:
                priority += 0.05
            if mode == "review" and status == "review_due":
                priority += 0.08
            prerequisites = self._unmet_prerequisites(atom_id)
            action = (
                "repair_prerequisites" if prerequisites
                else "verify_skip" if status == "skipped"
                else "review" if status in MASTERY_LIKE
                else "remediate" if status == "active" or latest_result in {"partial", "not_mastered"}
                else "learn"
            )
            question_ids = sorted(
                item["question_ids"],
                key=lambda question_id: (
                    question_by_id[question_id]["difficulty"]["effective_level"], question_id
                ),
            )
            candidates.append(
                {
                    "atom_id": atom_id,
                    "title": atom["title"],
                    "status": status,
                    "action": action,
                    "priority_score": round(min(1.0, priority), 3),
                    "exam_emphasis_score": item["emphasis_score"],
                    "corpus_tier": item["corpus_tier"],
                    "learner_gap_score": gap,
                    "latest_evidence_result": latest_result,
                    "provisional_skip": status == "skipped",
                    "average_difficulty": item["average_difficulty"],
                    "knowledge_point_ids": item["knowledge_point_ids"],
                    "prerequisite_atom_ids": prerequisites,
                    "representative_question_ids": question_ids[:3],
                    "evidence_basis": {
                        "paper_count": item["paper_count"],
                        "question_count": item["question_count"],
                        "assigned_points": item["assigned_points"],
                        "mapping_confidence": item["mapping_confidence"],
                    },
                }
            )
        candidates.sort(key=lambda item: (-item["priority_score"], item["atom_id"]))
        warnings = list(analysis["limitations"])
        target_date = self.state.get("target_date")
        days_remaining = (date.fromisoformat(target_date) - date.today()).days if target_date else None
        if days_remaining is not None and days_remaining < 0:
            warnings.append("The configured target date has passed; update the exam workspace before scheduling preparation.")
        elif days_remaining is not None and days_remaining <= 7:
            warnings.append("Seven or fewer days remain; preserve prerequisite guards and prioritize short diagnostic loops.")
        if any(item["provisional_skip"] for item in candidates):
            warnings.append(
                "Provisionally skipped exam-mapped Atoms are assumptions, not mastery; verify them when their corpus emphasis matters."
            )
        if any(self.workspace.atoms[item["id"]].get("status") == "deferred" for item in analysis["atoms"]):
            warnings.append("Deferred exam-mapped Atoms remain outside the queue until the learner restores them.")
        if not candidates:
            warnings.append(f"No mapped Atoms currently qualify for {mode} mode; use mixed mode or repair mappings.")
        return {
            "schema_version": SCHEMA_VERSION,
            "exam_revision": self.revision,
            "course_revision": self.workspace.revision,
            "mode": mode,
            "target_date": target_date,
            "days_remaining": days_remaining,
            "generated_at": iso(),
            "queue": candidates[:limit],
            "coverage_gaps": analysis["coverage_gaps"],
            "next_action": candidates[0] if candidates else None,
            "warnings": unique(warnings),
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        if set(self.state) != STATE_KEYS:
            errors.append("exam state fields are invalid")
        if set(self.bank) != BANK_KEYS:
            errors.append("exam bank fields are invalid")
        if self.state.get("schema_version") != SCHEMA_VERSION or self.bank.get("schema_version") != SCHEMA_VERSION:
            errors.append("exam state or bank has an unsupported schema_version")
        try:
            revision = self.revision
        except ExamError as exc:
            errors.append(str(exc))
            revision = -1
        bank_revision = self.bank.get("revision")
        if not isinstance(bank_revision, int) or isinstance(bank_revision, bool) or bank_revision != revision:
            errors.append("exam bank revision does not match state revision")
        try:
            limited_text(self.state.get("title"), "exam title", limit=500)
            self._target_date(self.state.get("target_date"))
        except (AtomLearnError, ExamError) as exc:
            errors.append(str(exc))
        for field in ["created_at", "updated_at"]:
            try:
                value = self.state.get(field)
                if not isinstance(value, str):
                    raise AtomLearnError("timestamp must be a string")
                parse_time(value)
            except AtomLearnError:
                errors.append(f"exam state {field} is invalid")
        papers = self.bank.get("papers")
        questions = self.bank.get("questions")
        if not isinstance(papers, list) or not isinstance(questions, list):
            errors.append("exam bank papers and questions must be lists")
            papers, questions = [], []
        normalized_papers: list[dict[str, Any]] = []
        for index, paper in enumerate(papers):
            try:
                normalized = self._normalize_paper(paper, index)
                normalized_papers.append(normalized)
                if paper != normalized:
                    errors.append(f"paper {index} does not match the normalized schema")
            except (AtomLearnError, ExamError) as exc:
                errors.append(str(exc))
        paper_ids = [item["id"] for item in normalized_papers]
        if len(paper_ids) != len(set(paper_ids)):
            errors.append("exam bank contains duplicate paper IDs")
        normalized_questions: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            raw = question
            if isinstance(question, dict) and isinstance(question.get("difficulty"), dict):
                raw = dict(question)
                raw["difficulty"] = {
                    key: value for key, value in question["difficulty"].items() if key in DIFFICULTY_INPUT_KEYS
                }
            try:
                normalized = self._normalize_question(raw, index, set(paper_ids))
                normalized_questions.append(normalized)
                if question != normalized:
                    errors.append(f"question {index} does not match the normalized schema")
            except (AtomLearnError, ExamError) as exc:
                errors.append(str(exc))
        question_ids = [item["id"] for item in normalized_questions]
        if len(question_ids) != len(set(question_ids)):
            errors.append("exam bank contains duplicate question IDs")
        referenced_papers = {item["paper_id"] for item in normalized_questions}
        unreferenced_papers = sorted(item["id"] for item in normalized_papers if item["id"] not in referenced_papers)
        if unreferenced_papers:
            errors.append("papers without imported questions: " + ", ".join(unreferenced_papers))
        registry: dict[str, tuple[str, str | None]] = {}
        for question in normalized_questions:
            for mapping in question["knowledge_points"]:
                signature = (mapping["label"], mapping["atom_id"])
                if mapping["id"] in registry and registry[mapping["id"]] != signature:
                    errors.append(f"knowledge point {mapping['id']} has inconsistent labels or Atom mappings")
                registry[mapping["id"]] = signature
        totals = {paper["id"]: paper.get("total_points") for paper in normalized_papers}
        for paper_id, total in totals.items():
            known = sum(item.get("points") or 0 for item in normalized_questions if item.get("paper_id") == paper_id)
            if total is not None and known > total + 0.001:
                errors.append(f"question points for {paper_id} exceed the paper total")
        try:
            events = self.events()
        except ExamError as exc:
            errors.append(str(exc))
            events = []
        imported_papers: set[str] = set()
        imported_questions: set[str] = set()
        for index, event in enumerate(events, start=1):
            event_id = event.get("event_id")
            if set(event) != EVENT_KEYS:
                errors.append(f"exam event {index} fields are invalid")
            event_revision = event.get("revision")
            if (
                event_id != f"xevt-{index:06d}"
                or not isinstance(event_revision, int)
                or isinstance(event_revision, bool)
                or event_revision != index
            ):
                errors.append(f"exam event {index} has an invalid ID or revision")
            if event.get("type") != "exam.questions_imported":
                errors.append(f"{event_id}: invalid exam event type")
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
            if not isinstance(details, dict) or set(details) != {"paper_ids", "question_ids"}:
                errors.append(f"{event_id}: invalid event details")
                continue
            for field, destination in [("paper_ids", imported_papers), ("question_ids", imported_questions)]:
                values = details.get(field)
                if not isinstance(values, list) or any(not isinstance(item, str) for item in values) or len(values) != len(set(values)):
                    errors.append(f"{event_id}: invalid {field}")
                else:
                    overlap = destination & set(values)
                    if overlap:
                        errors.append(f"{event_id}: {field} repeat earlier import records")
                    destination.update(values)
        if len(events) != revision:
            errors.append("exam event count does not match state revision")
        if imported_papers != set(paper_ids) or imported_questions != set(question_ids):
            errors.append("exam events do not account for every imported paper and question")
        return unique(errors)

    def status(self) -> dict[str, Any]:
        errors = self.validate()
        analysis = self.analyze() if self.bank.get("questions") and not errors else None
        return {
            "valid": not errors,
            "validation_errors": errors,
            "exam_revision": self.revision,
            "course_revision": self.workspace.revision,
            "title": self.state.get("title"),
            "target_date": self.state.get("target_date"),
            "paper_count": len(self.bank.get("papers", [])),
            "question_count": len(self.bank.get("questions", [])),
            "atom_mapping_coverage": analysis["corpus"]["atom_mapping_coverage"] if analysis else 0.0,
            "top_knowledge_points": analysis["knowledge_points"][:5] if analysis else [],
        }

    def render(self, mode: str = "mixed", limit: int = 10) -> None:
        if not self.bank.get("questions"):
            blueprint = [
                "# Exam Blueprint", "", "> Generated by AtomLearn from source-located question metadata.", "",
                f"- Exam revision: `{self.revision}`", "- No questions imported.",
            ]
            study = ["# Exam Study Plan", "", "- No questions imported."]
        else:
            analysis = self.analyze()
            plan = self.plan(mode, limit)
            corpus = analysis["corpus"]
            blueprint = [
                "# Exam Blueprint", "", "> Corpus emphasis is descriptive, not a prediction of future exam content.", "",
                f"- Exam revision: `{self.revision}`", f"- Papers: `{corpus['paper_count']}`",
                f"- Questions: `{corpus['question_count']}`", f"- Atom mapping coverage: `{corpus['atom_mapping_coverage']}`",
                "", "## Common Knowledge Points", "",
                "| Knowledge point | Atom | Tier | Papers | Questions | Score share | Difficulty | Confidence |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
            for item in analysis["knowledge_points"]:
                blueprint.append(
                    f"| `{item['id']}` {markdown(item['label'])} | `{item.get('atom_id') or 'unmapped'}` | "
                    f"{item['corpus_tier']} | {item['paper_count']} | {item['question_count']} | "
                    f"{item['score_share'] if item['score_share'] is not None else 'n/a'} | "
                    f"{item['average_difficulty']} | {item['confidence_tier']} |"
                )
            blueprint.extend(["", "## Coverage Gaps", ""])
            blueprint.extend(
                [f"- `{item['id']}` {markdown(item['label'])}: {', '.join(item['question_ids'])}" for item in analysis["coverage_gaps"]]
                or ["- None"]
            )
            blueprint.extend(["", "## Limitations", ""])
            blueprint.extend([f"- {markdown(item)}" for item in analysis["limitations"]])
            study = [
                "# Exam Study Plan", "", "> Generated from corpus emphasis, current learner Evidence, prerequisites, and difficulty.", "",
                f"- Mode: `{mode}`", f"- Exam revision: `{self.revision}`", f"- Course revision: `{self.workspace.revision}`",
                "", "## Priority Queue", "",
                "| Rank | Atom | Action | Priority | Exam emphasis | Learner gap | Difficulty | Questions |",
                "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
            for rank, item in enumerate(plan["queue"], start=1):
                study.append(
                    f"| {rank} | `{item['atom_id']}` {markdown(item['title'])} | {item['action']} | "
                    f"{item['priority_score']} | {item['exam_emphasis_score']} | {item['learner_gap_score']} | "
                    f"{item['average_difficulty']} | {', '.join(item['representative_question_ids'])} |"
                )
            if not plan["queue"]:
                study.append("| - | None | - | - | - | - | - | - |")
            study.extend(["", "## Warnings", ""])
            study.extend([f"- {markdown(item)}" for item in plan["warnings"]])
        atomic_text(self.workspace.root / "EXAM_BLUEPRINT.md", "\n".join(blueprint).rstrip() + "\n")
        atomic_text(self.workspace.root / "EXAM_STUDY_PLAN.md", "\n".join(study).rstrip() + "\n")


def add_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-exam-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze exam questions and generate targeted AtomLearn preparation")
    sub = parser.add_subparsers(dest="action", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("workspace")
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--target-date")
    import_parser = sub.add_parser("import")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    add_revision(import_parser)
    for action in ["status", "validate", "analyze", "render"]:
        command = sub.add_parser(action)
        command.add_argument("workspace")
    plan = sub.add_parser("plan")
    plan.add_argument("workspace")
    plan.add_argument("--mode", choices=sorted(PREPARATION_MODES), default="mixed")
    plan.add_argument("--limit", type=int, default=10)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "init":
        engine = ExamEngine.initialize(args.workspace, args.title, args.target_date)
        print(json.dumps({"ok": True, **engine.status()}, ensure_ascii=False, indent=2))
        return
    engine = ExamEngine.load(args.workspace)
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise ExamError("Exam validation failed:\n- " + "\n- ".join(errors))
        print(json.dumps({"ok": True, "exam_revision": engine.revision}))
        return
    errors = engine.validate()
    if errors:
        raise ExamError("Refusing to use invalid exam state:\n- " + "\n- ".join(errors))
    if args.action == "import":
        engine.expect_revision(args.expected_exam_revision)
        result = engine.import_bundle(read_data(Path(args.input)))
        print(json.dumps({"ok": True, "exam_revision": engine.revision, "result": result}, ensure_ascii=False, indent=2))
    elif args.action == "status":
        print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
    elif args.action == "analyze":
        result = engine.analyze()
        engine.render()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.action == "plan":
        result = engine.plan(args.mode, args.limit)
        engine.render(args.mode, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.action == "render":
        engine.render()
        print(json.dumps({"ok": True, "views": EXAM_VIEW_FILES}))
    else:  # pragma: no cover
        raise ExamError(f"Unhandled exam action: {args.action}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (ExamError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
