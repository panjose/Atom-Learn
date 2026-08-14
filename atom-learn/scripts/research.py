#!/usr/bin/env python3
"""Research-oriented paper reading and synthesis for AtomLearn."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from atomlearn import (
    MASTERY_LIKE,
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


PAPER_STATUSES = {
    "discovered",
    "queued",
    "active",
    "read",
    "synthesized",
    "parked",
    "excluded",
}
PAPER_ROLES = {
    "survey",
    "seminal",
    "theory",
    "method",
    "benchmark",
    "dataset",
    "application",
    "critique",
    "replication",
}
RELATION_TYPES = {"supports", "extends", "contradicts", "replicates", "compares"}
CLAIM_STRENGTHS = {"weak", "mixed", "moderate", "strong", "unclear"}
RESEARCH_STATUSES = {"scoping", "mapping", "reading", "synthesizing", "maintaining", "complete"}
ROLE_ORDER = {
    "survey": 0,
    "seminal": 1,
    "theory": 2,
    "method": 3,
    "benchmark": 4,
    "dataset": 5,
    "critique": 6,
    "replication": 7,
    "application": 8,
}
RESEARCH_VIEW_FILES = [
    "RESEARCH_MAP.md",
    "CURRENT_PAPER.md",
    "LITERATURE_MATRIX.md",
    "RESEARCH_GAPS.md",
]


class ResearchError(RuntimeError):
    """A user-correctable research workflow error."""


def template_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def require_text(value: Any, label: str, *, allow_empty: bool = False, limit: int = 4000) -> str:
    text = require_string(value, label, allow_empty=allow_empty)
    if len(text) > limit:
        raise ResearchError(f"{label} must be at most {limit} characters; store a concise note, not full text")
    return text


def string_list(value: Any, label: str, *, limit: int = 2000) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ResearchError(f"{label} must be a string list")
    result = unique(item.strip() for item in value if item.strip())
    if any(len(item) > limit for item in result):
        raise ResearchError(f"{label} entries must be at most {limit} characters")
    return result


def markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def compact(items: list[Any], empty: str = "Not recorded") -> str:
    values = [markdown(item) for item in items if markdown(item)]
    return "; ".join(values) if values else empty


class ResearchEngine:
    def __init__(self, workspace: Workspace, state: dict[str, Any], papers: dict[str, dict[str, Any]]):
        self.workspace = workspace
        self.root = workspace.meta / "research"
        self.paper_dir = self.root / "papers"
        self.state = state
        self.papers = papers

    @classmethod
    def initialize(
        cls,
        workspace_path: str,
        field: str,
        question: str,
        scope: str,
        inclusion_criteria: list[str],
        exclusion_criteria: list[str],
    ) -> "ResearchEngine":
        workspace = load_workspace(workspace_path)
        errors = workspace.validate()
        if errors:
            raise ResearchError("Cannot initialize research in an invalid workspace:\n- " + "\n- ".join(errors))
        root = workspace.meta / "research"
        if (root / "state.yaml").exists():
            raise ResearchError("Research mode is already initialized for this workspace")
        paper_dir = root / "papers"
        paper_dir.mkdir(parents=True, exist_ok=True)
        state = read_data(template_dir() / "research-state.yaml")
        timestamp = iso()
        state.update(
            {
                "field": require_text(field, "field", limit=500),
                "research_question": require_text(question, "research question", limit=2000),
                "scope": require_text(scope, "scope", allow_empty=True, limit=2000),
                "inclusion_criteria": string_list(inclusion_criteria, "inclusion criteria"),
                "exclusion_criteria": string_list(exclusion_criteria, "exclusion criteria"),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        write_yaml(root / "state.yaml", state)
        atomic_text(root / "events.ndjson", "")
        engine = cls(workspace, state, {})
        engine.render()
        return engine

    @classmethod
    def load(cls, workspace_path: str) -> "ResearchEngine":
        workspace = load_workspace(workspace_path)
        base_errors = workspace.validate()
        if base_errors:
            raise ResearchError("Cannot use research mode in an invalid workspace:\n- " + "\n- ".join(base_errors))
        root = workspace.meta / "research"
        state_path = root / "state.yaml"
        if not state_path.is_file():
            raise ResearchError("Research mode is not initialized; run `research init` first")
        papers: dict[str, dict[str, Any]] = {}
        paper_dir = root / "papers"
        for path in sorted(paper_dir.glob("*.yaml")):
            paper = read_data(path)
            paper_id = path.stem
            try:
                require_id(paper_id, "paper filename")
            except AtomLearnError as exc:
                raise ResearchError(str(exc)) from exc
            if paper.get("id") != paper_id:
                raise ResearchError(f"Paper filename {paper_id} does not match id {paper.get('id')!r}")
            if paper_id in papers:
                raise ResearchError(f"Duplicate paper ID: {paper_id}")
            papers[paper_id] = paper
        return cls(workspace, read_data(state_path), papers)

    @property
    def revision(self) -> int:
        revision = self.state.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ResearchError("research revision must be a non-negative integer")
        return revision

    def expect_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self.revision:
            raise ResearchError(
                f"Stale research revision: expected {expected}, current is {self.revision}. Reload research status."
            )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.state.get("schema_version") != SCHEMA_VERSION:
            errors.append("research state has unsupported schema_version")
        if self.state.get("status") not in RESEARCH_STATUSES:
            errors.append(f"research status is invalid: {self.state.get('status')!r}")
        try:
            require_text(self.state.get("field"), "research field", limit=500)
            require_text(self.state.get("research_question"), "research question", limit=2000)
            string_list(self.state.get("inclusion_criteria", []), "inclusion criteria")
            string_list(self.state.get("exclusion_criteria", []), "exclusion criteria")
        except (AtomLearnError, ResearchError) as exc:
            errors.append(str(exc))
        active_ids: list[str] = []
        for paper_id, paper in self.papers.items():
            try:
                require_id(paper_id, "paper key")
                if paper.get("id") != paper_id:
                    errors.append(f"paper key {paper_id} does not match id {paper.get('id')!r}")
                if paper.get("schema_version") != SCHEMA_VERSION:
                    errors.append(f"{paper_id}: unsupported schema_version")
                if paper.get("revision") != self.revision:
                    errors.append(f"{paper_id}: revision does not match research revision")
                require_text(paper.get("title"), f"{paper_id}.title", limit=1000)
                string_list(paper.get("authors", []), f"{paper_id}.authors", limit=500)
                string_list(paper.get("tags", []), f"{paper_id}.tags", limit=200)
                prerequisites = string_list(
                    paper.get("prerequisite_paper_ids", []), f"{paper_id}.prerequisite_paper_ids", limit=200
                )
                cites = string_list(paper.get("cites", []), f"{paper_id}.cites", limit=200)
                concepts = string_list(paper.get("concept_atom_ids", []), f"{paper_id}.concept_atom_ids", limit=200)
            except (AtomLearnError, ResearchError) as exc:
                errors.append(str(exc))
                continue
            if paper.get("status") not in PAPER_STATUSES:
                errors.append(f"{paper_id}: invalid status {paper.get('status')!r}")
            if paper.get("role") not in PAPER_ROLES:
                errors.append(f"{paper_id}: invalid role {paper.get('role')!r}")
            priority = paper.get("priority")
            if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 5:
                errors.append(f"{paper_id}: priority must be an integer from 1 to 5")
            year = paper.get("year")
            if year is not None and (
                not isinstance(year, int) or isinstance(year, bool) or not 1000 <= year <= 3000
            ):
                errors.append(f"{paper_id}: year must be null or a four-digit integer")
            if paper.get("status") == "active":
                active_ids.append(paper_id)
            for related_id in prerequisites + cites:
                if related_id not in self.papers:
                    errors.append(f"{paper_id}: referenced paper does not exist: {related_id}")
                if related_id == paper_id:
                    errors.append(f"{paper_id}: cannot reference itself")
            for atom_id in concepts:
                if atom_id not in self.workspace.atoms:
                    errors.append(f"{paper_id}: concept Atom does not exist: {atom_id}")
            analysis_errors = self._validate_analysis(
                paper_id,
                paper.get("analysis"),
                complete=paper.get("status") in {"read", "synthesized"},
            )
            errors.extend(analysis_errors)
            if paper.get("status") == "excluded" and not paper.get("exclusion_reason"):
                errors.append(f"{paper_id}: excluded papers require exclusion_reason")
        state_active = self.state.get("active_paper_id")
        if len(active_ids) > 1:
            errors.append("research mode permits at most one Active Paper")
        if state_active is None and active_ids:
            errors.append("research state has no active_paper_id but a paper is active")
        if state_active is not None and active_ids != [state_active]:
            errors.append("active_paper_id does not match the Active Paper")
        errors.extend(self._validate_prerequisite_dag())
        return unique(errors)

    def _validate_prerequisite_dag(self) -> list[str]:
        errors: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(paper_id: str, trail: list[str]) -> None:
            if paper_id in visiting:
                errors.append("paper prerequisite cycle: " + " -> ".join(trail + [paper_id]))
                return
            if paper_id in visited or paper_id not in self.papers:
                return
            visiting.add(paper_id)
            for prerequisite in self.papers[paper_id].get("prerequisite_paper_ids", []):
                visit(prerequisite, trail + [paper_id])
            visiting.remove(paper_id)
            visited.add(paper_id)

        for paper_id in self.papers:
            visit(paper_id, [])
        return unique(errors)

    def _validate_analysis(self, paper_id: str, analysis: Any, *, complete: bool) -> list[str]:
        if not isinstance(analysis, dict):
            return [f"{paper_id}.analysis must be a mapping"]
        errors: list[str] = []
        try:
            problem = require_text(
                analysis.get("problem", ""), f"{paper_id}.analysis.problem", allow_empty=not complete, limit=4000
            )
            contributions = string_list(
                analysis.get("contributions", []), f"{paper_id}.analysis.contributions"
            )
            approach = require_text(
                analysis.get("approach", ""), f"{paper_id}.analysis.approach", allow_empty=not complete, limit=4000
            )
            limitations = string_list(analysis.get("limitations", []), f"{paper_id}.analysis.limitations")
            require_text(
                analysis.get("field_positioning", ""),
                f"{paper_id}.analysis.field_positioning",
                allow_empty=not complete,
                limit=4000,
            )
            string_list(analysis.get("datasets", []), f"{paper_id}.analysis.datasets")
            string_list(analysis.get("open_questions", []), f"{paper_id}.analysis.open_questions")
            if complete and (not problem or not contributions or not approach or not limitations):
                errors.append(
                    f"{paper_id}: completion requires problem, contributions, approach, and limitations"
                )
        except (AtomLearnError, ResearchError) as exc:
            errors.append(str(exc))
        claims = analysis.get("claims", [])
        if not isinstance(claims, list):
            errors.append(f"{paper_id}.analysis.claims must be a list")
            claims = []
        claim_ids: set[str] = set()
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"{paper_id}.analysis.claims[{index}] must be a mapping")
                continue
            try:
                claim_id = require_id(claim.get("id"), f"{paper_id}.claims[{index}].id")
                require_text(claim.get("statement"), f"{claim_id}.statement", limit=2000)
                require_text(claim.get("evidence_summary"), f"{claim_id}.evidence_summary", limit=2000)
            except (AtomLearnError, ResearchError) as exc:
                errors.append(str(exc))
                continue
            if claim_id in claim_ids:
                errors.append(f"{paper_id}: duplicate claim ID {claim_id}")
            claim_ids.add(claim_id)
            if claim.get("strength") not in CLAIM_STRENGTHS:
                errors.append(f"{claim_id}: invalid evidence strength")
        if complete and not claims:
            errors.append(f"{paper_id}: completion requires at least one evidence-linked claim")
        relations = analysis.get("relations", [])
        if not isinstance(relations, list):
            errors.append(f"{paper_id}.analysis.relations must be a list")
            relations = []
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                errors.append(f"{paper_id}.analysis.relations[{index}] must be a mapping")
                continue
            target = relation.get("paper_id")
            if target not in self.papers or target == paper_id:
                errors.append(f"{paper_id}.relations[{index}]: target must be another imported paper")
            if relation.get("type") not in RELATION_TYPES:
                errors.append(f"{paper_id}.relations[{index}]: invalid relation type")
            try:
                require_text(relation.get("note"), f"{paper_id}.relations[{index}].note", limit=2000)
            except (AtomLearnError, ResearchError) as exc:
                errors.append(str(exc))
        return errors

    def commit(self, event_type: str, details: dict[str, Any] | None = None) -> None:
        new_revision = self.revision + 1
        timestamp = iso()
        self.state["revision"] = new_revision
        self.state["updated_at"] = timestamp
        for paper in self.papers.values():
            paper["revision"] = new_revision
            paper["updated_at"] = timestamp
        errors = self.validate()
        if errors:
            raise ResearchError("Research mutation would create invalid state:\n- " + "\n- ".join(errors))
        for paper_id, paper in sorted(self.papers.items()):
            write_yaml(self.paper_dir / f"{paper_id}.yaml", paper)
        write_yaml(self.root / "state.yaml", self.state)
        event = {
            "event_id": f"revt-{new_revision:06d}",
            "revision": new_revision,
            "type": event_type,
            "at": timestamp,
            "course_revision": self.workspace.revision,
            "details": details or {},
        }
        with (self.root / "events.ndjson").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.render()

    def import_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict):
            raise ResearchError("research import plan must be a mapping")
        research = plan.get("research", {})
        if research:
            if not isinstance(research, dict):
                raise ResearchError("research must be a mapping")
            for field, label, limit in [
                ("field", "research field", 500),
                ("research_question", "research question", 2000),
                ("scope", "scope", 2000),
            ]:
                if field in research:
                    self.state[field] = require_text(
                        research[field], label, allow_empty=field == "scope", limit=limit
                    )
            for field, label in [
                ("inclusion_criteria", "inclusion criteria"),
                ("exclusion_criteria", "exclusion criteria"),
            ]:
                if field in research:
                    self.state[field] = string_list(research[field], label)
        raw_papers = plan.get("papers")
        if not isinstance(raw_papers, list) or not raw_papers:
            raise ResearchError("research import plan requires a non-empty papers list")
        imported: list[str] = []
        seen: set[str] = set()
        for raw in raw_papers:
            if not isinstance(raw, dict):
                raise ResearchError("each paper must be a mapping")
            paper_id = require_id(raw.get("id"), "paper.id")
            if paper_id in seen:
                raise ResearchError(f"duplicate paper ID in import plan: {paper_id}")
            seen.add(paper_id)
            if "full_text" in raw:
                raise ResearchError("Do not store full paper text in research state; store metadata and locators")
            paper = self._normalize_paper(raw, self.papers.get(paper_id))
            self.papers[paper_id] = paper
            imported.append(paper_id)
        self.state["status"] = "mapping"
        return {"imported_paper_ids": imported, "total_papers": len(self.papers)}

    def _normalize_paper(self, raw: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
        paper_id = require_id(raw.get("id"), "paper.id")
        paper = copy.deepcopy(existing) if existing else read_data(template_dir() / "research-paper.yaml")
        timestamp = iso()
        paper["id"] = paper_id
        paper["title"] = require_text(raw.get("title", paper.get("title")), f"{paper_id}.title", limit=1000)
        for field, label, limit in [
            ("authors", "authors", 500),
            ("prerequisite_paper_ids", "prerequisite_paper_ids", 200),
            ("cites", "cites", 200),
            ("external_citations", "external_citations", 1000),
            ("concept_atom_ids", "concept_atom_ids", 200),
            ("tags", "tags", 200),
        ]:
            if field in raw or not existing:
                paper[field] = string_list(raw.get(field, paper.get(field, [])), f"{paper_id}.{label}", limit=limit)
        for field in ["venue", "doi", "url", "locator"]:
            if field in raw or not existing:
                paper[field] = require_text(
                    raw.get(field, paper.get(field, "")),
                    f"{paper_id}.{field}",
                    allow_empty=True,
                    limit=2000,
                )
        if "year" in raw or not existing:
            paper["year"] = raw.get("year")
        if "role" in raw or not existing:
            paper["role"] = raw.get("role", paper.get("role", "method"))
        if "priority" in raw or not existing:
            paper["priority"] = raw.get("priority", paper.get("priority", 3))
        if "status" in raw or not existing:
            paper["status"] = raw.get("status", paper.get("status", "queued"))
        if "analysis" in raw:
            paper["analysis"] = self._normalize_analysis(paper_id, raw["analysis"])
        if paper.get("status") == "excluded":
            paper["exclusion_reason"] = require_text(
                raw.get("exclusion_reason", paper.get("exclusion_reason")),
                f"{paper_id}.exclusion_reason",
                limit=2000,
            )
        paper["schema_version"] = SCHEMA_VERSION
        paper["revision"] = self.revision
        paper["created_at"] = paper.get("created_at") or timestamp
        paper["updated_at"] = timestamp
        return paper

    def _normalize_analysis(self, paper_id: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ResearchError("paper analysis must be a mapping")
        if "full_text" in raw:
            raise ResearchError("Do not store full paper text in research state; store concise notes and locators")
        analysis = {
            "problem": require_text(raw.get("problem", ""), "analysis.problem", allow_empty=True),
            "contributions": string_list(raw.get("contributions", []), "analysis.contributions"),
            "approach": require_text(raw.get("approach", ""), "analysis.approach", allow_empty=True),
            "datasets": string_list(raw.get("datasets", []), "analysis.datasets"),
            "claims": [],
            "limitations": string_list(raw.get("limitations", []), "analysis.limitations"),
            "open_questions": string_list(raw.get("open_questions", []), "analysis.open_questions"),
            "field_positioning": require_text(
                raw.get("field_positioning", ""), "analysis.field_positioning", allow_empty=True
            ),
            "relations": [],
        }
        claims = raw.get("claims", [])
        if not isinstance(claims, list):
            raise ResearchError("analysis.claims must be a list")
        for index, item in enumerate(claims, 1):
            if not isinstance(item, dict):
                raise ResearchError("each claim must be a mapping")
            claim_id = require_id(item.get("id", f"{paper_id}.claim.{index:03d}"), "claim.id")
            analysis["claims"].append(
                {
                    "id": claim_id,
                    "statement": require_text(item.get("statement"), f"{claim_id}.statement", limit=2000),
                    "evidence_summary": require_text(
                        item.get("evidence_summary"), f"{claim_id}.evidence_summary", limit=2000
                    ),
                    "strength": item.get("strength", "unclear"),
                }
            )
        relations = raw.get("relations", [])
        if not isinstance(relations, list):
            raise ResearchError("analysis.relations must be a list")
        for item in relations:
            if not isinstance(item, dict):
                raise ResearchError("each paper relation must be a mapping")
            analysis["relations"].append(
                {
                    "paper_id": require_id(item.get("paper_id"), "relation.paper_id"),
                    "type": item.get("type"),
                    "note": require_text(item.get("note"), "relation.note", limit=2000),
                }
            )
        return analysis

    def paper(self, paper_id: str) -> dict[str, Any]:
        paper_id = require_id(paper_id, "paper ID")
        if paper_id not in self.papers:
            raise ResearchError(f"Paper not found: {paper_id}")
        return self.papers[paper_id]

    def next_papers(self) -> list[dict[str, Any]]:
        complete = {
            paper_id for paper_id, paper in self.papers.items() if paper.get("status") in {"read", "synthesized"}
        }
        candidates: list[dict[str, Any]] = []
        for paper_id, paper in self.papers.items():
            if paper.get("status") not in {"discovered", "queued"}:
                continue
            unmet = [item for item in paper.get("prerequisite_paper_ids", []) if item not in complete]
            if unmet:
                continue
            knowledge_gaps = [
                atom_id
                for atom_id in paper.get("concept_atom_ids", [])
                if self.workspace.atoms.get(atom_id, {}).get("status") not in MASTERY_LIKE
            ]
            candidates.append(
                {
                    "id": paper_id,
                    "title": paper.get("title"),
                    "role": paper.get("role"),
                    "priority": paper.get("priority"),
                    "year": paper.get("year"),
                    "knowledge_gap_atom_ids": knowledge_gaps,
                    "reason": self._reading_reason(paper, knowledge_gaps),
                }
            )
        candidates.sort(
            key=lambda item: (
                item["priority"],
                ROLE_ORDER.get(item["role"], 99),
                item["year"] if isinstance(item["year"], int) else 9999,
                item["id"],
            )
        )
        return candidates

    def _reading_reason(self, paper: dict[str, Any], knowledge_gaps: list[str]) -> str:
        role = paper.get("role")
        reason = {
            "survey": "Map the field vocabulary, branches, and major debates.",
            "seminal": "Establish the historical problem framing and foundational contribution.",
            "theory": "Understand the formal assumptions and explanatory framework.",
            "benchmark": "Understand how competing methods are measured.",
            "dataset": "Inspect the evidence source, sampling, and coverage limits.",
            "method": "Study a representative method after its foundations are available.",
            "critique": "Test accepted claims against explicit counterarguments.",
            "replication": "Check robustness and reproducibility of prior evidence.",
            "application": "Assess transfer beyond the original setting.",
        }.get(role, "Advance the mapped research question.")
        if knowledge_gaps:
            reason += " Repair Knowledge Atoms first: " + ", ".join(knowledge_gaps) + "."
        return reason

    def activate(self, paper_id: str) -> dict[str, Any]:
        paper = self.paper(paper_id)
        active_id = self.state.get("active_paper_id")
        if active_id and active_id != paper_id:
            raise ResearchError(f"Finish or park Active Paper {active_id} before activating another paper")
        if paper.get("status") not in {"discovered", "queued", "parked", "active"}:
            raise ResearchError(f"Paper {paper_id} cannot be activated from status {paper.get('status')}")
        unmet = [
            item
            for item in paper.get("prerequisite_paper_ids", [])
            if self.papers.get(item, {}).get("status") not in {"read", "synthesized"}
        ]
        if unmet:
            raise ResearchError("Paper prerequisites are unread: " + ", ".join(unmet))
        paper["status"] = "active"
        self.state["active_paper_id"] = paper_id
        self.state["status"] = "reading"
        gaps = [
            atom_id
            for atom_id in paper.get("concept_atom_ids", [])
            if self.workspace.atoms.get(atom_id, {}).get("status") not in MASTERY_LIKE
        ]
        return {"paper_id": paper_id, "knowledge_gap_atom_ids": gaps}

    def record_note(self, paper_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if self.state.get("active_paper_id") != paper_id or paper.get("status") != "active":
            raise ResearchError("Critical notes can only be recorded for the Active Paper")
        analysis = self._normalize_analysis(paper_id, payload)
        paper["analysis"] = analysis
        errors = self._validate_analysis(paper_id, analysis, complete=False)
        if errors:
            raise ResearchError("Invalid critical note:\n- " + "\n- ".join(errors))
        return {
            "paper_id": paper_id,
            "claim_ids": [item["id"] for item in analysis["claims"]],
            "open_question_count": len(analysis["open_questions"]),
        }

    def complete(self, paper_id: str) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if self.state.get("active_paper_id") != paper_id or paper.get("status") != "active":
            raise ResearchError("Only the Active Paper can be completed")
        errors = self._validate_analysis(paper_id, paper.get("analysis"), complete=True)
        if errors:
            raise ResearchError("Paper is not critically complete:\n- " + "\n- ".join(errors))
        paper["status"] = "read"
        paper["completed_at"] = iso()
        self.state["active_paper_id"] = None
        self.state["status"] = "synthesizing"
        return {"paper_id": paper_id, "status": "read"}

    def park(self, paper_id: str, reason: str) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if paper.get("status") not in {"active", "queued", "discovered"}:
            raise ResearchError(f"Paper {paper_id} cannot be parked from status {paper.get('status')}")
        paper["status"] = "parked"
        paper["parked_reason"] = require_text(reason, "parking reason", limit=2000)
        if self.state.get("active_paper_id") == paper_id:
            self.state["active_paper_id"] = None
        self.state["status"] = "mapping"
        return {"paper_id": paper_id, "status": "parked"}

    def exclude(self, paper_id: str, reason: str) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if paper.get("status") == "active":
            raise ResearchError("Park the Active Paper before excluding it")
        paper["status"] = "excluded"
        paper["exclusion_reason"] = require_text(reason, "exclusion reason", limit=2000)
        return {"paper_id": paper_id, "status": "excluded"}

    def synthesize(self) -> dict[str, Any]:
        integrated = []
        for paper_id, paper in self.papers.items():
            if paper.get("status") == "read":
                paper["status"] = "synthesized"
                integrated.append(paper_id)
        remaining = any(
            paper.get("status") in {"discovered", "queued", "active", "parked"}
            for paper in self.papers.values()
        )
        self.state["status"] = "maintaining" if remaining else "complete"
        return {
            "integrated_paper_ids": integrated,
            "open_questions": self.open_questions(),
            "contradictions": self.contradictions(),
        }

    def open_questions(self) -> list[dict[str, str]]:
        return [
            {"paper_id": paper_id, "question": question}
            for paper_id, paper in self.papers.items()
            if paper.get("status") in {"read", "synthesized"}
            for question in paper.get("analysis", {}).get("open_questions", [])
        ]

    def contradictions(self) -> list[dict[str, str]]:
        return [
            {"paper_id": paper_id, "target_paper_id": relation["paper_id"], "note": relation["note"]}
            for paper_id, paper in self.papers.items()
            if paper.get("status") in {"read", "synthesized"}
            for relation in paper.get("analysis", {}).get("relations", [])
            if relation.get("type") == "contradicts"
        ]

    def status_summary(self) -> dict[str, Any]:
        errors = self.validate()
        counts = Counter(paper.get("status", "unknown") for paper in self.papers.values())
        active_id = self.state.get("active_paper_id")
        return {
            "valid": not errors,
            "validation_errors": errors,
            "research_revision": self.revision,
            "course_revision": self.workspace.revision,
            "field": self.state.get("field"),
            "research_question": self.state.get("research_question"),
            "status": self.state.get("status"),
            "active_paper": self.papers.get(active_id) if active_id else None,
            "counts": dict(sorted(counts.items())),
            "next_candidates": self.next_papers(),
            "open_question_count": len(self.open_questions()),
            "contradiction_count": len(self.contradictions()),
        }

    def render(self) -> None:
        active_id = self.state.get("active_paper_id")
        active = self.papers.get(active_id) if active_id else None
        map_lines = [
            f"# {self.state.get('field')} Research Map",
            "",
            "> Generated by AtomLearn Research. Edit canonical state through `atomlearn research` commands.",
            "",
            "## Research Question",
            "",
            str(self.state.get("research_question")),
            "",
            "## Scope",
            "",
            str(self.state.get("scope") or "Not specified"),
            "",
            "## Paper Map",
            "",
            "| Paper | Year | Role | Status | Prerequisites | Concepts |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
        for paper_id, paper in sorted(self.papers.items()):
            map_lines.append(
                f"| `{paper_id}` {markdown(paper.get('title'))} | {paper.get('year') or ''} | "
                f"{paper.get('role')} | {paper.get('status')} | "
                f"{compact(paper.get('prerequisite_paper_ids', []), 'None')} | "
                f"{compact(paper.get('concept_atom_ids', []), 'None')} |"
            )
        if not self.papers:
            map_lines.append("| None |  |  |  |  |  |")

        current_lines = [
            "# Current Paper",
            "",
            "> Generated by AtomLearn Research. Keep one Active Paper at a time.",
            "",
            "## Active Paper",
            "",
            f"`{active_id}` — {active.get('title')}" if active else "None",
            "",
        ]
        if active:
            analysis = active.get("analysis", {})
            current_lines.extend(
                [
                    "## Reading Lens",
                    "",
                    f"- Role: {active.get('role')}",
                    f"- Why now: {self._reading_reason(active, [])}",
                    f"- Problem: {analysis.get('problem') or 'Not recorded'}",
                    f"- Approach: {analysis.get('approach') or 'Not recorded'}",
                    f"- Field positioning: {analysis.get('field_positioning') or 'Not recorded'}",
                    "",
                    "## Claims",
                    "",
                ]
            )
            current_lines.extend(
                [
                    f"- `{claim['id']}` [{claim['strength']}] {claim['statement']} — {claim['evidence_summary']}"
                    for claim in analysis.get("claims", [])
                ]
                or ["- None recorded"]
            )
            current_lines.extend(["", "## Open Questions", ""])
            current_lines.extend(
                [f"- {item}" for item in analysis.get("open_questions", [])] or ["- None recorded"]
            )

        matrix_lines = [
            "# Literature Matrix",
            "",
            "> Generated by AtomLearn Research from critically completed papers.",
            "",
            "| Paper | Contribution | Approach | Claims | Limitations | Field Position |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        completed = [
            (paper_id, paper)
            for paper_id, paper in sorted(self.papers.items())
            if paper.get("status") in {"read", "synthesized"}
        ]
        for paper_id, paper in completed:
            analysis = paper.get("analysis", {})
            claim_text = [f"[{item.get('strength')}] {item.get('statement')}" for item in analysis.get("claims", [])]
            matrix_lines.append(
                f"| `{paper_id}` {markdown(paper.get('title'))} | "
                f"{compact(analysis.get('contributions', []))} | {markdown(analysis.get('approach'))} | "
                f"{compact(claim_text)} | {compact(analysis.get('limitations', []))} | "
                f"{markdown(analysis.get('field_positioning'))} |"
            )
        if not completed:
            matrix_lines.append("| None completed |  |  |  |  |  |")

        gap_lines = [
            "# Research Gaps",
            "",
            "> Generated by AtomLearn Research. These are evidence-linked candidates, not established novelty claims.",
            "",
            "## Open Questions",
            "",
        ]
        gap_lines.extend(
            [f"- `{item['paper_id']}` — {item['question']}" for item in self.open_questions()] or ["- None"]
        )
        gap_lines.extend(["", "## Reported Limitations", ""])
        limitations = [
            (paper_id, limitation)
            for paper_id, paper in completed
            for limitation in paper.get("analysis", {}).get("limitations", [])
        ]
        gap_lines.extend([f"- `{paper_id}` — {item}" for paper_id, item in limitations] or ["- None"])
        gap_lines.extend(["", "## Contradictions", ""])
        gap_lines.extend(
            [
                f"- `{item['paper_id']}` contradicts `{item['target_paper_id']}` — {item['note']}"
                for item in self.contradictions()
            ]
            or ["- None"]
        )
        gap_lines.extend(["", "## Next Reading Candidates", ""])
        gap_lines.extend(
            [f"- `{item['id']}` — {item['title']}: {item['reason']}" for item in self.next_papers()]
            or ["- None"]
        )

        rendered = {
            "RESEARCH_MAP.md": map_lines,
            "CURRENT_PAPER.md": current_lines,
            "LITERATURE_MATRIX.md": matrix_lines,
            "RESEARCH_GAPS.md": gap_lines,
        }
        for filename, lines in rendered.items():
            atomic_text(self.workspace.root / filename, "\n".join(lines).rstrip() + "\n")


def add_revision_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-research-revision", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guide field-oriented research paper reading")
    sub = parser.add_subparsers(dest="action", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("workspace")
    initialize.add_argument("--field", required=True)
    initialize.add_argument("--question", required=True)
    initialize.add_argument("--scope", default="")
    initialize.add_argument("--include", action="append", default=[])
    initialize.add_argument("--exclude", action="append", default=[])
    for action in ["status", "validate", "list", "next", "render"]:
        parser_for_action = sub.add_parser(action)
        parser_for_action.add_argument("workspace")
        if action == "list":
            parser_for_action.add_argument("--status")
            parser_for_action.add_argument("--role")
    import_parser = sub.add_parser("import")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    add_revision_argument(import_parser)
    for action in ["activate", "complete", "synthesize"]:
        parser_for_action = sub.add_parser(action)
        parser_for_action.add_argument("workspace")
        if action != "synthesize":
            parser_for_action.add_argument("paper_id")
        add_revision_argument(parser_for_action)
    note = sub.add_parser("note")
    note.add_argument("workspace")
    note.add_argument("paper_id")
    note.add_argument("--input", required=True)
    add_revision_argument(note)
    for action in ["park", "exclude"]:
        parser_for_action = sub.add_parser(action)
        parser_for_action.add_argument("workspace")
        parser_for_action.add_argument("paper_id")
        parser_for_action.add_argument("--reason", required=True)
        add_revision_argument(parser_for_action)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "init":
        engine = ResearchEngine.initialize(
            args.workspace,
            args.field,
            args.question,
            args.scope,
            args.include,
            args.exclude,
        )
        print(json.dumps({"ok": True, "research_revision": engine.revision, "views": RESEARCH_VIEW_FILES}))
        return
    engine = ResearchEngine.load(args.workspace)
    if args.action == "validate":
        errors = engine.validate()
        if errors:
            raise ResearchError("Research validation failed:\n- " + "\n- ".join(errors))
        print(json.dumps({"ok": True, "research_revision": engine.revision, "papers": len(engine.papers)}))
        return
    errors = engine.validate()
    if errors:
        raise ResearchError("Refusing to use invalid research state:\n- " + "\n- ".join(errors))
    if args.action == "status":
        print(json.dumps(engine.status_summary(), ensure_ascii=False, indent=2))
        return
    if args.action == "list":
        papers = list(engine.papers.values())
        if args.status:
            papers = [item for item in papers if item.get("status") == args.status]
        if args.role:
            papers = [item for item in papers if item.get("role") == args.role]
        print(json.dumps(papers, ensure_ascii=False, indent=2))
        return
    if args.action == "next":
        print(json.dumps(engine.next_papers(), ensure_ascii=False, indent=2))
        return
    if args.action == "render":
        engine.render()
        print(json.dumps({"ok": True, "views": RESEARCH_VIEW_FILES}))
        return
    engine.expect_revision(args.expected_research_revision)
    if args.action == "import":
        result = engine.import_plan(read_data(Path(args.input)))
        event_type = "research.papers_imported"
    elif args.action == "activate":
        result = engine.activate(args.paper_id)
        event_type = "research.paper_activated"
    elif args.action == "note":
        result = engine.record_note(args.paper_id, read_data(Path(args.input)))
        event_type = "research.note_recorded"
    elif args.action == "complete":
        result = engine.complete(args.paper_id)
        event_type = "research.paper_completed"
    elif args.action == "park":
        result = engine.park(args.paper_id, args.reason)
        event_type = "research.paper_parked"
    elif args.action == "exclude":
        result = engine.exclude(args.paper_id, args.reason)
        event_type = "research.paper_excluded"
    elif args.action == "synthesize":
        result = engine.synthesize()
        event_type = "research.synthesized"
    else:  # pragma: no cover
        raise ResearchError(f"Unhandled research action: {args.action}")
    engine.commit(event_type, result)
    print(
        json.dumps(
            {
                "ok": True,
                "research_revision": engine.revision,
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
    except (ResearchError, AtomLearnError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
