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


START_SCHEMA_VERSION = 1
SOURCE_TYPES = {"pdf", "book", "notes", "documentation", "website", "database", "outline", "exam", "other"}


class WizardError(RuntimeError):
    """A user-correctable start/wizard error."""


def asset_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath("assets", *parts)


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
            "discovery_sources": intake_sources if mode == "topic" else [],
            "ambiguities": payload.get("ambiguities", []),
            "assumptions": payload.get("assumptions", []),
        }
        IntakeEngine.initialize(str(workspace.root), intake_payload)
        rag = RagEngine.initialize(str(workspace.root), payload.get("chunk_chars", 2800), payload.get("overlap_chars", 300))
        if mode == "outline":
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
        state = {
            "schema_version": START_SCHEMA_VERSION,
            "revision": 0,
            "mode": mode,
            "course_id": course_id,
            "source_ids": [item["id"] for item in intake_sources] + ([outline_source_id] if mode == "outline" else []),
            "stage": "initialized",
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_result": {},
        }
        engine = cls(load_workspace(str(workspace.root)), state)
        write_yaml(engine.path, state)
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
            "status": result.get("status"),
            "next_action": result.get("next_action"),
            "web_search_tasks": result.get("web_search_tasks", []),
            "course_plan_task": result.get("course_plan_task"),
        }
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

    def _add_topic_discovery(self, payload: dict[str, Any]) -> None:
        if self.state.get("mode") != "topic" or not payload.get("web_evidence"):
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
        if self.state.get("mode") == "sources":
            return None
        rag = RagEngine.load(str(self.workspace.root))
        correction_requested = initial or "web_evidence" in payload or "verdicts" in payload
        if correction_requested:
            self._add_topic_discovery(payload)
            coverage = rag.requirements("intake")
            coverage["verdicts"] = payload.get("verdicts", [])
            correction = {"coverage": coverage}
            if payload.get("web_evidence") is not None:
                correction["web_evidence"] = payload["web_evidence"]
            return rag.correct(correction)
        coverage_path = self.workspace.meta / "rag" / "latest-coverage.yaml"
        if not coverage_path.is_file():
            return None
        report = read_data(coverage_path)
        return {
            "status": "complete" if report.get("gate") == "pass" else "web_search_required",
            "rag_revision": rag.revision,
            "coverage": report,
            "web_search_tasks": self.state.get("last_result", {}).get("web_search_tasks", []),
        }

    def _plan_task(self) -> dict[str, Any]:
        intake = IntakeEngine.load(str(self.workspace.root))
        rag = RagEngine.load(str(self.workspace.root))
        return {
            "action": "generate_course_plan",
            "goal": intake.state.get("goal"),
            "mode": intake.state.get("mode"),
            "source_ids": [item["id"] for item in rag._source_registry().get("sources", [])],
            "constraints": [
                "Produce a prerequisite DAG rather than copying source order.",
                "Every non-archived Atom must cite a registered source ID and stable locator.",
                "Keep each Atom independently teachable and assessable.",
            ],
            "submit_as": "course_plan in the next start payload",
            "schema_reference": "atom-learn/references/SCHEMA.md",
        }

    def advance(self, payload: dict[str, Any], *, initial: bool = False) -> dict[str, Any]:
        coverage = self._coverage(payload, initial=initial)
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
                "next_action": "Execute the returned tasks with harness Web Search, then rerun start with web_evidence and verdicts.",
            }
            self.commit("start.correction_required", result)
            return result

        intake = IntakeEngine.load(str(self.workspace.root))
        if payload.get("course_plan") is None:
            self.state["stage"] = "course_plan_required"
            result = {
                "ok": True,
                "status": "course_plan_required",
                "workspace": str(self.workspace.root),
                "wizard_revision": self.state.get("revision", 0) + 1,
                "intake": intake.status_summary(),
                "rag": RagEngine.load(str(self.workspace.root)).status(),
                "course_plan_task": self._plan_task(),
                "next_action": "Generate the source-grounded plan and rerun start with course_plan in the same payload shape.",
            }
            self.commit("start.plan_required", result)
            return result

        workspace = load_workspace(str(self.workspace.root))
        if workspace.atoms:
            raise WizardError("a course plan is already imported; use import-plan for later updates")
        plan_result = workspace.import_plan(payload["course_plan"])
        workspace.commit("plan.imported", "Imported the plan through the start wizard", plan_result)
        intake = IntakeEngine.load(str(self.workspace.root))
        completion = intake.complete()
        intake.commit("intake.completed", completion)
        self.state["stage"] = "complete"
        result = {
            "ok": True,
            "status": "complete",
            "workspace": str(self.workspace.root),
            "wizard_revision": self.state.get("revision", 0) + 1,
            "course_revision": workspace.revision,
            "plan": plan_result,
            "intake": completion,
            "next_action": "Run suggest-next, then activate the first available Atom.",
        }
        self.commit("start.completed", result)
        return result

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
    parser.add_argument("--title", help="Optional title used with --topic")
    parser.add_argument("--goal", help="Optional learning goal used with --topic")
    parser.add_argument("--course-id", help="Optional stable course ID used with --topic")
    parser.add_argument("--print-schema", action="store_true", help="Print the JSON Schema and exit")
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
    elif args.topic:
        payload = validate_payload(
            {key: value for key, value in {"topic": args.topic, "title": args.title, "goal": args.goal, "course_id": args.course_id}.items() if value is not None}
        )
        base_dir = Path.cwd()
    else:
        payload = {}
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
    print(json.dumps(engine.advance(payload, initial=initial), ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(argv)
        return 0
    except (WizardError, IntakeError, RagError, AtomLearnError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
