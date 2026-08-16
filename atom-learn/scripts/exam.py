#!/usr/bin/env python3
"""Source-traceable exam-question analysis and targeted preparation for AtomLearn."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import date, timedelta
from difflib import SequenceMatcher
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
from rag import RagEngine, RagError


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
STATE_KEYS = {"schema_version", "revision", "title", "target_date", "difficulty_calibration", "created_at", "updated_at"}
BANK_KEYS = {"schema_version", "revision", "papers", "questions", "families"}
PAPER_KEYS = {"id", "title", "year", "session", "kind", "total_points", "source_id", "locator"}
QUESTION_INPUT_KEYS = {
    "id", "paper_id", "number", "type", "points", "stem_summary", "source_locator", "family_id",
    "answer_locator", "marking_locator", "marking_link_status", "cognitive_levels", "tags", "difficulty",
    "knowledge_points", "family_candidate_ids",
}
KNOWLEDGE_POINT_KEYS = {"id", "label", "atom_id", "weight", "confidence", "basis"}
KNOWLEDGE_POINT_AUTO_KEYS = {
    "review_status", "candidate_atom_ids", "candidate_scores", "mapping_method",
    "evidence_locators", "rationale",
}
MAPPING_REVIEW_STATUSES = {"pending", "confirmed", "corrected", "rejected"}
MARKING_LINK_STATUSES = {"linked", "answer_only", "marking_only", "missing"}
DIFFICULTY_REQUIRED_INPUT_KEYS = set(DIFFICULTY_FACTORS) | {"basis", "confidence", "official_level"}
DIFFICULTY_OPTIONAL_INPUT_KEYS = {"empirical"}
DIFFICULTY_INPUT_KEYS = DIFFICULTY_REQUIRED_INPUT_KEYS | DIFFICULTY_OPTIONAL_INPUT_KEYS
DIFFICULTY_OUTPUT_KEYS = {
    "estimated_level", "calibrated_level", "calibration_offset", "effective_level", "band",
    "structural_complexity", "official_difficulty", "empirical_difficulty", "effective_basis",
    "uncertainty",
}
FAMILY_REVIEW_STATUSES = {"proposed", "confirmed", "corrected", "rejected"}
FAMILY_KEYS = {
    "id", "label", "question_ids", "fingerprint", "solution_signature", "similarity",
    "review_status", "proposal_method", "knowledge_point_ids", "transfer_evidence",
    "memorization_risk", "rationale",
}
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


QUESTION_PATTERNS = [
    re.compile(r"^\s*(?:question|q)\s*(\d+(?:\([a-z0-9]+\))?)\s*[:.)-]?\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*第\s*(\d+)\s*题\s*[:：、.]?\s*(.*)$"),
    re.compile(r"^\s*(\d+(?:\([a-z0-9]+\))?)[.)、]\s+(.*)$", re.IGNORECASE),
]


def question_header(line: str) -> tuple[str, str] | None:
    for pattern in QUESTION_PATTERNS:
        match = pattern.match(line)
        if match:
            return match.group(1), match.group(2)
    return None


def split_numbered_sections(value: str, label: str) -> list[dict[str, Any]]:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headers = [(index, match) for index, line in enumerate(lines) if (match := question_header(line))]
    if not headers:
        raise ExamError(f"{label} has no recognizable numbered question headings")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, (start, (number, remainder)) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        if number in seen:
            raise ExamError(f"{label} contains duplicate question number {number}")
        seen.add(number)
        text = "\n".join([remainder, *lines[start + 1 : end]]).strip()
        if not text:
            raise ExamError(f"{label} question {number} is empty")
        result.append({"number": number, "text": text, "line_start": start + 1, "line_end": end})
    return result


def transient_text(spec: Any, label: str, base_dir: Path) -> str:
    if isinstance(spec, str):
        return spec
    if not isinstance(spec, dict) or len({key for key in ["text", "path"] if key in spec}) != 1:
        raise ExamError(f"{label} must be text or a mapping with exactly one of text/path")
    if "text" in spec:
        return limited_text(spec["text"], label, limit=2_000_000)
    path = Path(str(spec["path"])).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        raise ExamError(f"{label} path is not a file: {path.resolve()}")
    if path.stat().st_size > 20_000_000:
        raise ExamError(f"{label} file exceeds 20 MB")
    return path.read_text(encoding="utf-8-sig")


def exam_tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    words = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]", text)
    stop = {"the", "and", "for", "with", "from", "that", "this", "question", "marks", "points", "using"}
    return {item for item in words if item not in stop}


def extract_points(value: str) -> float | None:
    totals = re.findall(r"[\[(]\s*(\d+(?:\.\d+)?)\s*(?:marks?|points?|分)\s*[\])]", value, re.IGNORECASE)
    if totals:
        return float(totals[-1])
    matches = re.findall(r"(?:\[|\()?\s*(\d+(?:\.\d+)?)\s*(?:marks?|points?|分)\s*(?:\]|\))?", value, re.IGNORECASE)
    values = [float(item) for item in matches]
    return round(sum(values), 3) if values else None


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
        state = read_data(root / "state.yaml")
        state.setdefault(
            "difficulty_calibration",
            {"offset": 0.0, "anchor_count": 0, "mae_before": None, "mae_after": None, "updated_at": None},
        )
        bank = read_data(root / "bank.yaml")
        bank.setdefault("families", [])
        return cls(workspace, state, bank)

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
        if not KNOWLEDGE_POINT_KEYS.issubset(raw) or set(raw) - KNOWLEDGE_POINT_KEYS - KNOWLEDGE_POINT_AUTO_KEYS:
            raise ExamError(f"{label} must contain the required mapping fields and only supported review metadata")
        point_id = require_id(raw.get("id"), f"{label}.id")
        atom_id = raw.get("atom_id")
        if atom_id is not None:
            atom_id = require_id(atom_id, f"{label}.atom_id")
            if atom_id not in self.workspace.atoms:
                raise ExamError(f"{label}.atom_id is not in the course graph: {atom_id}")
        basis = raw.get("basis")
        if basis not in MAPPING_BASES:
            raise ExamError(f"{label}.basis must be one of: {', '.join(sorted(MAPPING_BASES))}")
        review_status = raw.get("review_status", "confirmed")
        if review_status not in MAPPING_REVIEW_STATUSES:
            raise ExamError(f"{label}.review_status must be pending, confirmed, or corrected")
        candidates = raw.get("candidate_atom_ids", [atom_id] if atom_id else [])
        if not isinstance(candidates, list) or any(item not in self.workspace.atoms for item in candidates):
            raise ExamError(f"{label}.candidate_atom_ids must contain existing course Atoms")
        candidate_scores = raw.get("candidate_scores", [])
        if not isinstance(candidate_scores, list):
            raise ExamError(f"{label}.candidate_scores must be a list")
        normalized_scores: list[dict[str, Any]] = []
        for index, score in enumerate(candidate_scores):
            if not isinstance(score, dict) or set(score) != {"atom_id", "stem", "answer", "rubric", "joint"}:
                raise ExamError(f"{label}.candidate_scores[{index}] is invalid")
            candidate_id = require_id(score.get("atom_id"), f"{label}.candidate_scores[{index}].atom_id")
            if candidate_id not in self.workspace.atoms:
                raise ExamError(f"{label}.candidate_scores[{index}] references an unknown Atom")
            normalized_scores.append(
                {
                    "atom_id": candidate_id,
                    **{
                        field: round(float(require_number(score.get(field), f"{label}.candidate_scores[{index}].{field}", 0, 1)), 3)
                        for field in ["stem", "answer", "rubric", "joint"]
                    },
                }
            )
        evidence_locators = raw.get("evidence_locators", [])
        if not isinstance(evidence_locators, list) or any(not isinstance(item, str) for item in evidence_locators):
            raise ExamError(f"{label}.evidence_locators must be a string list")
        return {
            "id": point_id,
            "label": limited_text(raw.get("label"), f"{label}.label", limit=300),
            "atom_id": atom_id,
            "weight": round(float(require_number(raw.get("weight"), f"{label}.weight", 0.001, 1.0)), 3),
            "confidence": round(float(require_number(raw.get("confidence"), f"{label}.confidence", 0.5, 1.0)), 3),
            "basis": basis,
            "review_status": review_status,
            "candidate_atom_ids": unique(candidates),
            "candidate_scores": normalized_scores,
            "mapping_method": limited_text(
                raw.get("mapping_method", "human"), f"{label}.mapping_method", limit=100
            ),
            "evidence_locators": unique(item.strip() for item in evidence_locators if item.strip()),
            "rationale": limited_text(raw.get("rationale", ""), f"{label}.rationale", allow_empty=True, limit=1000),
        }

    def _normalize_empirical(self, raw: Any, label: str) -> dict[str, Any] | None:
        if raw is None:
            return None
        required = {"attempt_count", "correct_rate", "median_seconds", "discrimination", "irt_b", "source", "source_locator"}
        if not isinstance(raw, dict) or set(raw) != required:
            raise ExamError(f"{label} must contain exactly {', '.join(sorted(required))}")
        attempts = raw.get("attempt_count")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0 or attempts > 10_000_000:
            raise ExamError(f"{label}.attempt_count must be an integer from 0 through 10000000")
        correct_rate = optional_number(raw.get("correct_rate"), f"{label}.correct_rate", 0, 1)
        median_seconds = optional_number(raw.get("median_seconds"), f"{label}.median_seconds", 0, 1_000_000)
        discrimination = optional_number(raw.get("discrimination"), f"{label}.discrimination", -1, 1)
        irt_b = optional_number(raw.get("irt_b"), f"{label}.irt_b", -4, 4)
        if correct_rate is None and irt_b is None:
            raise ExamError(f"{label} requires correct_rate or irt_b")
        source = limited_text(raw.get("source"), f"{label}.source", limit=100)
        locator = limited_text(raw.get("source_locator"), f"{label}.source_locator", limit=1000)
        qualified = attempts >= 30
        level = round(
            min(5.0, max(1.0, 3.0 + 0.5 * irt_b)) if irt_b is not None
            else 1.0 + 4.0 * (1.0 - float(correct_rate)),
            3,
        )
        sampling_uncertainty = None
        if correct_rate is not None and attempts:
            sampling_uncertainty = round(
                min(2.0, 4.0 * 1.96 * math.sqrt(correct_rate * (1.0 - correct_rate) / attempts)), 3
            )
        return {
            "attempt_count": attempts,
            "correct_rate": correct_rate,
            "median_seconds": median_seconds,
            "discrimination": discrimination,
            "irt_b": irt_b,
            "source": source,
            "source_locator": locator,
            "level": level,
            "qualified": qualified,
            "sampling_uncertainty": sampling_uncertainty,
        }

    def _normalize_difficulty(self, raw: Any, label: str) -> dict[str, Any]:
        if (
            not isinstance(raw, dict)
            or not DIFFICULTY_REQUIRED_INPUT_KEYS.issubset(raw)
            or set(raw) - DIFFICULTY_INPUT_KEYS
        ):
            raise ExamError(f"{label} must contain the rubric factors, basis, confidence, official_level, and optional empirical aggregate")
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
        calibration = self.state.get("difficulty_calibration", {})
        offset = round(float(calibration.get("offset", 0.0)), 3)
        calibrated = round(min(5.0, max(1.0, estimated + offset)), 3)
        empirical_input = self._normalize_empirical(raw.get("empirical"), f"{label}.empirical")
        if empirical_input and empirical_input["qualified"]:
            effective = empirical_input["level"]
            effective_basis = "empirical"
            uncertainty = empirical_input["sampling_uncertainty"]
        elif official is not None:
            effective = official
            effective_basis = "official"
            uncertainty = 0.0
        else:
            effective = calibrated
            effective_basis = "structural_complexity"
            uncertainty = round(1.0 - float(raw.get("confidence")), 3)
        return {
            "basis": basis,
            **factors,
            "confidence": round(float(require_number(raw.get("confidence"), f"{label}.confidence", 0.5, 1)), 3),
            "official_level": official,
            "empirical": (
                {key: empirical_input[key] for key in [
                    "attempt_count", "correct_rate", "median_seconds", "discrimination", "irt_b", "source", "source_locator"
                ]}
                if empirical_input else None
            ),
            "estimated_level": estimated,
            "calibrated_level": calibrated,
            "calibration_offset": offset,
            "effective_level": effective,
            "band": difficulty_band(effective),
            "structural_complexity": {
                "level": calibrated,
                "raw_level": estimated,
                "confidence": round(float(raw.get("confidence")), 3),
                "factors": factors,
            },
            "official_difficulty": {"level": official, "available": official is not None},
            "empirical_difficulty": empirical_input,
            "effective_basis": effective_basis,
            "uncertainty": uncertainty,
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
        family_candidates = raw.get("family_candidate_ids", [])
        if not isinstance(family_candidates, list):
            raise ExamError(f"{question_id}.family_candidate_ids must be a list")
        marking_status = raw.get("marking_link_status", "missing")
        if marking_status not in MARKING_LINK_STATUSES:
            raise ExamError(f"{question_id}.marking_link_status is invalid")
        return {
            "id": question_id,
            "paper_id": paper_id,
            "number": limited_text(raw.get("number"), f"{question_id}.number", limit=100),
            "type": question_type,
            "points": optional_number(raw.get("points"), f"{question_id}.points", 0.01, 100000),
            "stem_summary": limited_text(raw.get("stem_summary"), f"{question_id}.stem_summary", limit=1000),
            "source_locator": limited_text(raw.get("source_locator"), f"{question_id}.source_locator", limit=1000),
            "family_id": family_id,
            "family_candidate_ids": unique(
                require_id(item, f"{question_id}.family_candidate_ids")
                for item in family_candidates
            ),
            "answer_locator": limited_text(
                raw.get("answer_locator", ""), f"{question_id}.answer_locator", allow_empty=True, limit=1000
            ),
            "marking_locator": limited_text(
                raw.get("marking_locator", ""), f"{question_id}.marking_locator", allow_empty=True, limit=1000
            ),
            "marking_link_status": marking_status,
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
        self._sync_declared_families()
        self._commit("exam.questions_imported", {"paper_ids": paper_ids, "question_ids": question_ids})
        return {
            "imported_papers": paper_ids,
            "imported_questions": question_ids,
            "analysis": self.analyze(),
        }

    def _normalize_family(self, raw: Any, index: int, question_ids: set[str]) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != FAMILY_KEYS:
            raise ExamError(f"families[{index}] must contain exactly the canonical family fields")
        family_id = require_id(raw.get("id"), f"families[{index}].id")
        members = raw.get("question_ids")
        if (
            not isinstance(members, list)
            or not members
            or len(members) != len(set(members))
            or any(item not in question_ids for item in members)
        ):
            raise ExamError(f"{family_id}.question_ids must be unique existing questions")
        status = raw.get("review_status")
        if status not in FAMILY_REVIEW_STATUSES:
            raise ExamError(f"{family_id}.review_status is invalid")
        transfer = raw.get("transfer_evidence")
        if transfer is not None:
            required = {"seen_attempts", "seen_success_rate", "held_out_attempts", "held_out_success_rate", "source_locator"}
            if not isinstance(transfer, dict) or set(transfer) != required:
                raise ExamError(f"{family_id}.transfer_evidence is invalid")
        risk = raw.get("memorization_risk")
        if risk not in {"unknown", "low", "high"}:
            raise ExamError(f"{family_id}.memorization_risk must be unknown, low, or high")
        knowledge = raw.get("knowledge_point_ids")
        if not isinstance(knowledge, list) or any(not isinstance(item, str) for item in knowledge):
            raise ExamError(f"{family_id}.knowledge_point_ids must be a string list")
        return {
            "id": family_id,
            "label": limited_text(raw.get("label"), f"{family_id}.label", limit=300),
            "question_ids": sorted(members),
            "fingerprint": limited_text(raw.get("fingerprint"), f"{family_id}.fingerprint", limit=200),
            "solution_signature": limited_text(raw.get("solution_signature"), f"{family_id}.solution_signature", limit=1000),
            "similarity": round(float(require_number(raw.get("similarity"), f"{family_id}.similarity", 0, 1)), 3),
            "review_status": status,
            "proposal_method": limited_text(raw.get("proposal_method"), f"{family_id}.proposal_method", limit=100),
            "knowledge_point_ids": sorted(unique(knowledge)),
            "transfer_evidence": transfer,
            "memorization_risk": risk,
            "rationale": limited_text(raw.get("rationale"), f"{family_id}.rationale", limit=1000),
        }

    def _sync_declared_families(self) -> None:
        families = {item["id"]: item for item in self.bank.get("families", [])}
        questions = self.bank.get("questions", [])
        declared: dict[str, list[dict[str, Any]]] = {}
        for question in questions:
            if question.get("family_id"):
                declared.setdefault(question["family_id"], []).append(question)
        for family_id, members in declared.items():
            if family_id in families:
                families[family_id]["question_ids"] = sorted(unique([
                    *families[family_id].get("question_ids", []), *(item["id"] for item in members)
                ]))
                families[family_id]["knowledge_point_ids"] = sorted(unique([
                    *families[family_id].get("knowledge_point_ids", []),
                    *(mapping["id"] for item in members for mapping in item["knowledge_points"]),
                ]))
                continue
            fingerprint = hashlib.sha256("|".join(sorted(item["id"] for item in members)).encode("utf-8")).hexdigest()[:12]
            knowledge_ids = sorted({mapping["id"] for item in members for mapping in item["knowledge_points"]})
            families[family_id] = {
                "id": family_id,
                "label": " / ".join(knowledge_ids[:3]) or family_id,
                "question_ids": sorted(item["id"] for item in members),
                "fingerprint": fingerprint,
                "solution_signature": "imported",
                "similarity": 1.0,
                "review_status": "confirmed",
                "proposal_method": "human-import",
                "knowledge_point_ids": knowledge_ids,
                "transfer_evidence": None,
                "memorization_risk": "unknown",
                "rationale": "Family identity was explicitly supplied by the import payload.",
            }
        self.bank["families"] = sorted(families.values(), key=lambda item: item["id"])

    def _auto_mappings(
        self,
        stem: str,
        answer: str,
        rubric: str,
        review_threshold: float,
        evidence_locators: list[str],
    ) -> list[dict[str, Any]]:
        signal_tokens = {
            "stem": exam_tokens(stem),
            "answer": exam_tokens(answer),
            "rubric": exam_tokens(rubric),
        }
        ranked: list[tuple[float, str, dict[str, float]]] = []
        for atom_id, atom in self.workspace.atoms.items():
            if atom.get("status") == "archived":
                continue
            title_tokens = exam_tokens(atom.get("title"))
            identifier_tokens = exam_tokens(atom_id.replace(".", " "))
            detail_tokens = exam_tokens(
                " ".join([str(atom.get("objective", "")), *atom.get("misconceptions", [])])
            )
            scores: dict[str, float] = {}
            for signal, query_tokens in signal_tokens.items():
                title_overlap = len(query_tokens & title_tokens) / max(1, len(title_tokens))
                identifier_overlap = len(query_tokens & identifier_tokens) / max(1, len(identifier_tokens))
                detail_overlap = len(query_tokens & detail_tokens) / max(1, min(len(detail_tokens), 12))
                exact = 1.0 if str(atom.get("title", "")).casefold() in {
                    "stem": stem, "answer": answer, "rubric": rubric
                }[signal].casefold() else 0.0
                scores[signal] = min(
                    1.0,
                    0.4 * title_overlap + 0.4 * identifier_overlap + 0.15 * detail_overlap + 0.05 * exact,
                )
            score = 0.5 * scores["stem"] + 0.3 * scores["answer"] + 0.2 * scores["rubric"]
            scores["joint"] = round(score, 3)
            if score > 0:
                ranked.append((score, atom_id, scores))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        candidates = ranked[:3]
        selected = [item for item in candidates if item[0] >= max(0.18, (candidates[0][0] * 0.72 if candidates else 1.0))]
        if not selected:
            label_tokens = sorted(set().union(*signal_tokens.values()))[:5]
            return [
                {
                    "id": "unmapped.auto",
                    "label": "Unmapped: " + (" ".join(label_tokens) or "question concept"),
                    "atom_id": None,
                    "weight": 1.0,
                    "confidence": 0.5,
                    "basis": "inferred",
                    "review_status": "pending",
                    "candidate_atom_ids": [item[1] for item in candidates],
                    "candidate_scores": [
                        {"atom_id": item[1], **{key: round(value, 3) for key, value in item[2].items()}}
                        for item in candidates
                    ],
                    "mapping_method": "joint-stem-answer-rubric-v1",
                    "evidence_locators": evidence_locators,
                    "rationale": "No candidate crossed the minimum joint evidence threshold; human review is required.",
                }
            ]
        total = sum(item[0] for item in selected)
        mappings = []
        for position, (score, atom_id, scores) in enumerate(selected):
            atom = self.workspace.atoms[atom_id]
            gap = score - (candidates[position + 1][0] if position + 1 < len(candidates) else 0.0)
            mappings.append(
                {
                    "id": f"auto.{atom_id}",
                    "label": atom["title"],
                    "atom_id": atom_id,
                    "weight": round(score / total, 3),
                    "confidence": round(max(0.5, min(1.0, 0.5 + 0.5 * score)), 3),
                    "basis": "inferred",
                    "review_status": "pending",
                    "candidate_atom_ids": [item[1] for item in candidates],
                    "candidate_scores": [
                        {"atom_id": item[1], **{key: round(value, 3) for key, value in item[2].items()}}
                        for item in candidates
                    ],
                    "mapping_method": "joint-stem-answer-rubric-v1",
                    "evidence_locators": evidence_locators,
                    "rationale": (
                        f"Joint score {score:.3f}; separation {gap:.3f}; automatic mappings remain proposed "
                        f"even when the configured review threshold {review_threshold:.3f} is crossed."
                    ),
                }
            )
        mappings[-1]["weight"] = round(1.0 - sum(item["weight"] for item in mappings[:-1]), 3)
        return mappings

    @staticmethod
    def _auto_question_type(text: str) -> str:
        lowered = text.casefold()
        if re.search(r"(?:^|\n)\s*[a-d][.)]", lowered):
            return "single_choice"
        if any(term in lowered for term in ["prove", "show that", "证明"]):
            return "proof"
        if any(term in lowered for term in ["write a program", "implement", "代码", "编程"]):
            return "programming"
        if any(term in lowered for term in ["calculate", "compute", "solve", "determine", "计算", "求解"]):
            return "calculation"
        if any(term in lowered for term in ["discuss", "evaluate", "critique", "论述", "评价"]):
            return "essay"
        return "short_answer"

    @staticmethod
    def _auto_cognitive_levels(text: str) -> list[str]:
        lowered = text.casefold()
        levels = []
        if any(term in lowered for term in ["define", "state", "list", "定义", "列出"]):
            levels.append("remember")
        if any(term in lowered for term in ["explain", "describe", "interpret", "解释", "说明"]):
            levels.append("understand")
        if any(term in lowered for term in ["apply", "calculate", "compute", "solve", "应用", "计算"]):
            levels.append("apply")
        if any(term in lowered for term in ["compare", "derive", "analyze", "prove", "比较", "推导", "分析", "证明"]):
            levels.append("analyze")
        if any(term in lowered for term in ["evaluate", "justify", "critique", "评价", "论证"]):
            levels.append("evaluate")
        if any(term in lowered for term in ["design", "create", "propose", "设计", "提出"]):
            levels.append("create")
        return unique(levels) or ["understand"]

    def _auto_difficulty(self, text: str, mappings: list[dict[str, Any]], points: float | None, linked: bool) -> dict[str, Any]:
        lowered = text.casefold()
        conceptual = min(5, 1 + len(mappings) + int(any(term in lowered for term in ["concept", "why", "证明", "解释"])))
        reasoning = 4 if any(term in lowered for term in ["prove", "derive", "evaluate", "证明", "推导", "评价"]) else 3 if any(
            term in lowered for term in ["analyze", "compare", "justify", "分析", "比较"]
        ) else 2
        integration = min(5, max(1, len(mappings) + int(" and " in lowered or "以及" in lowered)))
        visual_or_structured = int(any(term in lowered for term in ["[figure", "[image", "|", "table", "图", "表格"]))
        execution = min(5, max(1, 1 + len(text) // 300 + len(re.findall(r"[=+*/^]", text)) // 3 + visual_or_structured))
        time_pressure = min(5, max(1, int(math.ceil((points or 5) / 5))))
        return {
            "basis": "rubric",
            "conceptual_load": conceptual,
            "reasoning_depth": reasoning,
            "knowledge_integration": integration,
            "execution_load": execution,
            "time_pressure": time_pressure,
            "confidence": 0.8 if linked else 0.65,
            "official_level": None,
        }

    def process_documents(self, payload: Any, base_dir: Path) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"documents", "options"}:
            raise ExamError("exam process payload must contain documents and optional options")
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ExamError("exam process requires a non-empty documents list")
        options = payload.get("options", {})
        if not isinstance(options, dict) or set(options) - {"mapping_review_threshold"}:
            raise ExamError("exam process options are invalid")
        threshold = float(options.get("mapping_review_threshold", 0.85))
        if not 0.5 <= threshold <= 1.0:
            raise ExamError("mapping_review_threshold must be between 0.5 and 1.0")
        papers: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for document_index, document in enumerate(documents):
            if not isinstance(document, dict) or set(document) - {"paper", "questions", "answers", "marking_scheme"}:
                raise ExamError(f"documents[{document_index}] has unsupported fields")
            paper = document.get("paper")
            if not isinstance(paper, dict):
                raise ExamError(f"documents[{document_index}].paper must be a mapping")
            paper_id = require_id(paper.get("id"), f"documents[{document_index}].paper.id")
            question_sections = split_numbered_sections(
                transient_text(document.get("questions"), f"{paper_id}.questions", base_dir), f"{paper_id}.questions"
            )
            answer_sections = split_numbered_sections(
                transient_text(document["answers"], f"{paper_id}.answers", base_dir), f"{paper_id}.answers"
            ) if document.get("answers") is not None else []
            marking_sections = split_numbered_sections(
                transient_text(document["marking_scheme"], f"{paper_id}.marking_scheme", base_dir), f"{paper_id}.marking_scheme"
            ) if document.get("marking_scheme") is not None else []
            answers = {item["number"]: item for item in answer_sections}
            marking = {item["number"]: item for item in marking_sections}
            papers.append(paper)
            question_numbers = {item["number"] for item in question_sections}
            for section in question_sections:
                number = section["number"]
                block_ids = unique(
                    re.findall(r"\[document-ir-block:(block-[a-f0-9]{24})\]", section["text"])
                )
                question_text = re.sub(
                    r"^\s*\[document-ir-block:block-[a-f0-9]{24}\]\s*$",
                    "",
                    section["text"],
                    flags=re.MULTILINE,
                ).strip()
                answer = answers.get(number)
                scheme = marking.get(number)
                points = extract_points(scheme["text"] if scheme else question_text)
                slug = re.sub(r"[^a-z0-9]+", "-", number.casefold()).strip("-") or str(len(questions) + 1)
                question_id = f"{paper_id}.q{slug}"
                status = "linked" if answer and scheme else "answer_only" if answer else "marking_only" if scheme else "missing"
                answer_locator = f"answer lines {answer['line_start']}-{answer['line_end']}, question {number}" if answer else ""
                marking_locator = f"marking lines {scheme['line_start']}-{scheme['line_end']}, question {number}" if scheme else ""
                mappings = self._auto_mappings(
                    question_text,
                    answer["text"] if answer else "",
                    scheme["text"] if scheme else "",
                    threshold,
                    [item for item in [
                        f"{paper.get('locator')}, lines {section['line_start']}-{section['line_end']}, question {number}",
                        answer_locator,
                        marking_locator,
                    ] if item],
                )
                if mappings[0]["id"] == "unmapped.auto":
                    mappings[0]["id"] = f"unmapped.{question_id}"
                collapsed = re.sub(r"\s+", " ", question_text)
                collapsed = re.sub(r"(?:\[|\()?\s*\d+(?:\.\d+)?\s*(?:marks?|points?|分)\s*(?:\]|\))?", "", collapsed, flags=re.IGNORECASE).strip()
                questions.append(
                    {
                        "id": question_id,
                        "paper_id": paper_id,
                        "number": number,
                        "type": self._auto_question_type(question_text),
                        "points": points,
                        "stem_summary": collapsed[:500],
                        "source_locator": (
                            "document-ir blocks " + ", ".join(block_ids)
                            if block_ids
                            else f"{paper.get('locator')}, lines {section['line_start']}-{section['line_end']}, question {number}"
                        ),
                        "family_id": None,
                        "family_candidate_ids": [],
                        "answer_locator": answer_locator,
                        "marking_locator": marking_locator,
                        "marking_link_status": status,
                        "cognitive_levels": self._auto_cognitive_levels(question_text),
                        "tags": ["auto-split", "mapping-review-pending"] if any(item["review_status"] == "pending" for item in mappings) else ["auto-split"],
                        "difficulty": self._auto_difficulty(question_text, mappings, points, bool(scheme)),
                        "knowledge_points": mappings,
                    }
                )
            diagnostics.append(
                {
                    "paper_id": paper_id,
                    "question_count": len(question_sections),
                    "unmatched_answer_numbers": sorted(set(answers) - question_numbers),
                    "unmatched_marking_numbers": sorted(set(marking) - question_numbers),
                    "questions_without_answers": sorted(question_numbers - set(answers)),
                    "questions_without_marking": sorted(question_numbers - set(marking)),
                }
            )
        result = self.import_bundle({"papers": papers, "questions": questions})
        result["processing"] = diagnostics
        result["mapping_review_queue"] = self.mapping_review_queue()
        return result

    def process_source(
        self,
        source_id: str,
        *,
        paper_id: str,
        title: str | None,
        year: int | None,
        kind: str,
    ) -> dict[str, Any]:
        """Process the active revision of one RAG source through its shared Document IR."""
        document = RagEngine.load(str(self.workspace.root)).document_ir(source_id)
        rendered: list[str] = []
        used_blocks: list[str] = []
        for block in document["blocks"]:
            if block["kind"] in {"heading", "cell"}:
                continue
            text = block["text"]
            if block["kind"] in {"figure", "image"}:
                text = f"[{block['kind']} {block['block_id']}: {text or 'visual content; inspect the source region'}]"
            lines = text.splitlines()
            inserted = False
            for line in lines:
                rendered.append(line)
                if question_header(line):
                    rendered.append(f"[document-ir-block:{block['block_id']}]")
                    inserted = True
            if not inserted:
                rendered.append(f"[document-ir-block:{block['block_id']}]")
            used_blocks.append(block["block_id"])
        payload = {
            "documents": [
                {
                    "paper": {
                        "id": paper_id,
                        "title": title or document["title"],
                        "year": year,
                        "session": "",
                        "kind": kind,
                        "total_points": None,
                        "source_id": source_id,
                        "locator": f"document-ir:{source_id}@r{document['source_revision']}",
                    },
                    "questions": "\n".join(rendered),
                }
            ]
        }
        result = self.process_documents(payload, self.workspace.root)
        result["document_ir"] = {
            "source_id": source_id,
            "source_revision": document["source_revision"],
            "consumed_block_count": len(used_blocks),
            "content_sha256": document["content_sha256"],
        }
        return result

    def mapping_review_queue(self) -> list[dict[str, Any]]:
        return [
            {
                "question_id": question["id"],
                "number": question["number"],
                "mapping_id": mapping["id"],
                "label": mapping["label"],
                "proposed_atom_id": mapping["atom_id"],
                "candidate_atom_ids": mapping.get("candidate_atom_ids", []),
                "confidence": mapping["confidence"],
            }
            for question in self.bank.get("questions", [])
            for mapping in question.get("knowledge_points", [])
            if mapping.get("review_status") == "pending"
        ]

    def review_mappings(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("reviews"), list) or not payload["reviews"]:
            raise ExamError("mapping review payload must contain a non-empty reviews list")
        question_index = {item["id"]: item for item in self.bank.get("questions", [])}
        touched: set[str] = set()
        for index, review in enumerate(payload["reviews"]):
            if not isinstance(review, dict) or set(review) - {"question_id", "mapping_id", "decision", "atom_id", "knowledge_point_id", "label"}:
                raise ExamError(f"reviews[{index}] has unsupported fields")
            question_id = require_id(review.get("question_id"), f"reviews[{index}].question_id")
            question = question_index.get(question_id)
            if question is None:
                raise ExamError(f"mapping review question does not exist: {question_id}")
            mapping_id = require_id(review.get("mapping_id"), f"reviews[{index}].mapping_id")
            mapping = next((item for item in question["knowledge_points"] if item["id"] == mapping_id), None)
            if mapping is None:
                raise ExamError(f"mapping does not exist in {question_id}: {mapping_id}")
            decision = review.get("decision")
            if decision == "confirm":
                mapping["review_status"] = "confirmed"
                mapping["confidence"] = max(0.85, mapping["confidence"])
                mapping["mapping_method"] = "reviewed"
            elif decision in {"remap", "unmap", "reject"}:
                atom_id = review.get("atom_id") if decision == "remap" else None
                if decision == "remap":
                    atom_id = require_id(atom_id, f"reviews[{index}].atom_id")
                    if atom_id not in self.workspace.atoms:
                        raise ExamError(f"review Atom is not in the course graph: {atom_id}")
                mapping["atom_id"] = atom_id
                mapping["id"] = require_id(
                    review.get("knowledge_point_id") or (f"auto.{atom_id}" if atom_id else f"unmapped.{question_id}"),
                    f"reviews[{index}].knowledge_point_id",
                )
                mapping["label"] = limited_text(
                    review.get("label") or (self.workspace.atoms[atom_id]["title"] if atom_id else mapping["label"]),
                    f"reviews[{index}].label",
                    limit=300,
                )
                mapping["review_status"] = "rejected" if decision == "reject" else "corrected"
                mapping["confidence"] = 1.0
                mapping["basis"] = "inferred"
                mapping["candidate_atom_ids"] = unique([*mapping.get("candidate_atom_ids", []), *([atom_id] if atom_id else [])])
                mapping["mapping_method"] = "reviewed"
                mapping["rationale"] = "Explicitly rejected during review." if decision == "reject" else "Explicitly corrected during review."
            else:
                raise ExamError(f"reviews[{index}].decision must be confirm, remap, unmap, or reject")
            touched.add(question_id)
        registry: dict[str, tuple[str, str | None]] = {}
        for question in self.bank.get("questions", []):
            ids = [item["id"] for item in question.get("knowledge_points", [])]
            if len(ids) != len(set(ids)):
                raise ExamError(f"mapping review creates duplicate knowledge points in {question['id']}")
            for mapping in question.get("knowledge_points", []):
                signature = (mapping["label"], mapping["atom_id"])
                if mapping["id"] in registry and registry[mapping["id"]] != signature:
                    raise ExamError(f"mapping review makes knowledge point {mapping['id']} inconsistent across the corpus")
                registry[mapping["id"]] = signature
        return {"question_ids": sorted(touched), "review_count": len(payload["reviews"]), "remaining": len(self.mapping_review_queue())}

    def calibrate_difficulty(self) -> dict[str, Any]:
        anchors = [
            question["difficulty"] for question in self.bank.get("questions", [])
            if question.get("difficulty", {}).get("official_level") is not None
        ]
        if not anchors:
            raise ExamError("difficulty calibration requires at least one question with an official_level")
        residuals = [item["official_level"] - item["estimated_level"] for item in anchors]
        offset = round(sum(residuals) / len(residuals), 3)
        before = round(sum(abs(item) for item in residuals) / len(residuals), 3)
        after = round(sum(abs(item - offset) for item in residuals) / len(residuals), 3)
        self.state["difficulty_calibration"] = {
            "offset": offset,
            "anchor_count": len(anchors),
            "mae_before": before,
            "mae_after": after,
            "updated_at": iso(),
        }
        for question in self.bank.get("questions", []):
            question["difficulty"] = self._normalize_difficulty(
                {key: question["difficulty"][key] for key in DIFFICULTY_INPUT_KEYS},
                f"{question['id']}.difficulty",
            )
        return {"offset": offset, "anchor_count": len(anchors), "mae_before": before, "mae_after": after}

    def record_empirical_difficulty(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"aggregates"}:
            raise ExamError("empirical difficulty payload must contain exactly aggregates")
        aggregates = payload.get("aggregates")
        if not isinstance(aggregates, list) or not aggregates:
            raise ExamError("empirical difficulty aggregates must be a non-empty list")
        questions = {item["id"]: item for item in self.bank.get("questions", [])}
        updated: list[str] = []
        qualified: list[str] = []
        for index, aggregate in enumerate(aggregates):
            if not isinstance(aggregate, dict) or set(aggregate) != {
                "question_id", "attempt_count", "correct_rate", "median_seconds", "discrimination",
                "irt_b", "source", "source_locator",
            }:
                raise ExamError(f"aggregates[{index}] has invalid fields")
            question_id = require_id(aggregate.get("question_id"), f"aggregates[{index}].question_id")
            if question_id not in questions:
                raise ExamError(f"empirical aggregate question does not exist: {question_id}")
            raw = {key: value for key, value in questions[question_id]["difficulty"].items() if key in DIFFICULTY_INPUT_KEYS}
            raw["empirical"] = {key: value for key, value in aggregate.items() if key != "question_id"}
            questions[question_id]["difficulty"] = self._normalize_difficulty(raw, f"{question_id}.difficulty")
            updated.append(question_id)
            if questions[question_id]["difficulty"]["empirical_difficulty"]["qualified"]:
                qualified.append(question_id)
        return {
            "question_ids": updated,
            "qualified_question_ids": qualified,
            "minimum_attempts": 30,
            "effective_priority": ["qualified_empirical", "official", "structural_complexity"],
        }

    @staticmethod
    def _family_fingerprint(question: dict[str, Any]) -> tuple[str, set[str], str]:
        normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", question.get("stem_summary", "").casefold())
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff<>]+", " ", normalized).strip()
        tokens = exam_tokens(normalized)
        solution = "|".join(
            [
                question.get("type", "other"),
                ",".join(sorted(question.get("cognitive_levels", []))),
                ",".join(sorted(
                    item.get("atom_id")
                    or "+".join(item.get("candidate_atom_ids", [])[:3])
                    or item.get("id", "")
                    for item in question.get("knowledge_points", [])
                )),
            ]
        )
        digest = hashlib.sha256(f"{normalized}|{solution}".encode("utf-8")).hexdigest()[:16]
        return digest, tokens, solution

    def propose_families(self, threshold: float = 0.62) -> dict[str, Any]:
        if not 0.5 <= threshold <= 1.0:
            raise ExamError("family similarity threshold must be between 0.5 and 1.0")
        questions = [item for item in self.bank.get("questions", []) if not item.get("family_id")]
        if len(questions) < 2:
            raise ExamError("family proposal requires at least two questions")
        fingerprints = {item["id"]: self._family_fingerprint(item) for item in questions}
        parents = {item["id"]: item["id"] for item in questions}

        def find(question_id: str) -> str:
            while parents[question_id] != question_id:
                parents[question_id] = parents[parents[question_id]]
                question_id = parents[question_id]
            return question_id

        def union(left: str, right: str) -> None:
            a, b = find(left), find(right)
            if a != b:
                parents[max(a, b)] = min(a, b)

        pair_scores: dict[tuple[str, str], float] = {}
        for left_index, left in enumerate(questions):
            for right in questions[left_index + 1 :]:
                if left["paper_id"] == right["paper_id"]:
                    continue
                _, left_tokens, left_solution = fingerprints[left["id"]]
                _, right_tokens, right_solution = fingerprints[right["id"]]
                lexical = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
                sequence = SequenceMatcher(None, left["stem_summary"].casefold(), right["stem_summary"].casefold()).ratio()
                left_points = {item["id"] for item in left["knowledge_points"] if item.get("review_status") != "rejected"}
                right_points = {item["id"] for item in right["knowledge_points"] if item.get("review_status") != "rejected"}
                knowledge = len(left_points & right_points) / max(1, len(left_points | right_points))
                structure = 1.0 if left_solution == right_solution else 0.5 if left["type"] == right["type"] else 0.0
                score = round(0.25 * lexical + 0.2 * sequence + 0.35 * knowledge + 0.2 * structure, 3)
                pair_scores[(left["id"], right["id"])] = score
                if score >= threshold:
                    union(left["id"], right["id"])
        groups: dict[str, list[str]] = {}
        for question in questions:
            groups.setdefault(find(question["id"]), []).append(question["id"])
        existing = {item["id"]: item for item in self.bank.get("families", [])}
        proposals: list[dict[str, Any]] = []
        for question_ids in sorted(groups.values()):
            if len(question_ids) < 2:
                continue
            digest = hashlib.sha256("|".join(sorted(question_ids)).encode("utf-8")).hexdigest()[:12]
            family_id = f"family.auto.{digest}"
            scores = [
                score for pair, score in pair_scores.items() if pair[0] in question_ids and pair[1] in question_ids
            ]
            knowledge_ids = sorted({
                mapping["id"]
                for question in questions if question["id"] in question_ids
                for mapping in question["knowledge_points"] if mapping.get("review_status") != "rejected"
            })
            family = {
                "id": family_id,
                "label": " / ".join(knowledge_ids[:3]) or family_id,
                "question_ids": sorted(question_ids),
                "fingerprint": digest,
                "solution_signature": fingerprints[question_ids[0]][2],
                "similarity": round(sum(scores) / max(1, len(scores)), 3),
                "review_status": "proposed",
                "proposal_method": "normalized-stem-knowledge-solution-v1",
                "knowledge_point_ids": knowledge_ids,
                "transfer_evidence": None,
                "memorization_risk": "unknown",
                "rationale": "Cross-paper candidate from normalized stem, mapped knowledge, and solution structure; confirmation is required.",
            }
            existing[family_id] = family
            proposals.append(family)
            for question in questions:
                if question["id"] in question_ids:
                    question["family_candidate_ids"] = unique([*question.get("family_candidate_ids", []), family_id])
        self.bank["families"] = sorted(existing.values(), key=lambda item: item["id"])
        return {"proposed_family_ids": [item["id"] for item in proposals], "threshold": threshold, "pair_count": len(pair_scores)}

    def review_families(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"reviews"} or not isinstance(payload.get("reviews"), list):
            raise ExamError("family review payload must contain exactly a reviews list")
        families = {item["id"]: item for item in self.bank.get("families", [])}
        questions = {item["id"]: item for item in self.bank.get("questions", [])}
        reviewed: list[str] = []
        for index, review in enumerate(payload["reviews"]):
            if not isinstance(review, dict) or set(review) - {"family_id", "decision", "canonical_id", "label", "transfer_evidence"}:
                raise ExamError(f"reviews[{index}] has unsupported fields")
            family_id = require_id(review.get("family_id"), f"reviews[{index}].family_id")
            family = families.get(family_id)
            if family is None:
                raise ExamError(f"family does not exist: {family_id}")
            decision = review.get("decision")
            if decision not in {"confirm", "correct", "reject"}:
                raise ExamError(f"reviews[{index}].decision must be confirm, correct, or reject")
            canonical_id = family_id
            if decision == "correct":
                canonical_id = require_id(review.get("canonical_id"), f"reviews[{index}].canonical_id")
                if canonical_id != family_id and canonical_id in families:
                    raise ExamError(f"corrected family ID already exists: {canonical_id}")
                family["id"] = canonical_id
                family["label"] = limited_text(review.get("label") or family["label"], f"reviews[{index}].label", limit=300)
                family["review_status"] = "corrected"
                families.pop(family_id)
                families[canonical_id] = family
            elif decision == "confirm":
                family["review_status"] = "confirmed"
            else:
                family["review_status"] = "rejected"
            transfer = review.get("transfer_evidence")
            if transfer is not None:
                required = {"seen_attempts", "seen_success_rate", "held_out_attempts", "held_out_success_rate", "source_locator"}
                if not isinstance(transfer, dict) or set(transfer) != required:
                    raise ExamError(f"reviews[{index}].transfer_evidence is invalid")
                for field in ["seen_attempts", "held_out_attempts"]:
                    value = transfer[field]
                    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1_000_000:
                        raise ExamError(f"reviews[{index}].transfer_evidence.{field} must be a non-negative integer")
                normalized_transfer = {
                    "seen_attempts": transfer["seen_attempts"],
                    "seen_success_rate": round(float(require_number(transfer["seen_success_rate"], "seen_success_rate", 0, 1)), 3),
                    "held_out_attempts": transfer["held_out_attempts"],
                    "held_out_success_rate": round(float(require_number(transfer["held_out_success_rate"], "held_out_success_rate", 0, 1)), 3),
                    "source_locator": limited_text(transfer["source_locator"], "transfer source_locator", limit=1000),
                }
                family["transfer_evidence"] = normalized_transfer
                if normalized_transfer["seen_attempts"] >= 3 and normalized_transfer["held_out_attempts"] >= 3:
                    gap = normalized_transfer["seen_success_rate"] - normalized_transfer["held_out_success_rate"]
                    family["memorization_risk"] = "high" if gap >= 0.2 else "low"
            for question_id in family["question_ids"]:
                question = questions[question_id]
                question["family_candidate_ids"] = [item for item in question.get("family_candidate_ids", []) if item != family_id]
                if decision != "reject":
                    question["family_id"] = canonical_id
                elif question.get("family_id") in {family_id, canonical_id}:
                    question["family_id"] = None
            reviewed.append(canonical_id)
        self.bank["families"] = sorted(families.values(), key=lambda item: item["id"])
        return {"family_ids": reviewed, "remaining_proposals": sum(item["review_status"] == "proposed" for item in families.values())}

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
                resolved = (
                    self._resolve_atom_id(mapping.get("atom_id"))
                    if mapping.get("review_status") in {"confirmed", "corrected"}
                    else None
                )
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
        pending_mappings = self.mapping_review_queue()
        if pending_mappings:
            limitations.append("Automatically proposed knowledge mappings remain pending review and are provisional.")
        if any(item["difficulty"].get("effective_basis") == "structural_complexity" for item in questions):
            limitations.append(
                "Questions without qualified empirical data or an official label expose structural complexity only; it is not a reliable observed difficulty claim."
            )
        proposed_families = [item for item in self.bank.get("families", []) if item.get("review_status") == "proposed"]
        if proposed_families:
            limitations.append("Automatically proposed item families remain provisional until explicitly reviewed.")
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
            "difficulty_basis_distribution": dict(sorted(Counter(
                item["difficulty"].get("effective_basis", "unknown") for item in questions
            ).items())),
            "difficulty_calibration": self.state.get("difficulty_calibration"),
            "answer_marking_association": dict(
                sorted(Counter(item.get("marking_link_status", "missing") for item in questions).items())
            ),
            "mapping_review": {"pending_count": len(pending_mappings), "queue": pending_mappings},
            "question_families": {
                "confirmed_count": sum(item.get("review_status") in {"confirmed", "corrected"} for item in self.bank.get("families", [])),
                "proposed_count": len(proposed_families),
                "families": self.bank.get("families", []),
            },
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

    def daily_plan(self, payload: Any) -> dict[str, Any]:
        required = {
            "start_date", "target_date", "available_weekdays", "minutes_per_day", "durations",
            "desired_retention", "final_review_days", "mode",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ExamError("daily-plan input must contain exactly the documented scheduling fields")
        try:
            start = date.fromisoformat(str(payload["start_date"]))
            target = date.fromisoformat(str(payload["target_date"] or self.state.get("target_date")))
        except (TypeError, ValueError) as exc:
            raise ExamError("daily-plan start_date and target_date must use YYYY-MM-DD") from exc
        weekdays = payload.get("available_weekdays")
        if (
            not isinstance(weekdays, list)
            or not weekdays
            or any(not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= 7 for item in weekdays)
        ):
            raise ExamError("available_weekdays must contain ISO weekday integers 1 through 7")
        minutes_per_day = int(require_number(payload.get("minutes_per_day"), "minutes_per_day", 1, 1440))
        durations = payload.get("durations")
        duration_keys = {"learn", "remediate", "review", "practice", "prerequisite"}
        if not isinstance(durations, dict) or set(durations) != duration_keys:
            raise ExamError("durations must contain learn, remediate, review, practice, and prerequisite")
        normalized_durations = {
            key: int(require_number(value, f"durations.{key}", 1, 1440)) for key, value in durations.items()
        }
        desired_retention = round(float(require_number(payload.get("desired_retention"), "desired_retention", 0.5, 0.99)), 3)
        final_review_days = int(require_number(payload.get("final_review_days"), "final_review_days", 0, 365))
        mode = payload.get("mode")
        priority = self.plan(mode, 100)
        available_dates: list[date] = []
        cursor = start
        while cursor < target and len(available_dates) <= 3660:
            if cursor.isoweekday() in weekdays:
                available_dates.append(cursor)
            cursor += timedelta(days=1)
        tasks: list[dict[str, Any]] = []
        seen_prerequisites: set[str] = set()
        for item in priority["queue"]:
            for prerequisite_id in item["prerequisite_atom_ids"]:
                if prerequisite_id not in seen_prerequisites:
                    seen_prerequisites.add(prerequisite_id)
                    tasks.append(
                        {
                            "id": f"prerequisite:{prerequisite_id}",
                            "category": "prerequisite",
                            "atom_id": prerequisite_id,
                            "question_id": None,
                            "minutes": normalized_durations["prerequisite"],
                            "priority_score": 1.0,
                            "final_review": False,
                        }
                    )
            category = {
                "learn": "learn", "remediate": "remediate", "repair_prerequisites": "remediate",
                "review": "review", "verify_skip": "review",
            }[item["action"]]
            tasks.append(
                {
                    "id": f"{category}:{item['atom_id']}",
                    "category": category,
                    "atom_id": item["atom_id"],
                    "question_id": None,
                    "minutes": normalized_durations[category],
                    "priority_score": item["priority_score"],
                    "final_review": False,
                }
            )
            for question_id in item["representative_question_ids"][:2]:
                tasks.append(
                    {
                        "id": f"practice:{question_id}",
                        "category": "practice",
                        "atom_id": item["atom_id"],
                        "question_id": question_id,
                        "minutes": normalized_durations["practice"],
                        "priority_score": item["priority_score"],
                        "final_review": False,
                    }
                )
            if final_review_days:
                tasks.append(
                    {
                        "id": f"final-review:{item['atom_id']}",
                        "category": "review",
                        "atom_id": item["atom_id"],
                        "question_id": None,
                        "minutes": normalized_durations["review"],
                        "priority_score": item["priority_score"],
                        "final_review": True,
                    }
                )
        final_start = target - timedelta(days=final_review_days)
        ordinary = [item for item in tasks if not item["final_review"]]
        final_tasks = [item for item in tasks if item["final_review"]]
        schedule = [
            {"date": day.isoformat(), "capacity_minutes": minutes_per_day, "planned_minutes": 0, "tasks": []}
            for day in available_dates
        ]

        def place(task: dict[str, Any], eligible: list[dict[str, Any]]) -> bool:
            for day in eligible:
                if day["planned_minutes"] + task["minutes"] <= day["capacity_minutes"]:
                    day["tasks"].append(task)
                    day["planned_minutes"] += task["minutes"]
                    return True
            return False

        unscheduled: list[dict[str, Any]] = []
        pre_final_days = [item for item in schedule if date.fromisoformat(item["date"]) < final_start] or schedule
        for task in ordinary:
            if not place(task, pre_final_days):
                unscheduled.append(task)
        final_days = [item for item in schedule if date.fromisoformat(item["date"]) >= final_start]
        for task in final_tasks:
            if not place(task, final_days):
                unscheduled.append(task)
        counts = Counter(task["category"] for day in schedule for task in day["tasks"])
        required_minutes = sum(item["minutes"] for item in tasks)
        capacity = len(schedule) * minutes_per_day
        feasible = not unscheduled and target > start
        adjustments = []
        if not feasible:
            deficit = max(0, required_minutes - capacity)
            extra_days = math.ceil(deficit / minutes_per_day) if minutes_per_day else 0
            adjustments = [
                f"Add at least {extra_days} available study day(s) at the current capacity." if extra_days else "Move the target date later or add eligible final-review days.",
                "Increase minutes per available day without lowering the mastery threshold.",
                "Narrow the explicitly declared exam scope, then regenerate the corpus analysis.",
            ]
        return {
            "schema_version": SCHEMA_VERSION,
            "exam_revision": self.revision,
            "course_revision": self.workspace.revision,
            "status": "feasible" if feasible else "infeasible",
            "start_date": start.isoformat(),
            "target_date": target.isoformat(),
            "desired_retention": desired_retention,
            "final_review_days": final_review_days,
            "capacity_minutes": capacity,
            "required_minutes": required_minutes,
            "scheduled_counts": {key: counts.get(key, 0) for key in ["learn", "remediate", "review", "practice", "prerequisite"]},
            "days": schedule,
            "unscheduled_tasks": unscheduled,
            "gap_minutes": sum(item["minutes"] for item in unscheduled),
            "adjustments": adjustments,
            "warnings": priority["warnings"],
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
        calibration = self.state.get("difficulty_calibration")
        if not isinstance(calibration, dict) or set(calibration) != {
            "offset", "anchor_count", "mae_before", "mae_after", "updated_at"
        }:
            errors.append("exam difficulty_calibration is invalid")
        elif (
            not isinstance(calibration.get("offset"), (int, float))
            or isinstance(calibration.get("offset"), bool)
            or not isinstance(calibration.get("anchor_count"), int)
            or isinstance(calibration.get("anchor_count"), bool)
            or calibration.get("anchor_count", -1) < 0
        ):
            errors.append("exam difficulty calibration values are invalid")
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
        families = self.bank.get("families")
        if not isinstance(papers, list) or not isinstance(questions, list) or not isinstance(families, list):
            errors.append("exam bank papers, questions, and families must be lists")
            papers, questions, families = [], [], []
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
        normalized_families: list[dict[str, Any]] = []
        for index, family in enumerate(families):
            try:
                normalized = self._normalize_family(family, index, set(question_ids))
                normalized_families.append(normalized)
                if family != normalized:
                    errors.append(f"family {index} does not match the normalized schema")
            except (AtomLearnError, ExamError) as exc:
                errors.append(str(exc))
        family_ids = [item["id"] for item in normalized_families]
        if len(family_ids) != len(set(family_ids)):
            errors.append("exam bank contains duplicate family IDs")
        family_by_id = {item["id"]: item for item in normalized_families}
        for question in normalized_questions:
            family_id = question.get("family_id")
            if family_id and (
                family_id not in family_by_id
                or question["id"] not in family_by_id[family_id]["question_ids"]
                or family_by_id[family_id]["review_status"] not in {"confirmed", "corrected"}
            ):
                errors.append(f"{question['id']}: family_id must reference a reviewed family containing the question")
            for candidate_id in question.get("family_candidate_ids", []):
                if candidate_id not in family_by_id or family_by_id[candidate_id]["review_status"] != "proposed":
                    errors.append(f"{question['id']}: family candidate must reference a proposed family")
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
            event_type = event.get("type")
            if event_type not in {
                "exam.questions_imported", "exam.mappings_reviewed", "exam.difficulty_calibrated",
                "exam.empirical_difficulty_recorded", "exam.families_proposed", "exam.families_reviewed",
            }:
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
            if event_type == "exam.questions_imported":
                if not isinstance(details, dict) or set(details) != {"paper_ids", "question_ids"}:
                    errors.append(f"{event_id}: invalid import event details")
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
            elif event_type == "exam.mappings_reviewed":
                if not isinstance(details, dict) or set(details) != {"question_ids", "review_count", "remaining"}:
                    errors.append(f"{event_id}: invalid mapping-review event details")
            elif event_type == "exam.difficulty_calibrated":
                if not isinstance(details, dict) or set(details) != {"offset", "anchor_count", "mae_before", "mae_after"}:
                    errors.append(f"{event_id}: invalid calibration event details")
            elif event_type == "exam.empirical_difficulty_recorded":
                if not isinstance(details, dict) or set(details) != {
                    "question_ids", "qualified_question_ids", "minimum_attempts", "effective_priority"
                }:
                    errors.append(f"{event_id}: invalid empirical difficulty event details")
            elif event_type == "exam.families_proposed":
                if not isinstance(details, dict) or set(details) != {"proposed_family_ids", "threshold", "pair_count"}:
                    errors.append(f"{event_id}: invalid family proposal event details")
            elif event_type == "exam.families_reviewed":
                if not isinstance(details, dict) or set(details) != {"family_ids", "remaining_proposals"}:
                    errors.append(f"{event_id}: invalid family review event details")
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
            "pending_mapping_reviews": len(self.mapping_review_queue()),
            "proposed_family_reviews": sum(item.get("review_status") == "proposed" for item in self.bank.get("families", [])),
            "confirmed_families": sum(item.get("review_status") in {"confirmed", "corrected"} for item in self.bank.get("families", [])),
            "difficulty_calibration": self.state.get("difficulty_calibration"),
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
    initialize = sub.add_parser("init", help="Initialize a source-traceable exam corpus")
    initialize.add_argument("workspace")
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--target-date")
    import_parser = sub.add_parser("import", help="Import an already structured paper/question bundle")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    add_revision(import_parser)
    process = sub.add_parser("process", help="Split question documents and derive associations, mappings, and difficulty")
    process.add_argument("workspace")
    process.add_argument("--input", required=True)
    add_revision(process)
    process_source = sub.add_parser("process-source", help="Process one indexed source through the shared Document IR")
    process_source.add_argument("workspace")
    process_source.add_argument("--source-id", required=True)
    process_source.add_argument("--paper-id", required=True)
    process_source.add_argument("--title")
    process_source.add_argument("--year", type=int)
    process_source.add_argument("--kind", choices=sorted(PAPER_KINDS), default="official_past_exam")
    add_revision(process_source)
    review = sub.add_parser("review-mappings", help="Confirm, correct, unmap, or reject automatic Atom proposals")
    review.add_argument("workspace")
    review.add_argument("--input", required=True)
    add_revision(review)
    calibrate = sub.add_parser("calibrate", help="Calibrate rubric difficulty against official-level anchors")
    calibrate.add_argument("workspace")
    add_revision(calibrate)
    empirical = sub.add_parser("record-empirical", help="Attach source-located aggregate performance without conflating it with structural complexity")
    empirical.add_argument("workspace")
    empirical.add_argument("--input", required=True)
    add_revision(empirical)
    families = sub.add_parser("propose-families", help="Propose cross-paper item families for explicit review")
    families.add_argument("workspace")
    families.add_argument("--threshold", type=float, default=0.62)
    add_revision(families)
    review_families = sub.add_parser("review-families", help="Confirm, correct, or reject proposed item families and transfer evidence")
    review_families.add_argument("workspace")
    review_families.add_argument("--input", required=True)
    add_revision(review_families)
    simple_help = {
        "status": "Show corpus, mapping-review, calibration, and validity status",
        "validate": "Validate exam state, corpus schema, mappings, and audit events",
        "analyze": "Analyze supplied-corpus coverage, difficulty, and emphasis",
        "render": "Regenerate exam blueprint and targeted study-plan views",
    }
    for action in ["status", "validate", "analyze", "render"]:
        command = sub.add_parser(action, help=simple_help[action])
        command.add_argument("workspace")
    plan = sub.add_parser("plan", help="Generate a prerequisite-aware learning or review priority queue")
    plan.add_argument("workspace")
    plan.add_argument("--mode", choices=sorted(PREPARATION_MODES), default="mixed")
    plan.add_argument("--limit", type=int, default=10)
    daily = sub.add_parser("daily-plan", help="Build a capacity-checked daily learn/remediate/review/practice schedule")
    daily.add_argument("workspace")
    daily.add_argument("--input", required=True)
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
    if args.action in {
        "import", "process", "process-source", "review-mappings", "calibrate",
        "record-empirical", "propose-families", "review-families",
    }:
        engine.expect_revision(args.expected_exam_revision)
        if args.action == "import":
            result = engine.import_bundle(read_data(Path(args.input)))
        elif args.action == "process":
            input_path = Path(args.input).resolve()
            result = engine.process_documents(read_data(input_path), input_path.parent)
        elif args.action == "process-source":
            result = engine.process_source(
                args.source_id,
                paper_id=args.paper_id,
                title=args.title,
                year=args.year,
                kind=args.kind,
            )
        elif args.action == "review-mappings":
            result = engine.review_mappings(read_data(Path(args.input)))
            engine._commit("exam.mappings_reviewed", result)
        elif args.action == "calibrate":
            result = engine.calibrate_difficulty()
            engine._commit("exam.difficulty_calibrated", result)
        elif args.action == "record-empirical":
            result = engine.record_empirical_difficulty(read_data(Path(args.input)))
            engine._commit("exam.empirical_difficulty_recorded", result)
        elif args.action == "propose-families":
            result = engine.propose_families(args.threshold)
            engine._commit("exam.families_proposed", result)
        else:
            result = engine.review_families(read_data(Path(args.input)))
            engine._commit("exam.families_reviewed", result)
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
    elif args.action == "daily-plan":
        print(json.dumps(engine.daily_plan(read_data(Path(args.input))), ensure_ascii=False, indent=2))
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
    except (ExamError, RagError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
