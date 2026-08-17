#!/usr/bin/env python3
"""Typed action/submission protocol shared by the start wizard and harness bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from atomlearn import iso
from core_paths import CORE_ROOT


ASSETS = CORE_ROOT / "assets" / "schemas"
ACTION_SCHEMA = ASSETS / "workflow-action.schema.json"
SUBMISSION_SCHEMA = ASSETS / "workflow-submission.schema.json"
SUBMISSION_SCHEMA_ID = "https://atomlearn.dev/schemas/workflow-submission.schema.json"
ACTION_DETAILS = {
    "clarify_goal": ("harness.clarify", "Clarify the learning goal", "澄清学习目标"),
    "inventory_sources": ("harness.source_inventory", "Inventory supplied sources", "清点已提供资料"),
    "web_search": ("harness.web_search", "Find authoritative evidence", "查找权威证据"),
    "judge_coverage": ("harness.coverage_judgment", "Judge evidence coverage", "判断证据覆盖"),
    "generate_course_plan": ("harness.course_plan", "Generate a source-grounded Atom plan", "生成资料驱动的 Atom 计划"),
    "validate_plan": ("core.plan_validation", "Validate the proposed Atom plan", "校验候选 Atom 计划"),
    "confirm_phase": ("user.phase_confirmation", "Confirm the first learning phase", "确认第一学习阶段"),
    "activate_first_atom": ("core.activate_atom", "Activate the first eligible Atom", "激活第一个可学 Atom"),
    "done": ("none", "Start workflow complete", "启动流程已完成"),
}


class WorkflowError(RuntimeError):
    """A typed harness action or submission is invalid or stale."""


def _validate(value: dict[str, Any], path: Path, label: str) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        details = [
            (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
            for error in errors
        ]
        raise WorkflowError(f"{label} is invalid:\n- " + "\n- ".join(details))


def make_action(
    *,
    workflow_revision: int,
    stage: str,
    action: str,
    parameters: dict[str, Any],
    required_result_fields: list[str],
) -> dict[str, Any]:
    capability, english, chinese = ACTION_DETAILS[action]
    canonical = json.dumps(
        {
            "workflow_revision": workflow_revision,
            "stage": stage,
            "action": action,
            "parameters": parameters,
            "required_result_fields": required_result_fields,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result = {
        "kind": "atomlearn.workflow-action",
        "schema_version": 1,
        "action_id": "action-" + digest[:24],
        "workflow": "start",
        "workflow_revision": workflow_revision,
        "stage": stage,
        "action": action,
        "display": {"en": english, "zh_CN": chinese},
        "tool_contract": {
            "capability": capability,
            "parameters": parameters,
            "required_result_fields": required_result_fields,
        },
        "submission_schema": SUBMISSION_SCHEMA_ID,
        "idempotency_key": "sha256:" + hashlib.sha256(("submit|" + canonical).encode("utf-8")).hexdigest(),
        "created_at": iso(),
    }
    _validate(result, ACTION_SCHEMA, "workflow action")
    return result


def validate_submission(submission: Any, current_action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(submission, dict):
        raise WorkflowError("workflow submission must be a mapping")
    _validate(submission, SUBMISSION_SCHEMA, "workflow submission")
    for field in ["action_id", "workflow_revision", "idempotency_key"]:
        expected = current_action[field]
        if submission[field] != expected:
            raise WorkflowError(
                f"Stale workflow submission: {field} is {submission[field]!r}, expected {expected!r}"
            )
    required = current_action["tool_contract"]["required_result_fields"]
    missing = [field for field in required if field not in submission["result"]]
    if missing:
        raise WorkflowError("workflow submission result is missing: " + ", ".join(missing))
    return submission["result"]
