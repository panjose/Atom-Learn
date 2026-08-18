#!/usr/bin/env python3
"""One-input, resumable bootstrap orchestration for AtomLearn workspaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import (
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
from intake import IntakeEngine, IntakeError
from rag import AUTHORITATIVE, RagEngine, RagError
from workflow import WorkflowError, make_action, validate_submission


START_SCHEMA_VERSION = 1
SOURCE_TYPES = {"pdf", "book", "notes", "documentation", "website", "database", "outline", "exam", "other"}
TOPIC_DECISIONS = ("starting_point", "target_depth", "use_case")


class WizardError(RuntimeError):
    """A user-correctable start/wizard error."""


def asset_path(*parts: str) -> Path:
    from core_paths import CORE_ROOT

    return CORE_ROOT.joinpath("assets", *parts)


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WizardError("start payload must be a mapping")
    schema = json.loads(asset_path("schemas", "start.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        formatted = []
        for error in errors:
            location = ".".join(str(item) for item in error.absolute_path) or "$"
            formatted.append(f"{location}: {error.message}")
        raise WizardError("start payload does not match start.schema.json:\n- " + "\n- ".join(formatted))
    return payload


def validate_topic_diagnostic(payload: Any, unresolved: list[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkflowError("topic_diagnostic must be a mapping")
    schema = json.loads(asset_path("schemas", "topic-diagnostic.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        details = [
            (".".join(str(part) for part in error.absolute_path) or "$") + ": " + error.message
            for error in errors
        ]
        raise WorkflowError("topic diagnostic is invalid:\n- " + "\n- ".join(details))
    strategy = payload["strategy"]
    items = payload["items"]
    item_ids = [item["id"] for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise WorkflowError("topic diagnostic item IDs must be unique")
    if strategy == "adaptive_diagnostic":
        decisions = {item["decision"] for item in items}
        missing = sorted(set(unresolved) - decisions)
        if missing:
            raise WorkflowError("topic diagnostic must cover every unresolved decision: " + ", ".join(missing))
        for item in items:
            if item["response_status"] in {"dont_know", "skipped"} and item["signal"] != "unknown":
                raise WorkflowError("don't-know and skipped diagnostic items must use signal: unknown")
            if item["response_status"] == "answered" and item["signal"] == "unknown":
                raise WorkflowError("answered diagnostic items must provide a bounded non-unknown signal")
        for decision in TOPIC_DECISIONS:
            recommendation = payload["recommendations"][decision]
            if recommendation["source"] != "diagnostic_signal":
                continue
            informative = any(
                item["decision"] == decision
                and item["response_status"] == "answered"
                and item["signal"] != "unknown"
                for item in items
            )
            if not informative:
                raise WorkflowError(
                    f"{decision} cannot cite diagnostic_signal when its items were skipped or answered don't know"
                )
        secure_start = any(
            item["decision"] == "starting_point" and item["signal"] == "secure"
            for item in items
        )
        if payload["recommendations"]["test_out_recommended"] and not secure_start:
            raise WorkflowError("test-out can be recommended only from an answered secure starting-point signal")
    else:
        expected = {
            "start_from_basics": ("foundations", "learner_choice"),
            "map_first": ("map_first", "learner_choice"),
            "use_defaults": ("use_default", "default"),
        }[strategy]
        starting = payload["recommendations"]["starting_point"]
        if (starting["value"], starting["source"]) != expected:
            raise WorkflowError(f"{strategy} requires starting_point {expected[0]} from {expected[1]}")
        if any(
            payload["recommendations"][decision]["source"] == "diagnostic_signal"
            for decision in TOPIC_DECISIONS
        ):
            raise WorkflowError(f"{strategy} cannot cite diagnostic_signal because it contains no items")
        if payload["recommendations"]["test_out_recommended"]:
            raise WorkflowError(f"{strategy} cannot recommend test-out without diagnostic signals")
    return payload


def stable_id(value: str, fallback: str = "course") -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip("-.")
    if slug and re.fullmatch(r"[a-z0-9][a-z0-9.-]*", slug):
        return slug[:80].rstrip("-.")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{fallback}-{digest}"


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WizardError("topic/topic_terms must be a string or string list")
    return unique(item.strip() for item in value if item.strip())


def source_type(source: dict[str, Any]) -> str:
    explicit = source.get("type")
    if explicit:
        return explicit
    location = str(source.get("path") or source.get("location") or "").lower()
    suffix = Path(location).suffix
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".md", ".txt", ".rst", ".docx"}:
        return "notes"
    if source.get("url"):
        return "website"
    return "other"


def normalize_outline(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WizardError("outline must be a list")
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, str):
            item = {"title": raw}
        elif isinstance(raw, dict):
            item = raw
        else:
            raise WizardError(f"outline[{index - 1}] must be a string or mapping")
        title = require_string(item.get("title"), f"outline[{index - 1}].title")
        item_id = item.get("id") or f"outline.{index:03d}-{stable_id(title, 'item')}"
        item_id = require_id(item_id, f"outline[{index - 1}].id")
        if item_id in used:
            raise WizardError(f"duplicate outline ID: {item_id}")
        used.add(item_id)
        parent = item.get("parent_id")
        if parent is not None:
            parent = require_id(parent, f"{item_id}.parent_id")
        result.append({"id": item_id, "title": title, "parent_id": parent, "notes": str(item.get("notes", ""))})
    return result


def normalize_sources(value: Any, base_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise WizardError("sources must be a list")
    intake_sources: list[dict[str, Any]] = []
    rag_sources: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise WizardError(f"sources[{index - 1}] must be a mapping")
        title = require_string(raw.get("title"), f"sources[{index - 1}].title")
        source_id = require_id(raw.get("id") or f"source.{index:03d}-{stable_id(title, 'item')}", "source.id")
        if source_id in used:
            raise WizardError(f"duplicate source ID: {source_id}")
        used.add(source_id)
        kind = source_type(raw)
        if kind not in SOURCE_TYPES:
            raise WizardError(f"{source_id}.type is invalid: {kind}")
        source = dict(raw)
        source["id"] = source_id
        source["title"] = title
        raw_path = source.get("path")
        if raw_path:
            path = Path(str(raw_path)).expanduser()
            if not path.is_absolute():
                path = base_dir / path
            source["path"] = str(path.resolve())
        location = str(source.get("location") or source.get("path") or source.get("url") or f"inline:{source_id}")
        source["location"] = location
        intake_sources.append(
            {"id": source_id, "title": title, "type": kind, "location": location, "version": str(source.get("version", ""))}
        )
        allowed = {
            key: source[key]
            for key in ["id", "title", "authority", "version", "path", "text", "passages", "location", "ocr", "ocr_language"]
            if key in source
        }
        if not any(key in allowed for key in ["path", "text", "passages"]):
            raise WizardError(f"{source_id} must provide path, text, or passages so the wizard can index it")
        rag_sources.append(allowed)
    return intake_sources, rag_sources


def discovery_sources(web_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    sources = web_evidence.get("sources", []) if isinstance(web_evidence, dict) else []
    result = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "type": "website",
                "location": item.get("url"),
                "version": item.get("version", ""),
            }
        )
    return result


class WizardEngine:
    def __init__(self, workspace: Workspace, state: dict[str, Any]):
        self.workspace = workspace
        self.state = state
        self.path = workspace.meta / "start.yaml"
        self.events_path = workspace.meta / "start-events.ndjson"
        self.diagnostic_path = workspace.meta / "topic-diagnostic.yaml"

    @classmethod
    def initialize(cls, workspace_path: str, payload: dict[str, Any], base_dir: Path) -> "WizardEngine":
        topic_terms = unique(as_string_list(payload.get("topic")) + as_string_list(payload.get("topic_terms")))
        outline = normalize_outline(payload.get("outline"))
        intake_sources, rag_sources = normalize_sources(payload.get("sources"), base_dir)
        available = {"sources": bool(intake_sources), "outline": bool(outline), "topic": bool(topic_terms)}
        mode = payload.get("mode")
        if mode is None:
            mode = next((candidate for candidate in ["sources", "outline", "topic"] if available[candidate]), None)
        if mode is None or not available.get(mode):
            raise WizardError("initial start requires content for the selected mode: sources, outline, or topic")
        title_seed = payload.get("title") or (topic_terms[0] if topic_terms else outline[0]["title"] if outline else intake_sources[0]["title"])
        title = require_string(title_seed, "title")
        goal = require_string(payload.get("goal") or f"Learn {title}", "goal")
        course_id = require_id(payload.get("course_id") or stable_id(title), "course_id")
        workspace = Workspace.create(Path(workspace_path), course_id, title, goal)
        outline_source_id = require_id(payload.get("outline_source_id", "user-outline"), "outline_source_id")
        intake_payload = {
            "mode": mode,
            "request_summary": payload.get("request_summary") or goal,
            "goal": goal,
            "desired_outcome": payload.get("desired_outcome", "working_knowledge"),
            "target_depth": payload.get("target_depth", "working"),
            "prior_knowledge": payload.get("prior_knowledge", []),
            "constraints": payload.get("constraints", []),
            "source_materials": intake_sources,
            "outline_source_id": outline_source_id,
            "outline_items": outline,
            "topic_terms": topic_terms,
            "discovery_sources": [],
            "ambiguities": payload.get("ambiguities", []),
            "mandatory_anchors": payload.get("mandatory_anchors", []),
            "corpus_policy": payload.get("corpus_policy"),
            "assumptions": unique(
                [
                    *payload.get("assumptions", []),
                    *(
                        ["Purpose defaults to working knowledge until the learner changes it."]
                        if mode == "topic" and "desired_outcome" not in payload
                        else []
                    ),
                    *(
                        ["Depth defaults to a practical working level until the learner changes it."]
                        if mode == "topic" and "target_depth" not in payload
                        else []
                    ),
                    *(
                        ["No prior knowledge is assumed beyond prerequisites discovered during mapping."]
                        if mode == "topic" and "prior_knowledge" not in payload
                        else []
                    ),
                ]
            ),
        }
        IntakeEngine.initialize(str(workspace.root), intake_payload)
        rag = RagEngine.initialize(str(workspace.root), payload.get("chunk_chars", 2800), payload.get("overlap_chars", 300))
        if outline:
            outline_text = "\n\n".join(
                f"## {item['title']}\n\n{item['notes'] or 'Coverage heading supplied by the learner.'}"
                for item in outline
            )
            rag_sources.insert(
                0,
                {
                    "id": outline_source_id,
                    "title": payload.get("outline_title", "User-provided outline"),
                    "authority": "user",
                    "text": outline_text,
                },
            )
        if rag_sources:
            rag.ingest({"sources": rag_sources}, "local")
        timestamp = iso()
        unresolved_decisions = [
            decision
            for decision, supplied in [
                ("starting_point", "prior_knowledge" in payload),
                ("target_depth", "target_depth" in payload),
                ("use_case", "desired_outcome" in payload),
            ]
            if mode == "topic" and not supplied
        ]
        entry_strategy = payload.get(
            "entry_strategy", "adaptive_diagnostic" if unresolved_decisions else "use_defaults"
        )
        topic_diagnostic = {
            "status": (
                "not_applicable"
                if mode != "topic" or not unresolved_decisions
                else "pending"
                if entry_strategy == "adaptive_diagnostic"
                else "choice_applied"
            ),
            "strategy": entry_strategy,
            "unresolved_decisions": unresolved_decisions,
            "mastery_evidence_written": False,
        }
        if topic_diagnostic["status"] == "choice_applied":
            topic_diagnostic["recommendations"] = {
                "starting_point": {
                    "value": {
                        "start_from_basics": "foundations",
                        "map_first": "map_first",
                        "use_defaults": "use_default",
                    }[entry_strategy],
                    "source": "default" if entry_strategy == "use_defaults" else "learner_choice",
                },
                "target_depth": {"value": intake_payload["target_depth"], "source": "default"},
                "use_case": {"value": intake_payload["desired_outcome"], "source": "default"},
                "test_out_recommended": False,
            }
        state = {
            "schema_version": START_SCHEMA_VERSION,
            "revision": 0,
            "mode": mode,
            "course_id": course_id,
            "source_ids": [item["id"] for item in intake_sources] + ([outline_source_id] if outline else []),
            "topic_diagnostic": topic_diagnostic,
            "stage": "initialized",
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_result": {},
        }
        engine = cls(load_workspace(str(workspace.root)), state)
        write_yaml(engine.path, state)
        if topic_diagnostic["status"] == "choice_applied":
            write_yaml(engine.diagnostic_path, topic_diagnostic)
        atomic_text(engine.events_path, "")
        return engine

    @classmethod
    def load(cls, workspace_path: str) -> "WizardEngine":
        workspace = load_workspace(workspace_path)
        path = workspace.meta / "start.yaml"
        if not path.is_file():
            raise WizardError("workspace was not created by `start`; use the subsystem commands directly")
        return cls(workspace, read_data(path))

    def commit(self, event_type: str, result: dict[str, Any]) -> None:
        self.state["revision"] = int(self.state.get("revision", 0)) + 1
        self.state["updated_at"] = iso()
        self.state["last_result"] = {
            key: value for key, value in result.items()
            if key not in {"course_plan"}
        }
        action = self.state["last_result"].get("workflow_action")
        if action and action.get("workflow_revision") != self.state["revision"]:
            raise WizardError("workflow action revision disagrees with the committed wizard revision")
        write_yaml(self.path, self.state)
        event = {
            "event_id": f"sevt-{self.state['revision']:06d}",
            "revision": self.state["revision"],
            "type": event_type,
            "at": self.state["updated_at"],
            "stage": self.state["stage"],
        }
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.render()

    def current_result(self) -> dict[str, Any]:
        result = self.state.get("last_result")
        if not isinstance(result, dict) or not result.get("workflow_action"):
            raise WizardError("start workflow has no resumable action; rerun with structured input")
        return result

    def _action(
        self,
        stage: str,
        action: str,
        parameters: dict[str, Any],
        required_result_fields: list[str],
    ) -> dict[str, Any]:
        return make_action(
            workflow_revision=self.state.get("revision", 0) + 1,
            stage=stage,
            action=action,
            parameters=parameters,
            required_result_fields=required_result_fields,
        )

    def apply_submission(self, submission: dict[str, Any]) -> dict[str, Any]:
        current = self.current_result()["workflow_action"]
        result = validate_submission(submission, current)
        action = current["action"]
        allowed = set(current["tool_contract"]["required_result_fields"])
        unexpected = sorted(set(result) - allowed)
        if unexpected:
            raise WorkflowError("workflow submission result contains unsupported fields: " + ", ".join(unexpected))
        if action == "web_search":
            return {"web_evidence": result["web_evidence"], "verdicts": result["verdicts"]}
        if action == "judge_coverage":
            return {"verdicts": result["verdicts"]}
        if action == "generate_course_plan":
            return {"course_plan": result["course_plan"]}
        if action == "diagnose_topic":
            diagnostic = validate_topic_diagnostic(
                result["topic_diagnostic"], self.state.get("topic_diagnostic", {}).get("unresolved_decisions", [])
            )
            return {"_topic_diagnostic": diagnostic, "_rerun_correction": True}
        if action == "confirm_phase":
            if result.get("confirmed") is not True:
                raise WorkflowError("phase confirmation requires result.confirmed: true")
            return {"confirmed": True}
        if action == "activate_first_atom":
            if result.get("confirmed") is not True:
                raise WorkflowError("first-Atom activation requires result.confirmed: true")
            return {"activate_first": True}
        if action == "clarify_goal":
            intake = IntakeEngine.load(str(self.workspace.root))
            guidance = intake.update({**result, "ambiguities": []})
            intake.commit("intake.goal_clarified", {"fields": sorted(result)})
            return {
                "clarification_applied": True,
                "guidance": guidance,
                "_rerun_correction": True,
            }
        raise WorkflowError(f"Action {action} does not accept a submission")

    def _apply_topic_diagnostic(self, diagnostic: dict[str, Any]) -> None:
        recommendations = diagnostic["recommendations"]
        intake = IntakeEngine.load(str(self.workspace.root))
        update: dict[str, Any] = {}
        if recommendations["target_depth"]["source"] != "default":
            update["target_depth"] = recommendations["target_depth"]["value"]
        if recommendations["use_case"]["source"] != "default":
            update["desired_outcome"] = recommendations["use_case"]["value"]
        if update:
            intake.update(update)
            intake.commit("intake.topic_diagnostic_applied", {"fields": sorted(update)})
        sanitized = {
            "status": "completed",
            "strategy": diagnostic["strategy"],
            "unresolved_decisions": self.state.get("topic_diagnostic", {}).get("unresolved_decisions", []),
            "items": [
                {
                    "id": item["id"],
                    "decision": item["decision"],
                    "prompt_sha256": "sha256:" + hashlib.sha256(item["prompt"].encode("utf-8")).hexdigest(),
                    "response_status": item["response_status"],
                    "signal": item["signal"],
                    "scorer": item["scorer"],
                }
                for item in diagnostic["items"]
            ],
            "recommendations": recommendations,
            "privacy": diagnostic["privacy"],
            "mastery_evidence_written": False,
            "completed_at": iso(),
        }
        self.state["topic_diagnostic"] = sanitized
        write_yaml(self.diagnostic_path, sanitized)

    def _add_discovery_sources(self, payload: dict[str, Any]) -> None:
        if not payload.get("web_evidence"):
            return
        additions = discovery_sources(payload["web_evidence"])
        if not additions:
            return
        intake = IntakeEngine.load(str(self.workspace.root))
        by_id = {item["id"]: item for item in intake.state.get("discovery_sources", [])}
        for item in additions:
            if item.get("id"):
                by_id[item["id"]] = item
        intake.update({"discovery_sources": list(by_id.values())})
        intake.commit("intake.discovery_sources_added", {"source_ids": [item["id"] for item in additions]})

    def _coverage(self, payload: dict[str, Any], *, initial: bool) -> dict[str, Any] | None:
        rag = RagEngine.load(str(self.workspace.root))
        correction_requested = (
            initial
            or payload.get("_rerun_correction") is True
            or "web_evidence" in payload
            or "verdicts" in payload
        )
        if correction_requested:
            ingested = None
            if payload.get("web_evidence") is not None:
                intake = IntakeEngine.load(str(self.workspace.root))
                if intake.state["corpus_policy"]["expansion"] == "closed_corpus":
                    raise WizardError("closed_corpus forbids Web evidence; change the policy explicitly first")
                ingested = rag.ingest(payload["web_evidence"], "web")
                self._add_discovery_sources(payload)
                rag = RagEngine.load(str(self.workspace.root))
            coverage = rag.requirements("intake")
            coverage["verdicts"] = payload.get("verdicts", [])
            report = rag.coverage(coverage)
            if ingested is not None:
                return rag.correction_response(report, ingested)
            if report["gate"] != "pass" and not payload.get("verdicts") and any(
                item.get("candidate_chunk_ids") for item in report["requirements"]
            ):
                return {
                    "status": "coverage_judgment_required",
                    "rag_revision": rag.revision,
                    "coverage": report,
                    "web_search_tasks": [],
                }
            return rag.correction_response(report)
        coverage_path = self.workspace.meta / "rag" / "latest-coverage.yaml"
        if not coverage_path.is_file():
            return None
        report = read_data(coverage_path)
        return rag.correction_response(report)

    def _plan_task(self) -> dict[str, Any]:
        intake = IntakeEngine.load(str(self.workspace.root))
        rag = RagEngine.load(str(self.workspace.root))
        return {
            "action": "generate_course_plan",
            "goal": intake.state.get("goal"),
            "mode": intake.state.get("mode"),
            "goal_contract": intake.state.get("goal_contract"),
            "corpus_policy": intake.state.get("corpus_policy"),
            "source_ids": [item["id"] for item in rag._source_registry().get("sources", [])],
            "constraints": [
                "Produce a prerequisite DAG rather than copying source order.",
                "Every non-archived Atom must cite a registered source ID and stable locator.",
                "Keep each Atom independently teachable and assessable.",
                "Use the topic diagnostic only to choose the entry boundary; it is not mastery Evidence.",
            ],
            "topic_diagnostic": self.state.get("topic_diagnostic", {}),
            "submit_as": "course_plan in the next start payload",
            "schema_reference": "atom-learn/references/SCHEMA.md",
        }

    def advance(self, payload: dict[str, Any], *, initial: bool = False) -> dict[str, Any]:
        intake = IntakeEngine.load(str(self.workspace.root))
        if (
            payload.get("web_evidence") is not None
            and intake.state["corpus_policy"]["expansion"] == "closed_corpus"
        ):
            raise WizardError("closed_corpus forbids Web evidence; change the policy explicitly first")
        stage = self.state.get("stage")
        if stage == "coverage_judgment_required" and "verdicts" not in payload:
            return self.current_result()
        if stage == "web_search_required" and not any(key in payload for key in ["web_evidence", "verdicts"]):
            return self.current_result()
        if stage == "corpus_gap_reported" and not payload.get("clarification_applied"):
            return self.current_result()
        if intake.state.get("ambiguities") and not payload.get("clarification_applied"):
            self.state["stage"] = "clarify_goal"
            result = {
                "ok": True,
                "status": "clarification_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "assumptions": intake.state.get("assumptions", []),
                "workflow_action": self._action(
                    "clarify_goal",
                    "clarify_goal",
                    {"ambiguities": intake.state["ambiguities"], "current_goal": intake.state.get("goal")},
                    ["goal", "desired_outcome", "target_depth"],
                ),
                "next_action": "Answer the high-impact goal questions shown in workflow_action.",
            }
            self.commit("start.clarification_required", result)
            return result
        diagnostic_state = self.state.get("topic_diagnostic", {})
        if diagnostic_state.get("status") == "pending":
            if payload.get("_topic_diagnostic") is not None:
                self._apply_topic_diagnostic(payload["_topic_diagnostic"])
                intake = IntakeEngine.load(str(self.workspace.root))
            else:
                self.state["stage"] = "topic_diagnostic"
                result = {
                    "ok": True,
                    "status": "topic_diagnostic_required",
                    "workspace": str(self.workspace.root),
                    "wizard_revision": self.state.get("revision", 0) + 1,
                    "assumptions": intake.state.get("assumptions", []),
                    "workflow_action": self._action(
                        "topic_diagnostic",
                        "diagnose_topic",
                        {
                            "topic_terms": intake.state.get("topic_terms", []),
                            "unresolved_decisions": diagnostic_state.get("unresolved_decisions", []),
                            "item_policy": {
                                "minimum": 2,
                                "maximum": 5,
                                "prioritize": ["key_prerequisites", "target_boundary"],
                                "adaptive": True,
                                "dont_know_or_skip": "unknown_not_penalized",
                                "mastery_evidence": "forbidden_before_an_Atom_and_qualified_scorer_exist",
                            },
                            "alternatives": ["start_from_basics", "map_first", "use_defaults"],
                            "submission_schema": "atom-learn/assets/schemas/topic-diagnostic.schema.json",
                        },
                        ["topic_diagnostic"],
                    ),
                    "next_action": (
                        "Offer the three one-click paths or ask only 2-5 adaptive items that resolve the listed decisions."
                    ),
                }
                self.commit("start.topic_diagnostic_required", result)
                return result
        coverage = self._coverage(payload, initial=initial)
        if coverage and coverage.get("status") == "coverage_judgment_required":
            self.state["stage"] = "coverage_judgment_required"
            report = coverage["coverage"]
            result = {
                "ok": True,
                "status": "coverage_judgment_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "intake": IntakeEngine.load(str(self.workspace.root)).status_summary(),
                "rag_revision": coverage.get("rag_revision"),
                "coverage_requirements": report.get("requirements", []),
                "workflow_action": self._action(
                    "coverage_judgment",
                    "judge_coverage",
                    {
                        "requirements": report.get("requirements", []),
                        "instruction": (
                            "Judge each Goal Contract requirement against only its returned candidate chunks. "
                            "Mark supported, weak, or missing and cite candidate chunk IDs for supported verdicts."
                        ),
                    },
                    ["verdicts"],
                ),
                "next_action": "Judge the local candidates before any external search is considered.",
            }
            self.commit("start.coverage_judgment_required", result)
            return result
        if coverage and coverage.get("status") == "corpus_gap_reported":
            self.state["stage"] = "corpus_gap_reported"
            report = coverage["coverage"]
            gaps = [
                {
                    "requirement_id": item["id"],
                    "query": item["query"],
                    "status": item["status"],
                    "candidate_chunk_ids": item.get("candidate_chunk_ids", []),
                }
                for item in report.get("requirements", [])
                if item.get("status") != "supported"
            ]
            result = {
                "ok": True,
                "status": "corpus_gap_reported",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "intake": IntakeEngine.load(str(self.workspace.root)).status_summary(),
                "rag_revision": coverage.get("rag_revision"),
                "corpus_gaps": gaps,
                "web_search_tasks": [],
                "workflow_action": self._action(
                    "clarify_goal",
                    "clarify_goal",
                    {
                        "current_goal": intake.state.get("goal"),
                        "current_corpus_policy": intake.state.get("corpus_policy"),
                        "gaps": gaps,
                        "options": [
                            "Narrow the learning goal to what the closed corpus supports.",
                            "Add learner-approved material, then restart coverage.",
                            "Explicitly change expansion to correct_gaps or discover.",
                        ],
                    },
                    ["goal", "desired_outcome", "target_depth", "corpus_policy"],
                ),
                "next_action": "Resolve the closed-corpus gaps without automatic Web Search.",
            }
            self.commit("start.corpus_gap_reported", result)
            return result
        if coverage and coverage.get("status") != "complete":
            self.state["stage"] = "web_search_required"
            result = {
                "ok": True,
                "status": "web_search_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "intake": IntakeEngine.load(str(self.workspace.root)).status_summary(),
                "rag_revision": coverage.get("rag_revision"),
                "web_search_tasks": coverage.get("web_search_tasks", []),
                "workflow_action": self._action(
                    "evidence_discovery",
                    "web_search",
                    {
                        "tasks": coverage.get("web_search_tasks", []),
                        "instruction": "Use harness-native Web Search and open authoritative results.",
                    },
                    ["web_evidence", "verdicts"],
                ),
                "next_action": "Execute the returned tasks with harness Web Search, then rerun start with web_evidence and verdicts.",
            }
            self.commit("start.correction_required", result)
            return result

        intake = IntakeEngine.load(str(self.workspace.root))
        pending_plan_path = self.workspace.meta / "pending-start-plan.yaml"
        if payload.get("course_plan") is not None:
            if self.workspace.atoms:
                raise WizardError("a course plan is already imported; use import-plan for later updates")
            preview = load_workspace(str(self.workspace.root))
            preview_result = preview.import_plan(payload["course_plan"])
            preview_errors = preview.validate()
            if preview_errors:
                raise WizardError("Proposed course plan is invalid:\n- " + "\n- ".join(preview_errors))
            write_yaml(pending_plan_path, payload["course_plan"])
            candidates = preview.suggest_next()
            self.state["stage"] = "phase_confirmation_required"
            result = {
                "ok": True,
                "status": "phase_confirmation_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "plan_preview": {**preview_result, "first_candidates": candidates},
                "workflow_action": self._action(
                    "phase_confirmation",
                    "confirm_phase",
                    {
                        "plan_summary": preview_result,
                        "first_candidates": candidates,
                        "will_mutate_course": True,
                    },
                    ["confirmed"],
                ),
                "next_action": "Review the first phase and confirm it before the plan is imported.",
            }
            self.commit("start.phase_confirmation_required", result)
            return result

        if self.state.get("stage") == "phase_confirmation_required":
            if payload.get("confirmed") is not True:
                return self.current_result()
            if not pending_plan_path.is_file():
                raise WizardError("pending confirmed course plan is missing")
            workspace = load_workspace(str(self.workspace.root))
            plan_result = workspace.import_plan(read_data(pending_plan_path))
            workspace.commit("plan.imported", "Imported the plan through the start wizard", plan_result)
            intake = IntakeEngine.load(str(self.workspace.root))
            completion = intake.complete()
            intake.commit("intake.completed", completion)
            workspace = load_workspace(str(self.workspace.root))
            candidates = workspace.suggest_next()
            if not candidates:
                raise WizardError("imported plan has no eligible first Atom")
            self.state["stage"] = "first_atom_confirmation_required"
            result = {
                "ok": True,
                "status": "first_atom_confirmation_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "course_revision": workspace.revision,
                "plan": plan_result,
                "intake": completion,
                "first_atom": candidates[0],
                "workflow_action": self._action(
                    "first_atom",
                    "activate_first_atom",
                    {"atom": candidates[0], "requires_confirmation": True},
                    ["confirmed"],
                ),
                "next_action": "Confirm activation of the first eligible Atom.",
            }
            self.commit("start.first_atom_confirmation_required", result)
            return result

        if self.state.get("stage") == "first_atom_confirmation_required":
            if payload.get("activate_first") is not True:
                return self.current_result()
            workspace = load_workspace(str(self.workspace.root))
            candidates = workspace.suggest_next()
            if not candidates:
                raise WizardError("no eligible Atom is available for activation")
            atom_id = candidates[0]["id"]
            workspace.activate(atom_id)
            workspace.commit("atom.activated", "Activated the confirmed first Atom", {"atom_id": atom_id})
            self.state["stage"] = "complete"
            result = {
                "ok": True,
                "status": "complete",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "course_revision": workspace.revision,
                "active_atom_id": atom_id,
                "workflow_action": self._action(
                    "complete", "done", {"active_atom_id": atom_id}, []
                ),
                "next_action": "Teach only the Active Atom and record qualified Evidence before advancing.",
            }
            self.commit("start.completed", result)
            return result

        if payload.get("course_plan") is None:
            self.state["stage"] = "course_plan_required"
            plan_task = self._plan_task()
            result = {
                "ok": True,
                "status": "course_plan_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "intake": intake.status_summary(),
                "rag": RagEngine.load(str(self.workspace.root)).status(),
                "course_plan_task": plan_task,
                "workflow_action": self._action(
                    "course_planning", "generate_course_plan", plan_task, ["course_plan"]
                ),
                "next_action": "Generate the source-grounded plan and rerun start with course_plan in the same payload shape.",
            }
            self.commit("start.plan_required", result)
            return result

        raise WizardError("unhandled start workflow state")

    def render(self) -> None:
        last = self.state.get("last_result", {})
        lines = [
            "# Start Wizard",
            "",
            "> Generated by AtomLearn. Resume with the same `start` command; canonical subsystem state remains under `.atomlearn/`.",
            "",
            f"- Mode: `{self.state.get('mode')}`",
            f"- Stage: `{self.state.get('stage')}`",
            f"- Wizard revision: `{self.state.get('revision')}`",
            "",
            "## Next Action",
            "",
            last.get("next_action") or "Run `start` to continue.",
        ]
        tasks = last.get("web_search_tasks", [])
        if tasks:
            lines.extend(["", "## Web Search Tasks", ""])
            lines.extend(f"- `{item['requirement_id']}` — {item['query']}" for item in tasks)
        if last.get("course_plan_task"):
            lines.extend(["", "## Course Plan", "", "- Generate and submit a source-grounded prerequisite DAG."])
        atomic_text(self.workspace.root / "START.md", "\n".join(lines).rstrip() + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start or resume AtomLearn from one learner request")
    parser.add_argument("workspace", help="Course workspace to create or resume")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="JSON or YAML payload conforming to start.schema.json")
    source.add_argument("--topic", help="Shortest topic-only start; title and goal are inferred")
    source.add_argument("--submission", help="Typed JSON/YAML result for the currently issued workflow action")
    parser.add_argument("--title", help="Optional title used with --topic")
    parser.add_argument("--goal", help="Optional learning goal used with --topic")
    parser.add_argument("--course-id", help="Optional stable course ID used with --topic")
    parser.add_argument(
        "--entry-strategy",
        choices=["adaptive_diagnostic", "start_from_basics", "map_first", "use_defaults"],
        help="Topic entry path; adaptive diagnostic is the shortest-topic default",
    )
    parser.add_argument("--print-schema", action="store_true", help="Print the JSON Schema and exit")
    parser.add_argument("--confirm", action="store_true", help="Confirm the pending first learning phase")
    parser.add_argument("--activate-first", action="store_true", help="Confirm and activate the proposed first Atom")
    parser.add_argument("--json", action="store_true", help="Emit the complete typed action protocol as JSON")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.print_schema:
        print(asset_path("schemas", "start.schema.json").read_text(encoding="utf-8"))
        return
    input_path = Path(args.input).resolve() if args.input else None
    if input_path:
        payload = validate_payload(read_data(input_path))
        base_dir = input_path.parent
    elif args.submission:
        payload = {}
        base_dir = Path(args.submission).resolve().parent
    elif args.topic:
        payload = validate_payload(
            {key: value for key, value in {"topic": args.topic, "title": args.title, "goal": args.goal, "course_id": args.course_id, "entry_strategy": args.entry_strategy}.items() if value is not None}
        )
        base_dir = Path.cwd()
    else:
        payload = {key: value for key, value in {
            "confirmed": True if args.confirm else None,
            "activate_first": True if args.activate_first else None,
        }.items() if value is not None}
        base_dir = Path.cwd()
    root = Path(args.workspace).resolve()
    initial = not (root / ".atomlearn").is_dir()
    if initial:
        if not payload:
            raise WizardError("initial start requires --topic or --input")
        engine = WizardEngine.initialize(str(root), payload, base_dir)
    else:
        engine = WizardEngine.load(str(root))
        if payload:
            validate_payload(payload)
    if args.submission:
        submission = read_data(Path(args.submission))
        payload = engine.apply_submission(submission)
        result = engine.advance(payload, initial=False)
    elif not initial and not payload:
        result = engine.current_result()
    else:
        result = engine.advance(payload, initial=initial)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        action = result.get("workflow_action", {})
        display = action.get("display", {})
        print(display.get("en") or result.get("next_action") or result.get("status"))
        print(display.get("zh_CN") or "")
        print(f"Workspace: {result.get('workspace', root)}")
        print(f"Status: {result.get('status')}")
        print(f"Next: {result.get('next_action')}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (WizardError, WorkflowError, IntakeError, RagError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
