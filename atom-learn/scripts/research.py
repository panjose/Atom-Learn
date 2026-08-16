#!/usr/bin/env python3
"""Research-oriented paper reading and synthesis for AtomLearn."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
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
    require_number,
    require_string,
    unique,
    write_yaml,
)
from rag import RagEngine, RagError


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
SCREENING_STATUSES = {"candidate", "screening", "included", "excluded", "needs_review"}
DISCOVERY_PROVIDERS = {"harness", "crossref", "openalex"}
DISCOVERY_KINDS = {"query", "backward", "forward", "refresh"}
DISCOVERY_STATUSES = {"awaiting_submission", "completed", "partial", "failed"}
EVIDENCE_KINDS = {"sentence", "table", "figure", "equation", "block", "other"}
EXTRACTION_METHODS = {"human", "harness", "document_ir", "vision", "provider"}
FACET_FIELDS = ["population", "setting", "dataset", "method", "baseline", "outcome", "metric", "assumption"]
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
            paper.setdefault("discovery", None)
            paper.setdefault(
                "screening",
                {
                    "status": "candidate" if paper.get("status") == "discovered" else "needs_review" if paper.get("status") == "excluded" else "included",
                    "matched_criteria": [],
                    "exclusion_criterion": None,
                    "reason": paper.get("exclusion_reason") or "Migrated existing research record.",
                    "decision_source": "legacy-migration",
                    "decided_at": paper.get("updated_at"),
                },
            )
            paper.setdefault("integrity", {"status": "unknown", "provider": None, "checked_at": None, "source_locator": None})
            papers[paper_id] = paper
        state = read_data(state_path)
        state.setdefault("paper_aliases", {})
        state.setdefault("latest_synthesis", None)
        state.setdefault("protocol_revision", 0)
        state.setdefault("protocol", {
            "languages": [], "date_from": None, "date_to": None, "literature_types": [],
            "target_outcomes": [], "search_limits": [],
        })
        state.setdefault("discovery_log", [])
        state.setdefault("screening_log", [])
        state.setdefault("latest_refresh", None)
        engine = cls(workspace, state, papers)
        for paper_id, paper in papers.items():
            paper["analysis"] = engine._normalize_analysis(paper_id, paper.get("analysis", {}))
        return engine

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
        protocol_revision = self.state.get("protocol_revision", 0)
        if not isinstance(protocol_revision, int) or isinstance(protocol_revision, bool) or protocol_revision < 0:
            errors.append("research protocol_revision must be a non-negative integer")
        protocol = self.state.get("protocol", {})
        if not isinstance(protocol, dict) or set(protocol) != {
            "languages", "date_from", "date_to", "literature_types", "target_outcomes", "search_limits"
        }:
            errors.append("research protocol fields are invalid")
        else:
            try:
                for field in ["languages", "literature_types", "target_outcomes", "search_limits"]:
                    string_list(protocol.get(field, []), f"protocol.{field}")
                for field in ["date_from", "date_to"]:
                    if protocol.get(field) is not None:
                        date.fromisoformat(str(protocol[field]))
            except (ResearchError, ValueError) as exc:
                errors.append(str(exc))
        discovery_log = self.state.get("discovery_log", [])
        if not isinstance(discovery_log, list):
            errors.append("research discovery_log must be a list")
        else:
            seen_actions: set[str] = set()
            for index, action in enumerate(discovery_log):
                if not isinstance(action, dict):
                    errors.append(f"discovery_log[{index}] must be a mapping")
                    continue
                action_id = action.get("action_id")
                if action_id in seen_actions:
                    errors.append(f"duplicate discovery action: {action_id}")
                seen_actions.add(action_id)
                if action.get("kind") not in DISCOVERY_KINDS or action.get("provider") not in DISCOVERY_PROVIDERS:
                    errors.append(f"{action_id}: invalid discovery kind or provider")
                if action.get("status") not in DISCOVERY_STATUSES:
                    errors.append(f"{action_id}: invalid discovery status")
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
            screening = paper.get("screening")
            if not isinstance(screening, dict) or screening.get("status") not in SCREENING_STATUSES:
                errors.append(f"{paper_id}: invalid screening state")
            else:
                try:
                    string_list(screening.get("matched_criteria", []), f"{paper_id}.screening.matched_criteria")
                    require_text(screening.get("reason", ""), f"{paper_id}.screening.reason", allow_empty=True)
                except ResearchError as exc:
                    errors.append(str(exc))
                if screening.get("status") == "excluded" and screening.get("exclusion_criterion") not in self.state.get("exclusion_criteria", []):
                    errors.append(f"{paper_id}: screened exclusion must use a predeclared criterion")
            integrity = paper.get("integrity")
            if not isinstance(integrity, dict) or integrity.get("status") not in {"unknown", "not_retracted", "retracted", "concern"}:
                errors.append(f"{paper_id}: invalid integrity status")
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
            for field in ["populations", "settings", "methods", "baselines", "outcomes", "assumptions"]:
                string_list(analysis.get(field, []), f"{paper_id}.analysis.{field}")
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
            try:
                require_text(claim.get("effect", ""), f"{claim_id}.effect", allow_empty=True, limit=1000)
                require_text(claim.get("uncertainty", ""), f"{claim_id}.uncertainty", allow_empty=True, limit=1000)
                facets = claim.get("facets", {})
                if not isinstance(facets, dict) or set(facets) != set(FACET_FIELDS):
                    raise ResearchError(f"{claim_id}.facets must contain every structured facet field")
                for field in FACET_FIELDS:
                    string_list(facets[field], f"{claim_id}.facets.{field}")
                locator = self._normalize_evidence_locator(claim.get("evidence_locator"), f"{claim_id}.evidence_locator")
                if complete and not locator["locator"]:
                    errors.append(f"{claim_id}: completion requires a sentence, table, figure, equation, or block locator")
                if locator["block_ids"]:
                    if not locator["source_id"] or locator["source_revision"] is None:
                        errors.append(f"{claim_id}: block locators require source_id and source_revision")
                    else:
                        try:
                            document = RagEngine.load(str(self.workspace.root)).document_ir(locator["source_id"])
                            known_blocks = {item["block_id"] for item in document["blocks"]}
                            if document["source_revision"] != locator["source_revision"]:
                                errors.append(f"{claim_id}: evidence source revision is stale")
                            if any(item not in known_blocks for item in locator["block_ids"]):
                                errors.append(f"{claim_id}: evidence references unknown Document IR blocks")
                        except (RagError, OSError) as exc:
                            errors.append(f"{claim_id}: cannot verify Document IR evidence: {exc}")
            except (AtomLearnError, ResearchError) as exc:
                errors.append(str(exc))
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

    def set_protocol(self, payload: Any) -> dict[str, Any]:
        required = {
            "research_question", "scope", "languages", "date_from", "date_to", "literature_types",
            "inclusion_criteria", "exclusion_criteria", "target_outcomes", "search_limits",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ResearchError("research protocol must contain exactly the documented protocol fields")
        date_from = payload.get("date_from")
        date_to = payload.get("date_to")
        for value, label in [(date_from, "date_from"), (date_to, "date_to")]:
            if value is not None:
                try:
                    date.fromisoformat(str(value))
                except ValueError as exc:
                    raise ResearchError(f"protocol {label} must use YYYY-MM-DD or null") from exc
        if date_from and date_to and str(date_from) > str(date_to):
            raise ResearchError("protocol date_from cannot be after date_to")
        self.state["research_question"] = require_text(payload["research_question"], "research question", limit=2000)
        self.state["scope"] = require_text(payload["scope"], "scope", allow_empty=True, limit=2000)
        self.state["inclusion_criteria"] = string_list(payload["inclusion_criteria"], "inclusion criteria")
        self.state["exclusion_criteria"] = string_list(payload["exclusion_criteria"], "exclusion criteria")
        self.state["protocol"] = {
            "languages": string_list(payload["languages"], "languages", limit=100),
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "literature_types": string_list(payload["literature_types"], "literature types", limit=200),
            "target_outcomes": string_list(payload["target_outcomes"], "target outcomes"),
            "search_limits": string_list(payload["search_limits"], "search limits"),
        }
        self.state["protocol_revision"] = int(self.state.get("protocol_revision", 0)) + 1
        return {"protocol_revision": self.state["protocol_revision"], "protocol": copy.deepcopy(self.state["protocol"])}

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

    def attach_source(self, paper_id: str, source_id: str) -> dict[str, Any]:
        """Bind a paper to the active shared Document IR without copying full text."""
        paper_id = self.state.get("paper_aliases", {}).get(paper_id, paper_id)
        paper = self.papers.get(paper_id)
        if paper is None:
            raise ResearchError(f"Paper does not exist: {paper_id}")
        document = RagEngine.load(str(self.workspace.root)).document_ir(source_id)
        paper["locator"] = f"document-ir:{source_id}@r{document['source_revision']}"
        verification = paper.setdefault("metadata_verification", {"status": "unverified", "checks": {}})
        checks = verification.setdefault("checks", {})
        checks["document_ir"] = {
            "source_id": source_id,
            "source_revision": document["source_revision"],
            "content_sha256": document["content_sha256"],
            "block_count": len(document["blocks"]),
        }
        return {
            "paper_id": paper_id,
            "source_id": source_id,
            "source_revision": document["source_revision"],
            "content_sha256": document["content_sha256"],
            "block_count": len(document["blocks"]),
            "copied_full_text": False,
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
        discovery_duplicate = bool(existing and raw.get("discovery") and raw.get("status") == "discovered")
        if ("status" in raw and not discovery_duplicate) or not existing:
            paper["status"] = raw.get("status", paper.get("status", "queued"))
        if "analysis" in raw:
            paper["analysis"] = self._normalize_analysis(paper_id, raw["analysis"])
        if "metadata_verification" in raw:
            paper["metadata_verification"] = copy.deepcopy(raw["metadata_verification"])
        paper.setdefault("metadata_verification", {"status": "unverified", "checks": {}})
        if "discovery" in raw:
            if not isinstance(raw["discovery"], dict):
                raise ResearchError(f"{paper_id}.discovery must be a mapping")
            paper["discovery"] = copy.deepcopy(raw["discovery"])
        paper.setdefault("discovery", None)
        if "screening" in raw and not discovery_duplicate:
            if not isinstance(raw["screening"], dict):
                raise ResearchError(f"{paper_id}.screening must be a mapping")
            paper["screening"] = copy.deepcopy(raw["screening"])
        elif not existing:
            paper["screening"] = {
                "status": "candidate" if paper.get("status") == "discovered" else "included",
                "matched_criteria": [],
                "exclusion_criterion": None,
                "reason": "Imported directly into the reading corpus." if paper.get("status") != "discovered" else "",
                "decision_source": "human-import" if paper.get("status") != "discovered" else "discovery",
                "decided_at": timestamp if paper.get("status") != "discovered" else None,
            }
        if "integrity" in raw:
            if not isinstance(raw["integrity"], dict):
                raise ResearchError(f"{paper_id}.integrity must be a mapping")
            paper["integrity"] = copy.deepcopy(raw["integrity"])
        paper.setdefault(
            "integrity",
            {"status": "unknown", "provider": None, "checked_at": None, "source_locator": None},
        )
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
            "populations": string_list(raw.get("populations", []), "analysis.populations"),
            "settings": string_list(raw.get("settings", []), "analysis.settings"),
            "methods": string_list(raw.get("methods", []), "analysis.methods"),
            "baselines": string_list(raw.get("baselines", []), "analysis.baselines"),
            "outcomes": string_list(raw.get("outcomes", []), "analysis.outcomes"),
            "assumptions": string_list(raw.get("assumptions", []), "analysis.assumptions"),
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
            raw_facets = item.get("facets", {})
            if not isinstance(raw_facets, dict) or set(raw_facets) - set(FACET_FIELDS):
                raise ResearchError(f"{claim_id}.facets may contain only structured facet fields")
            facets = {
                field: string_list(raw_facets.get(field, []), f"{claim_id}.facets.{field}")
                for field in FACET_FIELDS
            }
            analysis["claims"].append(
                {
                    "id": claim_id,
                    "statement": require_text(item.get("statement"), f"{claim_id}.statement", limit=2000),
                    "evidence_summary": require_text(
                        item.get("evidence_summary"), f"{claim_id}.evidence_summary", limit=2000
                    ),
                    "strength": item.get("strength", "unclear"),
                    "effect": require_text(item.get("effect", ""), f"{claim_id}.effect", allow_empty=True, limit=1000),
                    "uncertainty": require_text(
                        item.get("uncertainty", ""), f"{claim_id}.uncertainty", allow_empty=True, limit=1000
                    ),
                    "facets": facets,
                    "evidence_locator": self._normalize_evidence_locator(
                        item.get("evidence_locator"), f"{claim_id}.evidence_locator"
                    ),
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

    def _normalize_evidence_locator(self, raw: Any, label: str) -> dict[str, Any]:
        if raw is None:
            raw = {}
        if isinstance(raw, str):
            raw = {
                "locator": raw, "kind": "other", "extraction_method": "human", "confidence": 1.0,
                "source_id": None, "source_revision": None, "block_ids": [],
            }
        required = {"locator", "kind", "extraction_method", "confidence", "source_id", "source_revision", "block_ids"}
        if not isinstance(raw, dict) or set(raw) - required:
            raise ResearchError(f"{label} has unsupported fields")
        kind = raw.get("kind", "other")
        method = raw.get("extraction_method", "human")
        if kind not in EVIDENCE_KINDS:
            raise ResearchError(f"{label}.kind is invalid")
        if method not in EXTRACTION_METHODS:
            raise ResearchError(f"{label}.extraction_method is invalid")
        source_id = raw.get("source_id")
        if source_id is not None:
            source_id = require_id(source_id, f"{label}.source_id")
        source_revision = raw.get("source_revision")
        if source_revision is not None and (
            not isinstance(source_revision, int) or isinstance(source_revision, bool) or source_revision < 1
        ):
            raise ResearchError(f"{label}.source_revision must be a positive integer or null")
        block_ids = raw.get("block_ids", [])
        if not isinstance(block_ids, list) or any(not isinstance(item, str) for item in block_ids):
            raise ResearchError(f"{label}.block_ids must be a string list")
        confidence = raw.get("confidence", 1.0 if raw.get("locator") else 0.5)
        return {
            "locator": require_text(raw.get("locator", ""), f"{label}.locator", allow_empty=True, limit=2000),
            "kind": kind,
            "extraction_method": method,
            "confidence": round(float(require_number(confidence, f"{label}.confidence", 0.5, 1.0)), 3),
            "source_id": source_id,
            "source_revision": source_revision,
            "block_ids": unique(block_ids),
        }

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
            if record.get("integrity_status") in {"unknown", "not_retracted", "retracted", "concern"}:
                paper["integrity"] = {
                    "status": record["integrity_status"],
                    "provider": provider,
                    "checked_at": str(record.get("retrieved_at") or iso()),
                    "source_locator": str(record.get("integrity_locator") or record.get("provider_id") or ""),
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
                            "integrity_status": "retracted" if any(
                                str(item.get("type", "")).casefold() == "retraction"
                                for item in message.get("update-to", [])
                            ) else "not_retracted",
                            "integrity_locator": message.get("URL", doi),
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
                            "integrity_status": "retracted" if data.get("is_retracted") is True else "not_retracted",
                            "integrity_locator": data.get("id", ""),
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

    def _next_discovery_action_id(self) -> str:
        return f"research-action-{len(self.state.get('discovery_log', [])) + 1:06d}"

    def _create_discovery_action(
        self,
        *,
        kind: str,
        provider: str,
        query: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        seed_paper_id: str | None = None,
        direction: str | None = None,
        depth: int = 0,
        stopping_rule: str = "",
    ) -> dict[str, Any]:
        if kind not in DISCOVERY_KINDS or provider not in DISCOVERY_PROVIDERS:
            raise ResearchError("unsupported discovery kind or provider")
        if not 1 <= limit <= 200:
            raise ResearchError("discovery limit must be from 1 through 200")
        if from_year is not None and not 1000 <= from_year <= 3000:
            raise ResearchError("from-year must be from 1000 through 3000")
        if to_year is not None and not 1000 <= to_year <= 3000:
            raise ResearchError("to-year must be from 1000 through 3000")
        if from_year and to_year and from_year > to_year:
            raise ResearchError("from-year cannot be after to-year")
        action_id = self._next_discovery_action_id()
        record = {
            "action_id": action_id,
            "kind": kind,
            "provider": provider,
            "query": require_text(query, "discovery query", limit=2000),
            "filters": {"from_year": from_year, "to_year": to_year, "limit": limit},
            "seed_paper_id": seed_paper_id,
            "direction": direction,
            "depth": depth,
            "stopping_rule": stopping_rule,
            "protocol_revision": self.state.get("protocol_revision", 0),
            "created_at": iso(),
            "status": "awaiting_submission",
            "completed_at": None,
            "result_ids": [],
            "complete": False,
            "failure": None,
        }
        self.state.setdefault("discovery_log", []).append(record)
        return {
            "kind": "atomlearn.research.discovery.v1",
            "action_id": action_id,
            "research_revision": self.revision,
            "protocol_revision": record["protocol_revision"],
            "operation": kind,
            "provider": provider,
            "query": record["query"],
            "filters": record["filters"],
            "seed_paper_id": seed_paper_id,
            "direction": direction,
            "depth": depth,
            "stopping_rule": stopping_rule,
            "submission_schema": "atom-learn/assets/schemas/research-discovery-submission.schema.json",
            "instructions": (
                "Return bibliographic candidates and provider IDs with the exact query/filter provenance. "
                "Do not claim exhaustive coverage; submit results with `research submit-discovery`."
            ),
        }

    @staticmethod
    def _crossref_candidate(item: dict[str, Any]) -> dict[str, Any]:
        date_parts = (item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts", [[]])
        updates = item.get("update-to", [])
        integrity = "retracted" if any(str(update.get("type", "")).casefold() == "retraction" for update in updates) else "not_retracted"
        return {
            "provider_id": str(item.get("DOI") or ""),
            "title": (item.get("title") or [""])[0],
            "authors": [
                " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part).strip()
                for author in item.get("author", [])
            ],
            "year": date_parts[0][0] if date_parts and date_parts[0] else None,
            "venue": (item.get("container-title") or [""])[0],
            "doi": item.get("DOI", ""),
            "url": item.get("URL", ""),
            "references": [
                {key: value for key, value in {"doi": ref.get("DOI"), "title": ref.get("article-title")}.items() if value}
                for ref in item.get("reference", [])
            ],
            "integrity_status": integrity,
            "integrity_locator": str(item.get("URL") or item.get("DOI") or ""),
        }

    @staticmethod
    def _openalex_candidate(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": str(item.get("id") or ""),
            "title": item.get("display_name") or item.get("title") or "",
            "authors": [entry.get("author", {}).get("display_name", "") for entry in item.get("authorships", [])],
            "year": item.get("publication_year"),
            "venue": ((item.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
            "doi": str(item.get("doi") or ""),
            "url": (item.get("primary_location") or {}).get("landing_page_url", ""),
            "references": [{"provider_id": reference} for reference in item.get("referenced_works", [])],
            "integrity_status": "retracted" if item.get("is_retracted") is True else "not_retracted",
            "integrity_locator": str(item.get("id") or ""),
        }

    def discover(
        self,
        query: str,
        provider: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        timeout: float,
        mailto: str,
    ) -> dict[str, Any]:
        action = self._create_discovery_action(
            kind="query", provider=provider, query=query, limit=limit, from_year=from_year, to_year=to_year
        )
        if provider == "harness":
            return {"action": action, "submission_required": True}
        if not 1 <= timeout <= 60:
            raise ResearchError("discovery timeout must be between 1 and 60 seconds")
        user_agent = "AtomLearn/0.14 (+https://github.com/panjose/Atom-Learn)"
        if mailto:
            user_agent += f" mailto:{mailto}"
        try:
            if provider == "crossref":
                filters = []
                if from_year:
                    filters.append(f"from-pub-date:{from_year}-01-01")
                if to_year:
                    filters.append(f"until-pub-date:{to_year}-12-31")
                params = {"query.bibliographic": query, "rows": limit, "select": "DOI,title,author,published-print,published-online,issued,container-title,URL,reference,update-to"}
                if filters:
                    params["filter"] = ",".join(filters)
                data = fetch_json(f"https://api.crossref.org/works?{urlencode(params)}", user_agent, timeout)
                records = [self._crossref_candidate(item) for item in data.get("message", {}).get("items", [])]
            else:
                filters = []
                if from_year:
                    filters.append(f"from_publication_date:{from_year}-01-01")
                if to_year:
                    filters.append(f"to_publication_date:{to_year}-12-31")
                params = {"search": query, "per-page": limit}
                if filters:
                    params["filter"] = ",".join(filters)
                if mailto:
                    params["mailto"] = mailto
                data = fetch_json(f"https://api.openalex.org/works?{urlencode(params)}", user_agent, timeout)
                records = [self._openalex_candidate(item) for item in data.get("results", [])]
        except Exception as exc:
            log = next(item for item in self.state["discovery_log"] if item["action_id"] == action["action_id"])
            log["status"] = "failed"
            log["failure"] = str(exc)
            log["completed_at"] = iso()
            return {
                "action_id": action["action_id"],
                "action_status": "failed",
                "failure": str(exc),
                "retryable": True,
                "coverage_claim": "no_discovery_coverage",
            }
        return self.submit_discovery(
            {
                "action_id": action["action_id"],
                "retrieved_at": iso(),
                "records": records,
                "complete": True,
                "failure": None,
            }
        )

    @staticmethod
    def _discovered_paper_id(record: dict[str, Any]) -> str:
        identity = normalize_doi(record.get("doi", "")) or str(record.get("provider_id") or title_fingerprint(record.get("title")))
        digest = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:16]
        return f"paper.discovered.{digest}"

    def submit_discovery(self, payload: Any) -> dict[str, Any]:
        required = {"action_id", "retrieved_at", "records", "complete", "failure"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ResearchError("discovery submission must contain exactly action_id, retrieved_at, records, complete, and failure")
        action_id = require_id(payload.get("action_id"), "action_id")
        action = next((item for item in self.state.get("discovery_log", []) if item.get("action_id") == action_id), None)
        if action is None:
            raise ResearchError(f"discovery action does not exist: {action_id}")
        if action.get("status") != "awaiting_submission":
            raise ResearchError(f"discovery action is not awaiting submission: {action_id}")
        records = payload.get("records")
        if not isinstance(records, list):
            raise ResearchError("discovery records must be a list")
        papers: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ResearchError(f"records[{index}] must be a mapping")
            title = require_text(record.get("title"), f"records[{index}].title", limit=1000)
            paper_id = self._discovered_paper_id(record)
            references = record.get("references", [])
            if not isinstance(references, list):
                raise ResearchError(f"records[{index}].references must be a list")
            role = "survey" if "survey" in title.casefold() or "review" in title.casefold() else "method"
            paper = {
                "id": paper_id,
                "title": title,
                "authors": string_list(record.get("authors", []), f"records[{index}].authors", limit=500),
                "year": record.get("year"),
                "venue": str(record.get("venue") or ""),
                "doi": str(record.get("doi") or ""),
                "url": str(record.get("url") or ""),
                "locator": str(record.get("url") or record.get("provider_id") or ""),
                "role": role,
                "priority": 3,
                "status": "discovered",
                "prerequisite_paper_ids": [],
                "cites": [],
                "external_citations": unique(
                    normalize_doi(item.get("doi", "")) or str(item.get("provider_id") or item.get("title") or "")
                    for item in references if isinstance(item, dict)
                ),
                "concept_atom_ids": [],
                "tags": ["discovery-candidate"],
                "discovery": {
                    "action_id": action_id,
                    "provider": action["provider"],
                    "provider_id": str(record.get("provider_id") or ""),
                    "query": action["query"],
                    "retrieved_at": str(payload.get("retrieved_at")),
                    "kind": action["kind"],
                    "seed_paper_id": action.get("seed_paper_id"),
                    "direction": action.get("direction"),
                    "depth": action.get("depth", 0),
                },
                "screening": {
                    "status": "candidate", "matched_criteria": [], "exclusion_criterion": None,
                    "reason": "", "decision_source": "discovery", "decided_at": None,
                },
                "integrity": {
                    "status": record.get("integrity_status", "unknown"),
                    "provider": action["provider"],
                    "checked_at": str(payload.get("retrieved_at")),
                    "source_locator": str(record.get("integrity_locator") or record.get("provider_id") or ""),
                },
            }
            papers.append(paper)
        imported = self.import_plan({"papers": papers}) if papers else {"imported_paper_ids": [], "deduplicated": [], "total_papers": len(self.papers)}
        result_ids = imported["imported_paper_ids"]
        doi_index, title_index = self._metadata_indexes()
        for record in records:
            record_doi = normalize_doi(record.get("doi", ""))
            canonical_id = doi_index.get(record_doi) if record_doi else None
            if canonical_id is None:
                canonical_id = title_index.get(title_fingerprint(record.get("title")))
            if canonical_id and record.get("integrity_status") in {"unknown", "not_retracted", "retracted", "concern"}:
                self.papers[canonical_id]["integrity"] = {
                    "status": record["integrity_status"],
                    "provider": action["provider"],
                    "checked_at": str(payload.get("retrieved_at")),
                    "source_locator": str(record.get("integrity_locator") or record.get("provider_id") or ""),
                }
        seed_id = action.get("seed_paper_id")
        if seed_id in self.papers and action.get("kind") == "backward":
            self.papers[seed_id]["cites"] = unique([
                *self.papers[seed_id].get("cites", []), *(item for item in result_ids if item != seed_id)
            ])
        elif seed_id in self.papers and action.get("kind") == "forward":
            for result_id in result_ids:
                if result_id != seed_id and result_id in self.papers:
                    self.papers[result_id]["cites"] = unique([*self.papers[result_id].get("cites", []), seed_id])
        action["result_ids"] = result_ids
        action["complete"] = payload.get("complete") is True
        action["failure"] = str(payload.get("failure") or "") or None
        action["status"] = "completed" if action["complete"] and not action["failure"] else "partial" if result_ids else "failed"
        action["completed_at"] = iso()
        if action["kind"] == "refresh":
            self.state["latest_refresh"] = {"action_id": action_id, "at": action["completed_at"], "status": action["status"]}
        return {
            **imported,
            "action_id": action_id,
            "action_status": action["status"],
            "retrieval_complete": action["complete"],
            "coverage_claim": "bounded_provider_results_not_exhaustive",
        }

    def snowball(
        self,
        paper_id: str,
        direction: str,
        provider: str,
        depth: int,
        limit: int,
        stopping_rule: str,
    ) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if direction not in {"backward", "forward"}:
            raise ResearchError("snowball direction must be backward or forward")
        if not 1 <= depth <= 5:
            raise ResearchError("snowball depth must be from 1 through 5")
        action = self._create_discovery_action(
            kind=direction,
            provider=provider,
            query=paper.get("doi") or paper.get("title"),
            limit=limit,
            from_year=None,
            to_year=None,
            seed_paper_id=paper_id,
            direction=direction,
            depth=depth,
            stopping_rule=require_text(stopping_rule, "stopping rule", limit=1000),
        )
        action["known_identifiers"] = paper.get("external_citations", []) if direction == "backward" else []
        return {"action": action, "submission_required": True}

    def refresh(self, provider: str, limit: int) -> dict[str, Any]:
        included = [
            paper_id for paper_id, paper in self.papers.items()
            if paper.get("screening", {}).get("status") == "included" and paper.get("status") != "excluded"
        ]
        saved_queries = [item["query"] for item in self.state.get("discovery_log", []) if item.get("kind") == "query"]
        query = " OR ".join(unique(saved_queries)) or self.state.get("research_question")
        action = self._create_discovery_action(
            kind="refresh", provider=provider, query=query, limit=limit, from_year=None, to_year=None,
            stopping_rule="Check saved queries, included-paper metadata updates, corrections, and retractions.",
        )
        action["included_paper_ids"] = included
        action["saved_queries"] = unique(saved_queries)
        action["integrity_fields_required"] = ["integrity_status", "integrity_locator"]
        return {"action": action, "submission_required": True}

    def screen(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"decisions"} or not isinstance(payload.get("decisions"), list):
            raise ResearchError("screening payload must contain exactly a decisions list")
        included: list[str] = []
        excluded: list[str] = []
        needs_review: list[str] = []
        for index, decision in enumerate(payload["decisions"]):
            required = {"paper_id", "decision", "matched_criteria", "exclusion_criterion", "reason", "confirmed"}
            if not isinstance(decision, dict) or set(decision) != required:
                raise ResearchError(f"decisions[{index}] has invalid fields")
            paper = self.paper(require_id(decision.get("paper_id"), f"decisions[{index}].paper_id"))
            outcome = decision.get("decision")
            if outcome not in {"include", "exclude", "needs_review"}:
                raise ResearchError(f"decisions[{index}].decision must be include, exclude, or needs_review")
            matched = string_list(decision.get("matched_criteria"), f"decisions[{index}].matched_criteria")
            unknown = sorted(set(matched) - set(self.state.get("inclusion_criteria", [])))
            if unknown:
                raise ResearchError("screening matched criteria were not predeclared: " + ", ".join(unknown))
            exclusion = decision.get("exclusion_criterion")
            if exclusion is not None and exclusion not in self.state.get("exclusion_criteria", []):
                raise ResearchError("screening exclusion criterion was not predeclared")
            confirmed = decision.get("confirmed") is True
            final_outcome = outcome if confirmed or outcome == "needs_review" else "needs_review"
            if final_outcome == "include" and not matched:
                raise ResearchError("confirmed inclusion requires at least one predeclared inclusion criterion")
            self.state.setdefault("screening_log", []).append(
                {
                    "paper_id": paper["id"], "status": "screening", "matched_criteria": [],
                    "exclusion_criterion": None, "reason": "Protocol criteria evaluation started.",
                    "decision_source": "core-transition", "decided_at": iso(),
                }
            )
            screening = {
                "status": "included" if final_outcome == "include" else "excluded" if final_outcome == "exclude" else "needs_review",
                "matched_criteria": matched,
                "exclusion_criterion": exclusion,
                "reason": require_text(decision.get("reason"), f"decisions[{index}].reason", limit=2000),
                "decision_source": "confirmed" if confirmed else "model_proposal",
                "decided_at": iso(),
            }
            if final_outcome == "exclude" and exclusion is None:
                raise ResearchError("confirmed exclusion requires a predeclared exclusion_criterion")
            paper["screening"] = screening
            if final_outcome == "include":
                paper["status"] = "queued"
                included.append(paper["id"])
            elif final_outcome == "exclude":
                paper["status"] = "excluded"
                paper["exclusion_reason"] = screening["reason"]
                excluded.append(paper["id"])
            else:
                paper["status"] = "discovered"
                needs_review.append(paper["id"])
            self.state.setdefault("screening_log", []).append({"paper_id": paper["id"], **screening})
        return {
            "included_paper_ids": included,
            "excluded_paper_ids": excluded,
            "needs_review_paper_ids": needs_review,
            "counts": self.screening_summary(),
        }

    def screening_summary(self) -> dict[str, Any]:
        counts = Counter(paper.get("screening", {}).get("status", "candidate") for paper in self.papers.values())
        actions = self.state.get("discovery_log", [])
        return {
            "candidate": counts.get("candidate", 0),
            "screening": counts.get("screening", 0),
            "included": counts.get("included", 0),
            "excluded": counts.get("excluded", 0),
            "needs_review": counts.get("needs_review", 0),
            "discovery_actions": len(actions),
            "completed_actions": sum(item.get("status") == "completed" for item in actions),
            "claim": "PRISMA-style audit counts only; retrieval is not asserted exhaustive.",
        }

    def evidence_synthesis(self) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for paper_id, paper in self.papers.items():
            if paper.get("status") not in {"read", "synthesized"}:
                continue
            for claim in paper.get("analysis", {}).get("claims", []):
                facets = claim.get("facets", {field: [] for field in FACET_FIELDS})
                claims.append(
                    {
                        "paper_id": paper_id,
                        **claim,
                        "facets": facets,
                        "tokens": synthesis_tokens(claim.get("statement")),
                    }
                )
        parents = list(range(len(claims)))
        merge_reasons: dict[tuple[int, int], dict[str, Any]] = {}

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
                shared_facets = {
                    field: sorted(set(claims[left]["facets"].get(field, [])) & set(claims[right]["facets"].get(field, [])))
                    for field in FACET_FIELDS
                }
                outcome_anchor = bool(shared_facets["outcome"] or shared_facets["metric"])
                context_anchor = bool(
                    shared_facets["population"] or shared_facets["dataset"] or shared_facets["method"]
                    or shared_facets["setting"] or shared_facets["assumption"]
                )
                structured_match = outcome_anchor and context_anchor
                explicit_match = bool(explicit_relation) and (
                    bool(overlap)
                    or structured_match
                    or claims_per_paper[claims[left]["paper_id"]] == claims_per_paper[claims[right]["paper_id"]] == 1
                )
                if structured_match or explicit_match:
                    union(left, right)
                    merge_reasons[(left, right)] = {
                        "basis": "explicit_relation" if explicit_match else "structured_facets",
                        "relation": explicit_relation,
                        "shared_facets": {field: values for field, values in shared_facets.items() if values},
                        "token_overlap": round(len(overlap) / denominator, 3),
                    }
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
            facet_counts = Counter(
                value
                for item in group
                for field in ["outcome", "metric", "method", "dataset"]
                for value in item["facets"].get(field, [])
            )
            label = " / ".join(item for item, _ in facet_counts.most_common(5)) or " / ".join(
                item for item, _ in token_counts.most_common(5)
            ) or group[0]["statement"][:100]
            conditional_differences = {
                field: sorted({value for item in group for value in item["facets"].get(field, [])})
                for field in FACET_FIELDS
            }
            conditional_differences = {field: values for field, values in conditional_differences.items() if len(values) > 1}
            group_indexes = [claims.index(item) for item in group]
            reasons = [
                reason for pair, reason in merge_reasons.items()
                if pair[0] in group_indexes and pair[1] in group_indexes
            ]
            themes.append(
                {
                    "id": f"theme.{theme_index:03d}",
                    "label": label,
                    "assessment": "contested" if contested else "corroborated" if corroborated else "single_source",
                    "evidence_grade": evidence_grade,
                    "review_status": "proposed",
                    "merge_basis": unique(reason["basis"] for reason in reasons) or ["single_source"],
                    "merge_evidence": reasons,
                    "paper_ids": paper_ids,
                    "relation_types": relations,
                    "claims": [
                        {key: item[key] for key in [
                            "paper_id", "id", "statement", "evidence_summary", "strength", "effect",
                            "uncertainty", "facets", "evidence_locator",
                        ]}
                        for item in group
                    ],
                    "conditional_differences": conditional_differences,
                    "conditional_conflict": contested and bool(conditional_differences),
                    "limitations": unique(
                        limitation
                        for paper_id in paper_ids
                        for limitation in self.papers[paper_id].get("analysis", {}).get("limitations", [])
                    ),
                }
            )
        return {
            "generated_at": iso(),
            "source_paper_ids": unique(item["paper_id"] for item in claims),
            "themes": themes,
            "coverage_claim": "bounded_included_corpus_not_exhaustive",
            "unsupported_novelty_claims_allowed": False,
        }

    def review_synthesis(self, payload: Any) -> dict[str, Any]:
        synthesis = self.state.get("latest_synthesis")
        if not isinstance(synthesis, dict):
            raise ResearchError("No synthesis exists; run `research synthesize` first")
        if not isinstance(payload, dict) or set(payload) != {"reviews"} or not isinstance(payload.get("reviews"), list):
            raise ResearchError("synthesis review payload must contain exactly a reviews list")
        themes = {item["id"]: item for item in synthesis.get("themes", [])}
        reviewed: list[str] = []
        for index, review in enumerate(payload["reviews"]):
            if not isinstance(review, dict) or set(review) != {"theme_id", "decision", "label", "reason"}:
                raise ResearchError(f"reviews[{index}] has invalid fields")
            theme_id = require_id(review.get("theme_id"), f"reviews[{index}].theme_id")
            theme = themes.get(theme_id)
            if theme is None:
                raise ResearchError(f"synthesis theme does not exist: {theme_id}")
            decision = review.get("decision")
            if decision not in {"confirm", "reject", "relabel"}:
                raise ResearchError(f"reviews[{index}].decision must be confirm, reject, or relabel")
            if any(not claim.get("evidence_locator", {}).get("locator") for claim in theme["claims"]):
                raise ResearchError(f"{theme_id}: every claim needs a source locator before theme review")
            theme["review_status"] = "confirmed" if decision in {"confirm", "relabel"} else "rejected"
            if decision == "relabel":
                theme["label"] = require_text(review.get("label"), f"reviews[{index}].label", limit=500)
            theme["review_reason"] = require_text(review.get("reason"), f"reviews[{index}].reason", limit=2000)
            reviewed.append(theme_id)
        return {
            "theme_ids": reviewed,
            "confirmed": sum(item.get("review_status") == "confirmed" for item in themes.values()),
            "remaining_proposals": sum(item.get("review_status") == "proposed" for item in themes.values()),
        }

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
            if paper.get("screening", {}).get("status") != "included":
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
        if paper.get("screening", {}).get("status") != "included":
            raise ResearchError(f"Paper {paper_id} must have a confirmed inclusion decision before reading")
        if paper.get("integrity", {}).get("status") in {"retracted", "concern"}:
            raise ResearchError(f"Paper {paper_id} has an integrity alert and requires explicit screening review")
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

    def exclude(self, paper_id: str, reason: str, criterion: str) -> dict[str, Any]:
        paper = self.paper(paper_id)
        if paper.get("status") == "active":
            raise ResearchError("Park the Active Paper before excluding it")
        if criterion not in self.state.get("exclusion_criteria", []):
            raise ResearchError("exclusion criterion must be one of the predeclared research protocol criteria")
        paper["status"] = "excluded"
        paper["exclusion_reason"] = require_text(reason, "exclusion reason", limit=2000)
        paper["screening"] = {
            "status": "excluded", "matched_criteria": [], "exclusion_criterion": criterion,
            "reason": paper["exclusion_reason"], "decision_source": "confirmed", "decided_at": iso(),
        }
        return {"paper_id": paper_id, "status": "excluded", "exclusion_criterion": criterion}

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
            "integrity": dict(Counter(paper.get("integrity", {}).get("status", "unknown") for paper in self.papers.values())),
            "screening": self.screening_summary(),
            "protocol_revision": self.state.get("protocol_revision", 0),
            "discovery": {
                "action_count": len(self.state.get("discovery_log", [])),
                "awaiting_submission": sum(item.get("status") == "awaiting_submission" for item in self.state.get("discovery_log", [])),
                "latest_refresh": self.state.get("latest_refresh"),
                "coverage_claim": "bounded_provider_results_not_exhaustive",
            },
            "synthesis_theme_count": len((self.state.get("latest_synthesis") or {}).get("themes", [])),
            "confirmed_synthesis_theme_count": sum(
                item.get("review_status") == "confirmed"
                for item in (self.state.get("latest_synthesis") or {}).get("themes", [])
            ),
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
                f"- `{theme['id']}` **{markdown(theme['label'])}** — {theme['assessment']}, review `{theme.get('review_status', 'proposed')}`, "
                f"evidence `{theme['evidence_grade']}`; papers: {', '.join(theme['paper_ids'])}"
            )
            matrix_lines.extend(
                f"  - `{claim['paper_id']}/{claim['id']}` [{claim['strength']}] {markdown(claim['statement'])} — "
                f"{markdown(claim['evidence_summary'])}; locator: {markdown(claim.get('evidence_locator', {}).get('locator'))}"
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
    initialize = sub.add_parser("init", help="Initialize a question-oriented research-reading workspace")
    initialize.add_argument("workspace")
    initialize.add_argument("--field", required=True)
    initialize.add_argument("--question", required=True)
    initialize.add_argument("--scope", default="")
    initialize.add_argument("--include", action="append", default=[])
    initialize.add_argument("--exclude", action="append", default=[])
    simple_help = {
        "status": "Show research revision, validity, active paper, and next candidates",
        "validate": "Validate paper identities, graph, notes, metadata, and synthesis state",
        "list": "List papers with optional role and status filters",
        "next": "Rank readable papers whose paper prerequisites are complete",
        "render": "Regenerate research map, active paper, matrix, and gap views",
    }
    for action in ["status", "validate", "list", "next", "render"]:
        parser_for_action = sub.add_parser(action, help=simple_help[action])
        parser_for_action.add_argument("workspace")
        if action == "list":
            parser_for_action.add_argument("--status")
            parser_for_action.add_argument("--role")
    import_parser = sub.add_parser("import", help="Import, deduplicate, and normalize a paper map")
    import_parser.add_argument("workspace")
    import_parser.add_argument("--input", required=True)
    add_revision_argument(import_parser)
    protocol = sub.add_parser("set-protocol", help="Persist a revisioned discovery and screening protocol before search")
    protocol.add_argument("workspace")
    protocol.add_argument("--input", required=True)
    add_revision_argument(protocol)
    discover = sub.add_parser("discover", help="Search Crossref/OpenAlex or emit a typed harness Web Search action")
    discover.add_argument("workspace")
    discover.add_argument("--query")
    discover.add_argument("--provider", choices=sorted(DISCOVERY_PROVIDERS), default="harness")
    discover.add_argument("--limit", type=int, default=25)
    discover.add_argument("--from-year", type=int)
    discover.add_argument("--to-year", type=int)
    discover.add_argument("--timeout", type=float, default=15.0)
    discover.add_argument("--mailto", default="")
    add_revision_argument(discover)
    submit_discovery = sub.add_parser("submit-discovery", help="Validate and import one revision-bound discovery/snowball/refresh result")
    submit_discovery.add_argument("workspace")
    submit_discovery.add_argument("--input", required=True)
    add_revision_argument(submit_discovery)
    screen = sub.add_parser("screen", help="Apply confirmed protocol-bound include/exclude decisions or retain model proposals for review")
    screen.add_argument("workspace")
    screen.add_argument("--input", required=True)
    add_revision_argument(screen)
    snowball = sub.add_parser("snowball", help="Emit a reproducible backward or forward citation-expansion action")
    snowball.add_argument("workspace")
    snowball.add_argument("paper_id")
    snowball.add_argument("--direction", choices=["backward", "forward"], required=True)
    snowball.add_argument("--provider", choices=sorted(DISCOVERY_PROVIDERS), default="openalex")
    snowball.add_argument("--depth", type=int, default=1)
    snowball.add_argument("--limit", type=int, default=50)
    snowball.add_argument("--stopping-rule", required=True)
    add_revision_argument(snowball)
    refresh = sub.add_parser("refresh", help="Emit an on-demand saved-query, metadata, correction, and retraction refresh action")
    refresh.add_argument("workspace")
    refresh.add_argument("--provider", choices=sorted(DISCOVERY_PROVIDERS), default="harness")
    refresh.add_argument("--limit", type=int, default=50)
    add_revision_argument(refresh)
    reconcile = sub.add_parser("reconcile-metadata", help="Verify provider metadata and resolve outgoing citations")
    reconcile.add_argument("workspace")
    reconcile.add_argument("--input", required=True)
    add_revision_argument(reconcile)
    fetch = sub.add_parser("fetch-metadata", help="Fetch DOI metadata and references from Crossref or OpenAlex")
    fetch.add_argument("workspace")
    fetch.add_argument("--provider", choices=["crossref", "openalex"], default="crossref")
    fetch.add_argument("--timeout", type=float, default=15.0)
    fetch.add_argument("--mailto", default="")
    add_revision_argument(fetch)
    attach = sub.add_parser("attach-source", help="Bind a paper to an indexed source through shared Document IR")
    attach.add_argument("workspace")
    attach.add_argument("paper_id")
    attach.add_argument("--source-id", required=True)
    add_revision_argument(attach)
    mutation_help = {
        "activate": "Activate one eligible paper for critical reading",
        "complete": "Mark the Active Paper read after its critical-note guard passes",
        "synthesize": "Build provenance-preserving cross-paper evidence themes",
    }
    for action in ["activate", "complete", "synthesize"]:
        parser_for_action = sub.add_parser(action, help=mutation_help[action])
        parser_for_action.add_argument("workspace")
        if action != "synthesize":
            parser_for_action.add_argument("paper_id")
        add_revision_argument(parser_for_action)
    note = sub.add_parser("note", help="Record a structured critical note for the Active Paper")
    note.add_argument("workspace")
    note.add_argument("paper_id")
    note.add_argument("--input", required=True)
    add_revision_argument(note)
    for action in ["park", "exclude"]:
        parser_for_action = sub.add_parser(
            action,
            help="Defer a paper without losing it" if action == "park" else "Exclude an out-of-scope paper with a reason",
        )
        parser_for_action.add_argument("workspace")
        parser_for_action.add_argument("paper_id")
        parser_for_action.add_argument("--reason", required=True)
        if action == "exclude":
            parser_for_action.add_argument("--criterion", required=True)
        add_revision_argument(parser_for_action)
    review_synthesis = sub.add_parser("review-synthesis", help="Confirm, relabel, or reject structured cross-paper theme proposals")
    review_synthesis.add_argument("workspace")
    review_synthesis.add_argument("--input", required=True)
    add_revision_argument(review_synthesis)
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
    elif args.action == "set-protocol":
        result = engine.set_protocol(read_data(Path(args.input)))
        event_type = "research.protocol_updated"
    elif args.action == "discover":
        result = engine.discover(
            args.query or engine.state["research_question"], args.provider, args.limit,
            args.from_year, args.to_year, args.timeout, args.mailto,
        )
        event_type = "research.discovery_started"
    elif args.action == "submit-discovery":
        result = engine.submit_discovery(read_data(Path(args.input)))
        event_type = "research.discovery_submitted"
    elif args.action == "screen":
        result = engine.screen(read_data(Path(args.input)))
        event_type = "research.screening_decided"
    elif args.action == "snowball":
        result = engine.snowball(
            args.paper_id, args.direction, args.provider, args.depth, args.limit, args.stopping_rule
        )
        event_type = "research.snowball_started"
    elif args.action == "refresh":
        result = engine.refresh(args.provider, args.limit)
        event_type = "research.refresh_started"
    elif args.action == "reconcile-metadata":
        result = engine.reconcile_metadata(read_data(Path(args.input)))
        event_type = "research.metadata_reconciled"
    elif args.action == "fetch-metadata":
        result = engine.fetch_metadata(args.provider, args.timeout, args.mailto)
        event_type = "research.metadata_fetched"
    elif args.action == "attach-source":
        result = engine.attach_source(args.paper_id, args.source_id)
        event_type = "research.source_attached"
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
        result = engine.exclude(args.paper_id, args.reason, args.criterion)
        event_type = "research.paper_excluded"
    elif args.action == "synthesize":
        result = engine.synthesize()
        event_type = "research.synthesized"
    elif args.action == "review-synthesis":
        result = engine.review_synthesis(read_data(Path(args.input)))
        event_type = "research.synthesis_reviewed"
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
    except (ResearchError, RagError, AtomLearnError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
