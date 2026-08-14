#!/usr/bin/env python3
"""Research-oriented paper reading and synthesis for AtomLearn."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from atomlearn import (
    SATISFIED_STATUSES,
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
METADATA_STATUSES = {"unverified", "verified", "conflict"}
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


def normalize_doi(value: Any, *, allow_empty: bool = True) -> str:
    if value is None or not str(value).strip():
        if allow_empty:
            return ""
        raise ResearchError("DOI is required")
    doi = str(value).strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    doi = doi.rstrip(". ,;)")
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ResearchError(f"invalid DOI: {value!r}")
    return doi


def title_fingerprint(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def synthesis_tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    words = re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]", text)
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "under", "paper", "study", "method",
        "result", "results", "reported", "using", "into", "than", "their", "does", "show", "shows",
    }
    return {item for item in words if item not in stop}


def fetch_json(url: str, user_agent: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS provider URLs
        data = json.load(response)
    if not isinstance(data, dict):
        raise ResearchError("metadata provider returned a non-object response")
    return data


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
            paper.setdefault("metadata_verification", {"status": "unverified", "checks": {}})
            papers[paper_id] = paper
        state = read_data(state_path)
        state.setdefault("paper_aliases", {})
        state.setdefault("latest_synthesis", None)
        return cls(workspace, state, papers)

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
        doi_owners: dict[str, str] = {}
        title_owners: dict[str, str] = {}
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
                doi = normalize_doi(paper.get("doi", ""))
            except (AtomLearnError, ResearchError) as exc:
                errors.append(str(exc))
                continue
            if doi:
                if doi in doi_owners and doi_owners[doi] != paper_id:
                    errors.append(f"duplicate DOI across papers: {doi}")
                doi_owners[doi] = paper_id
            fingerprint = title_fingerprint(paper.get("title"))
            if fingerprint in title_owners and title_owners[fingerprint] != paper_id:
                errors.append(f"duplicate normalized title across papers: {paper.get('title')}")
            title_owners[fingerprint] = paper_id
            verification = paper.get("metadata_verification")
            if not isinstance(verification, dict) or verification.get("status") not in METADATA_STATUSES:
                errors.append(f"{paper_id}: invalid metadata_verification")
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
        aliases = self.state.get("paper_aliases", {})
        if not isinstance(aliases, dict):
            errors.append("paper_aliases must be a mapping")
        else:
            for alias, target in aliases.items():
                try:
                    require_id(alias, "paper alias")
                except AtomLearnError as exc:
                    errors.append(str(exc))
                if target not in self.papers or alias == target:
                    errors.append(f"paper alias {alias!r} must target a different canonical paper")
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
        doi_index = {
            normalize_doi(paper.get("doi", "")): paper_id
            for paper_id, paper in self.papers.items()
            if paper.get("doi")
        }
        title_index = {title_fingerprint(paper["title"]): paper_id for paper_id, paper in self.papers.items()}
        aliases = dict(self.state.get("paper_aliases", {}))
        merged: dict[str, dict[str, Any]] = {}
        deduplicated: list[dict[str, str]] = []
        for raw in raw_papers:
            if not isinstance(raw, dict):
                raise ResearchError("each paper must be a mapping")
            raw = copy.deepcopy(raw)
            raw_id = require_id(raw.get("id"), "paper.id")
            if "full_text" in raw:
                raise ResearchError("Do not store full paper text in research state; store metadata and locators")
            raw_doi = normalize_doi(raw.get("doi", ""))
            raw_title = require_text(raw.get("title"), f"{raw_id}.title", limit=1000)
            identity_matches = {
                candidate
                for candidate in [
                    raw_id if raw_id in self.papers or raw_id in merged else None,
                    doi_index.get(raw_doi) if raw_doi else None,
                    title_index.get(title_fingerprint(raw_title)),
                ]
                if candidate
            }
            if len(identity_matches) > 1:
                raise ResearchError(f"{raw_id}: DOI, title, and ID resolve to conflicting canonical papers")
            canonical_id = next(iter(identity_matches), raw_id)
            if raw_id != canonical_id:
                aliases[raw_id] = canonical_id
                deduplicated.append({"duplicate_id": raw_id, "canonical_id": canonical_id})
            raw["id"] = canonical_id
            current = merged.get(canonical_id, {})
            combined = copy.deepcopy(current)
            for key, value in raw.items():
                if key in {"authors", "prerequisite_paper_ids", "cites", "external_citations", "concept_atom_ids", "tags"}:
                    combined[key] = unique([*combined.get(key, []), *(value or [])])
                elif key not in combined or combined[key] is None or combined[key] == "" or combined[key] == []:
                    combined[key] = value
            merged[canonical_id] = combined
            if raw_doi:
                doi_index[raw_doi] = canonical_id
            title_index[title_fingerprint(raw_title)] = canonical_id
        aliases = {alias: aliases.get(target, target) for alias, target in aliases.items()}
        imported: list[str] = []
        for paper_id, raw in merged.items():
            for field in ["prerequisite_paper_ids", "cites"]:
                raw[field] = unique(
                    aliases.get(reference, reference)
                    for reference in raw.get(field, [])
                    if aliases.get(reference, reference) != paper_id
                )
            paper = self._normalize_paper(raw, self.papers.get(paper_id))
            self.papers[paper_id] = paper
            imported.append(paper_id)
        self.state["paper_aliases"] = aliases
        self.state["status"] = "mapping"
        return {
            "imported_paper_ids": imported,
            "deduplicated": deduplicated,
            "total_papers": len(self.papers),
        }

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
        for field in ["venue", "url", "locator"]:
            if field in raw or not existing:
                paper[field] = require_text(
                    raw.get(field, paper.get(field, "")),
                    f"{paper_id}.{field}",
                    allow_empty=True,
                    limit=2000,
                )
        if "doi" in raw or not existing:
            paper["doi"] = normalize_doi(raw.get("doi", paper.get("doi", "")))
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
        if "metadata_verification" in raw:
            paper["metadata_verification"] = copy.deepcopy(raw["metadata_verification"])
        paper.setdefault("metadata_verification", {"status": "unverified", "checks": {}})
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

    def _metadata_indexes(self) -> tuple[dict[str, str], dict[str, str]]:
        doi_index = {
            normalize_doi(paper.get("doi", "")): paper_id
            for paper_id, paper in self.papers.items()
            if paper.get("doi")
        }
        title_index = {title_fingerprint(paper.get("title")): paper_id for paper_id, paper in self.papers.items()}
        return doi_index, title_index

    def reconcile_metadata(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list) or not payload["records"]:
            raise ResearchError("metadata payload must contain a non-empty records list")
        doi_index, title_index = self._metadata_indexes()
        verified: list[str] = []
        conflicts: list[dict[str, Any]] = []
        citation_edges: list[dict[str, str]] = []
        unresolved: list[dict[str, str]] = []
        matched_records: list[tuple[str, dict[str, Any]]] = []
        aliases = self.state.get("paper_aliases", {})
        for index, record in enumerate(payload["records"]):
            if not isinstance(record, dict):
                raise ResearchError(f"records[{index}] must be a mapping")
            record_doi = normalize_doi(record.get("doi", ""))
            record_title = require_text(record.get("title"), f"records[{index}].title", limit=1000)
            requested_id = record.get("paper_id")
            if requested_id is not None:
                requested_id = require_id(requested_id, f"records[{index}].paper_id")
                requested_id = aliases.get(requested_id, requested_id)
            matches = {
                item
                for item in [requested_id, doi_index.get(record_doi) if record_doi else None, title_index.get(title_fingerprint(record_title))]
                if item in self.papers
            }
            if not matches:
                raise ResearchError(f"records[{index}] does not match an imported paper by ID, DOI, or title")
            if len(matches) != 1:
                raise ResearchError(f"records[{index}] identifiers resolve to conflicting papers")
            paper_id = next(iter(matches))
            paper = self.papers[paper_id]
            record_authors = string_list(record.get("authors", []), f"records[{index}].authors", limit=500)
            checks = {
                "title": title_fingerprint(paper.get("title")) == title_fingerprint(record_title),
                "doi": not paper.get("doi") or not record_doi or normalize_doi(paper.get("doi")) == record_doi,
                "year": paper.get("year") is None or record.get("year") is None or paper.get("year") == record.get("year"),
                "authors": not paper.get("authors") or not record_authors or bool(
                    {item.casefold() for item in paper.get("authors", [])} & {item.casefold() for item in record_authors}
                ),
            }
            status = "verified" if all(checks.values()) else "conflict"
            provider = require_text(record.get("provider", "harness"), f"records[{index}].provider", limit=200)
            paper["metadata_verification"] = {
                "status": status,
                "provider": provider,
                "provider_id": str(record.get("provider_id", "")),
                "retrieved_at": str(record.get("retrieved_at") or iso()),
                "checks": checks,
            }
            if status == "verified":
                verified.append(paper_id)
                if record_doi and not paper.get("doi"):
                    paper["doi"] = record_doi
                for field in ["year", "venue", "url"]:
                    if not paper.get(field) and record.get(field):
                        paper[field] = record[field]
                if not paper.get("authors") and record_authors:
                    paper["authors"] = record_authors
            else:
                conflicts.append({"paper_id": paper_id, "checks": checks})
            matched_records.append((paper_id, record))
        doi_index, title_index = self._metadata_indexes()
        for paper_id, record in matched_records:
            paper = self.papers[paper_id]
            for reference in record.get("references", []):
                if isinstance(reference, str):
                    reference = {"doi": reference} if reference.lower().startswith("10.") else {"provider_id": reference}
                if not isinstance(reference, dict):
                    raise ResearchError(f"{paper_id}.references entries must be strings or mappings")
                reference_doi = normalize_doi(reference.get("doi", ""))
                target = doi_index.get(reference_doi) if reference_doi else None
                if target is None and reference.get("title"):
                    target = title_index.get(title_fingerprint(reference["title"]))
                if target and target != paper_id:
                    if target not in paper["cites"]:
                        paper["cites"].append(target)
                        citation_edges.append({"from": paper_id, "to": target})
                elif not target:
                    external = reference_doi or str(reference.get("provider_id") or reference.get("title") or "").strip()
                    if external:
                        paper["external_citations"] = unique([*paper.get("external_citations", []), external])
                        unresolved.append({"paper_id": paper_id, "reference": external})
        return {
            "verified_paper_ids": unique(verified),
            "conflicts": conflicts,
            "citation_edges_added": citation_edges,
            "unresolved_references": unresolved,
        }

    def fetch_metadata(self, provider: str, timeout: float, mailto: str) -> dict[str, Any]:
        if provider not in {"crossref", "openalex"}:
            raise ResearchError("metadata provider must be crossref or openalex")
        if not 1 <= timeout <= 60:
            raise ResearchError("metadata timeout must be between 1 and 60 seconds")
        user_agent = "AtomLearn/0.11 (+https://github.com/panjose/Atom-Learn)"
        if mailto:
            user_agent += f" mailto:{mailto}"
        records: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for paper_id, paper in self.papers.items():
            doi = normalize_doi(paper.get("doi", ""))
            if not doi:
                failures.append({"paper_id": paper_id, "error": "missing DOI"})
                continue
            try:
                if provider == "crossref":
                    data = fetch_json(f"https://api.crossref.org/works/{quote(doi, safe='')}", user_agent, timeout)
                    message = data.get("message", {})
                    authors = [
                        " ".join(part for part in [item.get("given", ""), item.get("family", "")] if part).strip()
                        for item in message.get("author", [])
                    ]
                    date_parts = (message.get("published-print") or message.get("published-online") or {}).get("date-parts", [[]])
                    references = [
                        {key: value for key, value in {"doi": item.get("DOI"), "title": item.get("article-title")}.items() if value}
                        for item in message.get("reference", [])
                    ]
                    records.append(
                        {
                            "paper_id": paper_id,
                            "provider": provider,
                            "provider_id": message.get("DOI", doi),
                            "retrieved_at": iso(),
                            "title": (message.get("title") or [paper["title"]])[0],
                            "authors": authors,
                            "year": date_parts[0][0] if date_parts and date_parts[0] else None,
                            "venue": (message.get("container-title") or [""])[0],
                            "doi": message.get("DOI", doi),
                            "url": message.get("URL", ""),
                            "references": [item for item in references if item],
                        }
                    )
                else:
                    identifier = quote(doi, safe="")
                    data = fetch_json(f"https://api.openalex.org/works/https://doi.org/{identifier}", user_agent, timeout)
                    records.append(
                        {
                            "paper_id": paper_id,
                            "provider": provider,
                            "provider_id": data.get("id", ""),
                            "retrieved_at": iso(),
                            "title": data.get("display_name") or paper["title"],
                            "authors": [
                                item.get("author", {}).get("display_name", "") for item in data.get("authorships", [])
                            ],
                            "year": data.get("publication_year"),
                            "venue": ((data.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                            "doi": str(data.get("doi") or doi),
                            "url": (data.get("primary_location") or {}).get("landing_page_url", ""),
                            "references": [{"provider_id": item} for item in data.get("referenced_works", [])],
                        }
                    )
            except Exception as exc:  # provider/network failures are reported per paper
                failures.append({"paper_id": paper_id, "error": str(exc)})
        if not records:
            raise ResearchError("metadata acquisition produced no records: " + "; ".join(item["error"] for item in failures))
        result = self.reconcile_metadata({"records": records})
        result["provider"] = provider
        result["acquired_records"] = len(records)
        result["failures"] = failures
        return result

    def evidence_synthesis(self) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for paper_id, paper in self.papers.items():
            if paper.get("status") not in {"read", "synthesized"}:
                continue
            for claim in paper.get("analysis", {}).get("claims", []):
                claims.append({"paper_id": paper_id, **claim, "tokens": synthesis_tokens(claim.get("statement"))})
        parents = list(range(len(claims)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        relation_lookup = {
            (paper_id, relation.get("paper_id")): relation.get("type")
            for paper_id, paper in self.papers.items()
            for relation in paper.get("analysis", {}).get("relations", [])
        }
        claims_per_paper = Counter(item["paper_id"] for item in claims)
        for left in range(len(claims)):
            for right in range(left + 1, len(claims)):
                if claims[left]["paper_id"] == claims[right]["paper_id"]:
                    continue
                overlap = claims[left]["tokens"] & claims[right]["tokens"]
                denominator = min(len(claims[left]["tokens"]), len(claims[right]["tokens"])) or 1
                explicit_relation = relation_lookup.get((claims[left]["paper_id"], claims[right]["paper_id"])) or relation_lookup.get(
                    (claims[right]["paper_id"], claims[left]["paper_id"])
                )
                if len(overlap) / denominator >= 0.3 or (
                    explicit_relation
                    and (overlap or (claims_per_paper[claims[left]["paper_id"]] == claims_per_paper[claims[right]["paper_id"]] == 1))
                ):
                    union(left, right)
        groups: dict[int, list[dict[str, Any]]] = {}
        for index, claim in enumerate(claims):
            groups.setdefault(find(index), []).append(claim)
        themes = []
        strength_score = {"strong": 4, "moderate": 3, "mixed": 2, "weak": 1, "unclear": 0}
        for theme_index, group in enumerate(groups.values(), start=1):
            paper_ids = unique(item["paper_id"] for item in group)
            relations = unique(
                relation_lookup.get((left, right)) or relation_lookup.get((right, left))
                for left in paper_ids
                for right in paper_ids
                if left != right and (relation_lookup.get((left, right)) or relation_lookup.get((right, left)))
            )
            contested = "contradicts" in relations
            corroborated = len(paper_ids) > 1 and any(item in relations for item in ["supports", "replicates", "extends"])
            average_strength = sum(strength_score[item.get("strength", "unclear")] for item in group) / len(group)
            evidence_grade = "strong" if len(paper_ids) >= 3 and average_strength >= 3 else "moderate" if len(paper_ids) >= 2 and average_strength >= 2 else "limited"
            if contested:
                evidence_grade = "contested"
            token_counts = Counter(token for item in group for token in item["tokens"])
            label = " / ".join(item for item, _ in token_counts.most_common(5)) or group[0]["statement"][:100]
            themes.append(
                {
                    "id": f"theme.{theme_index:03d}",
                    "label": label,
                    "assessment": "contested" if contested else "corroborated" if corroborated else "single_source",
                    "evidence_grade": evidence_grade,
                    "paper_ids": paper_ids,
                    "relation_types": relations,
                    "claims": [
                        {key: item[key] for key in ["paper_id", "id", "statement", "evidence_summary", "strength"]}
                        for item in group
                    ],
                    "limitations": unique(
                        limitation
                        for paper_id in paper_ids
                        for limitation in self.papers[paper_id].get("analysis", {}).get("limitations", [])
                    ),
                }
            )
        return {"generated_at": iso(), "source_paper_ids": unique(item["paper_id"] for item in claims), "themes": themes}

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
                if self.workspace.atoms.get(atom_id, {}).get("status") not in SATISFIED_STATUSES
            ]
            provisional = [
                atom_id
                for atom_id in paper.get("concept_atom_ids", [])
                if self.workspace.atoms.get(atom_id, {}).get("status") == "skipped"
            ]
            candidates.append(
                {
                    "id": paper_id,
                    "title": paper.get("title"),
                    "role": paper.get("role"),
                    "priority": paper.get("priority"),
                    "year": paper.get("year"),
                    "knowledge_gap_atom_ids": knowledge_gaps,
                    "provisional_knowledge_atom_ids": provisional,
                    "reason": self._reading_reason(paper, knowledge_gaps, provisional),
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

    def _reading_reason(
        self, paper: dict[str, Any], knowledge_gaps: list[str], provisional: list[str] | None = None
    ) -> str:
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
        if provisional:
            reason += " Verify provisional Knowledge Atom assumptions if comprehension breaks: " + ", ".join(provisional) + "."
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
            if self.workspace.atoms.get(atom_id, {}).get("status") not in SATISFIED_STATUSES
        ]
        provisional = [
            atom_id
            for atom_id in paper.get("concept_atom_ids", [])
            if self.workspace.atoms.get(atom_id, {}).get("status") == "skipped"
        ]
        return {
            "paper_id": paper_id,
            "knowledge_gap_atom_ids": gaps,
            "provisional_knowledge_atom_ids": provisional,
        }

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
        synthesis = self.evidence_synthesis()
        self.state["latest_synthesis"] = synthesis
        return {
            "integrated_paper_ids": integrated,
            "evidence_synthesis": synthesis,
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
            "metadata": dict(Counter(paper.get("metadata_verification", {}).get("status", "unverified") for paper in self.papers.values())),
            "synthesis_theme_count": len((self.state.get("latest_synthesis") or {}).get("themes", [])),
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
            active_provisional = [
                atom_id
                for atom_id in active.get("concept_atom_ids", [])
                if self.workspace.atoms.get(atom_id, {}).get("status") == "skipped"
            ]
            current_lines.extend(
                [
                    "## Reading Lens",
                    "",
                    f"- Role: {active.get('role')}",
                    f"- Why now: {self._reading_reason(active, [], active_provisional)}",
                    f"- Provisional concept assumptions: {compact(active_provisional, 'None')}",
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
        matrix_lines.extend(["", "## Cross-Paper Evidence Synthesis", ""])
        themes = (self.state.get("latest_synthesis") or {}).get("themes", [])
        for theme in themes:
            matrix_lines.append(
                f"- `{theme['id']}` **{markdown(theme['label'])}** — {theme['assessment']}, "
                f"evidence `{theme['evidence_grade']}`; papers: {', '.join(theme['paper_ids'])}"
            )
            matrix_lines.extend(
                f"  - `{claim['paper_id']}/{claim['id']}` [{claim['strength']}] {markdown(claim['statement'])} — {markdown(claim['evidence_summary'])}"
                for claim in theme["claims"]
            )
        if not themes:
            matrix_lines.append("- Not generated")

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
    reconcile = sub.add_parser("reconcile-metadata")
    reconcile.add_argument("workspace")
    reconcile.add_argument("--input", required=True)
    add_revision_argument(reconcile)
    fetch = sub.add_parser("fetch-metadata")
    fetch.add_argument("workspace")
    fetch.add_argument("--provider", choices=["crossref", "openalex"], default="crossref")
    fetch.add_argument("--timeout", type=float, default=15.0)
    fetch.add_argument("--mailto", default="")
    add_revision_argument(fetch)
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
    elif args.action == "reconcile-metadata":
        result = engine.reconcile_metadata(read_data(Path(args.input)))
        event_type = "research.metadata_reconciled"
    elif args.action == "fetch-metadata":
        result = engine.fetch_metadata(args.provider, args.timeout, args.mailto)
        event_type = "research.metadata_fetched"
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
