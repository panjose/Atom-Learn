from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))
sys.path.insert(0, str(ROOT / "manager"))

from effective_policy import (  # noqa: E402
    INVARIANTS,
    POLICY_DIMENSION_CONTEXTS,
    POLICY_VALUES,
    EffectivePolicyError,
    merge_effective_policy,
)
from migrations import MigrationError, MigrationRegistry  # noqa: E402
from platform_state import load_core_manifest  # noqa: E402
from atomlearn_manager.common import version_tuple  # noqa: E402


TEACHING_PAIRS = [
    (dimension, value)
    for dimension, values in POLICY_VALUES.items()
    if "teaching" in POLICY_DIMENSION_CONTEXTS[dimension]
    for value in sorted(values)
]


@settings(deadline=None)
@given(st.sampled_from(TEACHING_PAIRS))
def test_current_turn_always_wins_without_changing_invariants(pair: tuple[str, str]) -> None:
    dimension, requested = pair
    alternatives = sorted(POLICY_VALUES[dimension])
    lower = alternatives[0]
    profile = {
        "preferences": {
            dimension: {
                "status": "active",
                "active_value": lower,
                "source": "explicit",
                "confidence": 1.0,
            }
        }
    }
    policy = merge_effective_policy(
        context="teaching",
        current_turn={dimension: requested},
        workspace_profile=profile,
        workspace_revision=8,
        user_profile=profile,
        user_revision=7,
        course_strategy=[
            {"dimension": dimension, "value": lower, "source": "course_strategy", "source_revision": 6}
        ],
        user_strategy=[
            {"dimension": dimension, "value": lower, "source": "user_strategy", "source_revision": 5}
        ],
    )
    assert policy["effective"][dimension] == {
        "value": requested,
        "source": "current_turn",
        "source_revision": 0,
    }
    assert policy["invariants"] == INVARIANTS
    assert set(policy["invariants"].values()) == {"enforced"}


@settings(deadline=None)
@given(st.permutations([
    {"dimension": "response.detail", "value": "concise", "source": "course_strategy", "source_revision": 3},
    {"dimension": "explanation.order", "value": "example_first", "source": "course_strategy", "source_revision": 3},
    {"dimension": "feedback.style", "value": "direct", "source": "course_strategy", "source_revision": 3},
]))
def test_effective_policy_fingerprint_is_order_independent(candidates: tuple[dict, ...]) -> None:
    first = merge_effective_policy(context="teaching", course_strategy=list(candidates))
    second = merge_effective_policy(context="teaching", course_strategy=list(reversed(candidates)))
    assert first == second
    assert first["policy_fingerprint"] == second["policy_fingerprint"]


JSON_SCALAR = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=40))
SAFE_KEYS = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True).filter(
    lambda key: key not in {"schema_version", "migrated"}
)


@settings(deadline=None)
@given(st.dictionaries(SAFE_KEYS, JSON_SCALAR, max_size=12))
def test_deterministic_migrations_are_pure_and_idempotent(payload: dict) -> None:
    registry = MigrationRegistry()

    def one_to_two(value: dict) -> dict:
        value["schema_version"] = 2
        value["migrated"] = True
        return value

    registry.register("fixture", 1, 2, one_to_two)
    source = {**payload, "schema_version": 1}
    before = copy.deepcopy(source)
    migrated = registry.migrate_document("fixture", source, 2)
    replayed = registry.migrate_document("fixture", migrated, 2)
    assert source == before
    assert migrated == replayed
    assert migrated["schema_version"] == 2


@settings(deadline=None)
@given(st.one_of(st.none(), st.booleans(), st.text(), st.integers(max_value=0)))
def test_invalid_migration_versions_fail_closed(schema_version: object) -> None:
    registry = MigrationRegistry()
    with pytest.raises(MigrationError, match="valid schema_version"):
        registry.migrate_document("fixture", {"schema_version": schema_version}, 1)


@settings(deadline=None)
@given(st.text(min_size=1).filter(lambda value: value not in POLICY_VALUES))
def test_unknown_policy_dimensions_fail_closed(dimension: str) -> None:
    with pytest.raises(EffectivePolicyError, match="protected invariants cannot be overridden"):
        merge_effective_policy(context="teaching", current_turn={dimension: "lower"})


@settings(deadline=None)
@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=0, max_value=10_000))
def test_semver_numeric_prerelease_order_is_not_lexicographic(left: int, right: int) -> None:
    comparison = version_tuple(f"1.2.3-rc.{left}") < version_tuple(f"1.2.3-rc.{right}")
    assert comparison is (left < right)
    assert version_tuple(f"1.2.3-rc.{left}") < version_tuple("1.2.3")


def test_v2_capabilities_remain_default_off_and_explicitly_gated() -> None:
    manifest = load_core_manifest()
    assert manifest["feature_defaults"] == {
        "global_personalization": False,
        "strategy_experiments": False,
        "capsule_export": False,
        "release_manager": False,
    }
    policy = merge_effective_policy(context="teaching")
    assert all(item["source"] == "core_default" for item in policy["effective"].values())
    assert policy["ignored"] == []
