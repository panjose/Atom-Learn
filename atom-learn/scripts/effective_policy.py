#!/usr/bin/env python3
"""Pure, explainable Effective Policy merger for AtomLearn personalization."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from adaptation import ADAPTATION_CONTEXTS, DIMENSION_CONTEXTS, GUIDANCE, PREFERENCE_VALUES, AdaptationEngine
from atomlearn import load_workspace
from platform_state import CORE_ROOT, PlatformStateError, core_version, resolve_user_data_root
from user_profile import UserProfileEngine, UserProfileError, WorkspaceBinding, load_yaml


POLICY_SCHEMA = CORE_ROOT / "assets" / "schemas" / "effective-policy.schema.json"
STRATEGY_ONLY_VALUES = {
    "check.style": {"short_transfer", "worked_then_transfer", "error_diagnosis"},
    "review.presentation": {"retrieval_first", "example_then_recall", "mixed"},
}
POLICY_VALUES = {**PREFERENCE_VALUES, **STRATEGY_ONLY_VALUES}
POLICY_DIMENSION_CONTEXTS = {
    **DIMENSION_CONTEXTS,
    "check.style": {"teaching", "review", "exam"},
    "review.presentation": {"review"},
}
POLICY_GUIDANCE = {
    **GUIDANCE,
    ("check.style", "short_transfer"): "Use one short transfer check without supplying the worked solution first.",
    ("check.style", "worked_then_transfer"): "Show one worked example, then ask a closely matched transfer check.",
    ("check.style", "error_diagnosis"): "Use a plausible incorrect solution and ask the learner to diagnose it.",
    ("review.presentation", "retrieval_first"): "Begin review with unaided retrieval before showing examples or notes.",
    ("review.presentation", "example_then_recall"): "Refresh with one compact example before an unaided recall check.",
    ("review.presentation", "mixed"): "Choose retrieval-first or example-first review from the Atom's observed difficulty.",
}
CORE_DEFAULTS = {
    "response.detail": "balanced",
    "answer.structure": "mixed",
    "language.mode": "match_user",
    "explanation.order": "mixed",
    "example.mode": "mixed",
    "interaction.pacing": "one_atom",
    "teaching.mode": "mixed",
    "feedback.style": "neutral",
    "notation.level": "mixed",
    "challenge.level": "standard",
    "research.orientation": "breadth_first",
    "source.priority": "mixed",
    "check.style": "short_transfer",
    "review.presentation": "retrieval_first",
}
SOURCE_RANK = {
    "core_default": 0,
    "user_strategy": 1,
    "course_strategy": 2,
    "user_global_inferred": 3,
    "workspace_inferred": 4,
    "user_global_explicit": 5,
    "workspace_explicit": 6,
    "current_turn": 7,
}
WINNER_REASONS = {
    "current_turn": "overridden_by_current_turn",
    "workspace_explicit": "overridden_by_workspace_explicit",
    "user_global_explicit": "overridden_by_user_explicit",
    "workspace_inferred": "overridden_by_workspace_inferred",
    "user_global_inferred": "overridden_by_user_inferred",
    "course_strategy": "overridden_by_course_strategy",
    "user_strategy": "overridden_by_user_strategy",
}
INVARIANTS = {
    "one_active_atom": "enforced",
    "mastery_requires_evidence": "enforced",
    "prerequisites_before_activation": "enforced",
    "source_grounding": "enforced",
    "privacy": "enforced",
}


class EffectivePolicyError(RuntimeError):
    """Effective Policy input is incompatible or violates a protected boundary."""


def _valid_value(dimension: str, value: Any) -> bool:
    return dimension in POLICY_VALUES and isinstance(value, str) and value in POLICY_VALUES[dimension]


def validate_overrides(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise EffectivePolicyError("current-turn overrides must be a mapping of dimension to enum value")
    result: dict[str, str] = {}
    for dimension, value in raw.items():
        if not _valid_value(str(dimension), value):
            if dimension not in POLICY_VALUES:
                raise EffectivePolicyError(
                    f"Current-turn override {dimension!r} is not a presentation dimension; protected invariants cannot be overridden"
                )
            raise EffectivePolicyError(
                f"Current-turn override {dimension!r} must be one of: {', '.join(sorted(POLICY_VALUES[dimension]))}"
            )
        result[str(dimension)] = value
    return result


def _preference_candidates(
    profile: dict[str, Any] | None,
    *,
    explicit_source: str,
    inferred_source: str,
    revision: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    if not profile:
        return candidates, pending
    for dimension, item in sorted(profile.get("preferences", {}).items()):
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        value = item.get("active_value")
        if status == "active" and _valid_value(dimension, value):
            source = explicit_source if item.get("source") == "explicit" else inferred_source
            candidates.append(
                {"dimension": dimension, "value": value, "source": source, "source_revision": revision}
            )
        elif status in {"provisional", "contested", "needs_review", "forbidden"}:
            pending.append(
                {
                    "dimension": dimension,
                    "status": status,
                    "source": explicit_source.split("_explicit")[0] if "_explicit" in explicit_source else explicit_source,
                    "confidence": item.get("confidence"),
                }
            )
    return candidates, pending


def _course_strategy(workspace: Any) -> tuple[list[dict[str, Any]], int]:
    path = workspace.meta / "evolution" / "policy.yaml"
    if not path.is_file():
        return [], 0
    policy = load_yaml(path)
    revision = int(policy.get("revision", 0))
    learner = policy.get("learner_strategy", {})
    if not isinstance(learner, dict):
        return [], revision
    merged: dict[str, Any] = {}
    default = learner.get("default", {})
    if isinstance(default, dict):
        merged.update(default)
    active_atom = workspace.current.get("active_atom_id")
    atoms = learner.get("atoms", {})
    if active_atom and isinstance(atoms, dict) and isinstance(atoms.get(active_atom), dict):
        merged.update(atoms[active_atom])
    candidates = [
        {"dimension": dimension, "value": value, "source": "course_strategy", "source_revision": revision}
        for dimension, value in sorted(merged.items())
        if _valid_value(dimension, value)
    ]
    return candidates, revision


def _user_strategy(data_root: Path, profile_id: str | None) -> tuple[list[dict[str, Any]], int]:
    if not profile_id:
        return [], 0
    path = data_root / "strategies" / profile_id / "state.yaml"
    if not path.is_file():
        return [], 0
    state = load_yaml(path)
    revision = int(state.get("revision", 0))
    if state.get("experiments_enabled") is not True:
        return [], revision
    active = state.get("active", {})
    if not isinstance(active, dict):
        return [], revision
    candidates = []
    for dimension, item in sorted(active.items()):
        if isinstance(item, dict) and _valid_value(dimension, item.get("value")):
            candidates.append(
                {
                    "dimension": dimension,
                    "value": item["value"],
                    "source": "user_strategy",
                    "source_revision": revision,
                }
            )
    return candidates, revision


def merge_effective_policy(
    *,
    context: str,
    current_turn: dict[str, str] | None = None,
    workspace_profile: dict[str, Any] | None = None,
    workspace_revision: int = 0,
    user_profile: dict[str, Any] | None = None,
    user_revision: int = 0,
    course_strategy: list[dict[str, Any]] | None = None,
    user_strategy: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if context not in ADAPTATION_CONTEXTS:
        raise EffectivePolicyError(f"context must be one of: {', '.join(sorted(ADAPTATION_CONTEXTS))}")
    overrides = validate_overrides(current_turn)
    candidates: list[dict[str, Any]] = [
        {"dimension": dimension, "value": value, "source": "core_default", "source_revision": 0}
        for dimension, value in CORE_DEFAULTS.items()
    ]
    user_candidates, _ = _preference_candidates(
        user_profile,
        explicit_source="user_global_explicit",
        inferred_source="user_global_inferred",
        revision=user_revision,
    )
    workspace_candidates, _ = _preference_candidates(
        workspace_profile,
        explicit_source="workspace_explicit",
        inferred_source="workspace_inferred",
        revision=workspace_revision,
    )
    candidates.extend(user_strategy or [])
    candidates.extend(course_strategy or [])
    candidates.extend(user_candidates)
    candidates.extend(workspace_candidates)
    candidates.extend(
        {"dimension": dimension, "value": value, "source": "current_turn", "source_revision": 0}
        for dimension, value in sorted(overrides.items())
    )
    eligible: dict[str, list[dict[str, Any]]] = {}
    ignored: list[dict[str, Any]] = []
    for candidate in candidates:
        dimension = candidate["dimension"]
        if context not in POLICY_DIMENSION_CONTEXTS.get(dimension, set()):
            if candidate["source"] != "core_default":
                ignored.append({**candidate, "reason": "context_not_allowed"})
            continue
        eligible.setdefault(dimension, []).append(candidate)
    effective: dict[str, Any] = {}
    for dimension, choices in sorted(eligible.items()):
        choices.sort(key=lambda item: (SOURCE_RANK[item["source"]], item["source_revision"], item["value"]))
        winner = choices[-1]
        effective[dimension] = {
            "value": winner["value"],
            "source": winner["source"],
            "source_revision": winner["source_revision"],
        }
        reason = WINNER_REASONS.get(winner["source"], "overridden_by_course_strategy")
        for candidate in choices[:-1]:
            if candidate["source"] == "core_default":
                continue
            ignored.append({**candidate, "reason": reason})
    canonical = json.dumps(
        {"context": context, "core_version": core_version(), "effective": effective, "ignored": ignored},
        sort_keys=True,
        separators=(",", ":"),
    )
    result = {
        "context": context,
        "core_version": core_version(),
        "policy_fingerprint": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "effective": effective,
        "ignored": [
            {key: item[key] for key in ["dimension", "value", "source", "reason"]}
            for item in ignored
        ],
        "invariants": dict(INVARIANTS),
        "instructions": unique_instructions(effective),
    }
    schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:  # pragma: no cover - construction guard
        raise EffectivePolicyError("Effective Policy failed its schema: " + "; ".join(error.message for error in errors))
    return result


def unique_instructions(effective: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for dimension, decision in sorted(effective.items()):
        instruction = POLICY_GUIDANCE.get((dimension, decision["value"]))
        if instruction and instruction not in result:
            result.append(instruction)
    return result


def effective_for_workspace(
    workspace_path: str | Path,
    context: str,
    *,
    current_turn: dict[str, str] | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    workspace = load_workspace(str(workspace_path))
    workspace_profile = None
    workspace_revision = 0
    pending: list[dict[str, Any]] = []
    if (workspace.meta / "adaptation").is_dir():
        local = AdaptationEngine.load(str(workspace.root))
        errors = local.validate()
        if errors:
            raise EffectivePolicyError("Workspace adaptation is invalid:\n- " + "\n- ".join(errors))
        workspace_profile = local.profile
        workspace_revision = local.revision
        _, pending = _preference_candidates(
            workspace_profile,
            explicit_source="workspace_explicit",
            inferred_source="workspace_inferred",
            revision=workspace_revision,
        )
    root = resolve_user_data_root(data_dir, create=False)
    binding = WorkspaceBinding(workspace.root).read()
    user_state = None
    user_revision = 0
    profile_id = None
    if binding and binding["enabled"]:
        profile_id = binding["profile_id"]
        profile = UserProfileEngine(root, profile_id)
        if not profile.exists():
            raise EffectivePolicyError(f"Workspace is bound to missing user profile: {profile_id}")
        profile.require_valid()
        state = profile.state()
        if state["global_enabled"]:
            user_state = state
            user_revision = state["revision"]
            _, user_pending = _preference_candidates(
                user_state,
                explicit_source="user_global_explicit",
                inferred_source="user_global_inferred",
                revision=user_revision,
            )
            pending.extend(user_pending)
    course_candidates, _ = _course_strategy(workspace)
    user_candidates, _ = _user_strategy(root, profile_id if user_state else None)
    policy = merge_effective_policy(
        context=context,
        current_turn=current_turn,
        workspace_profile=workspace_profile,
        workspace_revision=workspace_revision,
        user_profile=user_state,
        user_revision=user_revision,
        course_strategy=course_candidates,
        user_strategy=user_candidates,
    )
    policy["pending"] = pending
    policy["workspace_adaptation_revision"] = workspace_revision
    policy["user_profile_revision"] = user_revision if user_state else None
    policy["profile_id"] = profile_id if user_state else None
    schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(policy))
    if errors:  # pragma: no cover - construction guard
        raise EffectivePolicyError("Effective Policy failed its schema: " + "; ".join(error.message for error in errors))
    return policy


def backward_compatible_guidance(policy: dict[str, Any]) -> dict[str, Any]:
    source_alias = {
        "workspace_explicit": "explicit",
        "workspace_inferred": "inferred",
    }
    active = []
    active_instructions: list[str] = []
    for dimension, decision in sorted(policy["effective"].items()):
        if decision["source"] == "core_default":
            continue
        active.append(
            {
                "dimension": dimension,
                "value": decision["value"],
                "source": source_alias.get(decision["source"], decision["source"]),
            }
        )
        instruction = POLICY_GUIDANCE.get((dimension, decision["value"]))
        if instruction and instruction not in active_instructions:
            active_instructions.append(instruction)
    return {
        "adaptation_revision": policy["workspace_adaptation_revision"],
        "user_profile_revision": policy["user_profile_revision"],
        "context": policy["context"],
        "active_preferences": active,
        "instructions": active_instructions,
        "pending_preferences": policy["pending"],
        "precedence": [
            "The learner's explicit request in the current turn overrides stored presentation preferences.",
            "Workspace explicit, user explicit, inferred, strategy, then Core defaults apply in that order.",
            "Presentation policy never weakens mastery, source, prerequisite, privacy, or safety guards.",
        ],
        "effective_policy": {
            key: policy[key]
            for key in ["context", "core_version", "policy_fingerprint", "effective", "ignored", "invariants"]
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain the merged AtomLearn teaching policy")
    sub = parser.add_subparsers(dest="action", required=True)
    effective = sub.add_parser("effective", help="Compute the complete policy with source provenance")
    effective.add_argument("workspace")
    effective.add_argument("--context", choices=sorted(ADAPTATION_CONTEXTS), default="general")
    effective.add_argument("--overrides")
    effective.add_argument("--data-dir")
    explain = sub.add_parser("explain", help="Explain one policy dimension and overridden candidates")
    explain.add_argument("workspace")
    explain.add_argument("dimension", choices=sorted(POLICY_VALUES))
    explain.add_argument("--context", choices=sorted(ADAPTATION_CONTEXTS), default="general")
    explain.add_argument("--overrides")
    explain.add_argument("--data-dir")
    return parser


def read_overrides(path: str | None) -> dict[str, str]:
    if path is None:
        return {}
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return validate_overrides(raw)


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    policy = effective_for_workspace(
        args.workspace,
        args.context,
        current_turn=read_overrides(args.overrides),
        data_dir=args.data_dir,
    )
    if args.action == "effective":
        result = policy
    elif args.action == "explain":
        result = {
            "context": args.context,
            "dimension": args.dimension,
            "effective": policy["effective"].get(args.dimension),
            "ignored": [item for item in policy["ignored"] if item["dimension"] == args.dimension],
            "invariants": policy["invariants"],
            "policy_fingerprint": policy["policy_fingerprint"],
        }
    else:  # pragma: no cover
        raise EffectivePolicyError(f"Unhandled policy action: {args.action}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (EffectivePolicyError, UserProfileError, PlatformStateError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
