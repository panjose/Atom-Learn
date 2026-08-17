from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atom-learn" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from migrations import (  # noqa: E402
    MigrationError,
    MigrationRegistry,
    StateTarget,
    build_plan,
    plan_target,
    validate_catalog,
)
from platform_state import (  # noqa: E402
    CORE_ROOT,
    FileLock,
    NamespaceStore,
    PlatformStateError,
    core_version,
    load_core_manifest,
    resolve_user_data_root,
    tree_fingerprint,
)


def test_core_manifest_matches_package_and_declares_all_v2_namespaces() -> None:
    manifest = load_core_manifest()
    assert manifest["core_version"] == "0.14.0" == core_version()
    assert manifest["release_channel"] == "development"
    assert manifest["feature_defaults"] == {
        "global_personalization": False,
        "strategy_experiments": False,
        "capsule_export": False,
        "release_manager": False,
    }
    assert {"workspace_core", "user_profile", "user_strategy"} <= set(manifest["schemas"])
    assert manifest["artifact_sha256"].startswith("sha256:")


def test_user_data_root_is_lazy_and_requires_absolute_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "not-created-yet"
    monkeypatch.setenv("ATOMLEARN_DATA_DIR", str(target))
    assert resolve_user_data_root() == target.resolve()
    assert not target.exists()
    assert resolve_user_data_root(create=True) == target.resolve()
    assert target.is_dir()
    monkeypatch.setenv("ATOMLEARN_DATA_DIR", "relative/state")
    with pytest.raises(PlatformStateError, match="must be absolute"):
        resolve_user_data_root()


def test_namespace_store_rejects_stale_revision_and_preserves_current_state(tmp_path: Path) -> None:
    store = NamespaceStore(tmp_path / "profiles" / "default", "user_profile")
    initial = store.create({"kind": "fixture", "schema_version": 1, "revision": 0, "value": "a"})
    assert initial["revision"] == 0
    updated = store.write({**initial, "value": "b"}, expected_revision=0)
    assert updated["revision"] == 1
    with pytest.raises(PlatformStateError, match="Stale user_profile revision"):
        store.write({**updated, "value": "c"}, expected_revision=0)
    assert store.read()["value"] == "b"


def test_file_lock_refuses_a_second_writer(tmp_path: Path) -> None:
    lock_path = tmp_path / "state.lock"
    with FileLock(lock_path):
        with pytest.raises(PlatformStateError, match="Timed out"):
            with FileLock(lock_path, timeout=0.05):
                pass


def test_migration_registry_is_deterministic_and_requires_a_complete_path() -> None:
    registry = MigrationRegistry()

    def one_to_two(value: dict) -> dict:
        value["schema_version"] = 2
        value["renamed"] = value.pop("old")
        return value

    registry.register("fixture", 1, 2, one_to_two)
    source = {"schema_version": 1, "old": "value"}
    assert registry.migrate_document("fixture", source, 2) == {"schema_version": 2, "renamed": "value"}
    assert source == {"schema_version": 1, "old": "value"}
    with pytest.raises(MigrationError, match="No deterministic migration"):
        registry.migrate_document("fixture", source, 3)
    with pytest.raises(MigrationError, match="exactly one"):
        registry.register("fixture", 1, 3, one_to_two)


def make_workspace(path: Path, schema_version: int = 1) -> Path:
    state = path / ".atomlearn"
    state.mkdir(parents=True)
    (state / "course.yaml").write_text(
        yaml.safe_dump({"schema_version": schema_version, "revision": 0, "id": "fixture.course"}),
        encoding="utf-8",
    )
    return path


def test_plan_and_validate_current_workspace_without_creating_user_data(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    workspace = make_workspace(tmp_path / "course")
    plan = build_plan(data_root=data_root, workspaces=[workspace])
    assert plan["target_count"] == 1
    assert plan["counts"]["compatible"] == 1
    assert plan["items"][0]["reason"] == "current_write_schema"
    validation = validate_catalog(data_root=data_root, workspaces=[workspace])
    assert validation["ok"] is True
    assert not data_root.exists()


def test_future_workspace_schema_needs_review_and_fails_validation(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    workspace = make_workspace(tmp_path / "future", schema_version=99)
    target = StateTarget("workspace_core", workspace / ".atomlearn" / "course.yaml", "workspace")
    item = plan_target(target, load_core_manifest())
    assert item["status"] == "needs_review"
    result = validate_catalog(data_root=data_root, workspaces=[workspace])
    assert result["ok"] is False
    assert "cannot read workspace_core schema 99" in result["targets"][0]["errors"][0]


def test_installed_version_and_migration_status_are_read_only(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["ATOMLEARN_DATA_DIR"] = str(data_root)
    before = tree_fingerprint(CORE_ROOT)
    version = subprocess.run(["atomlearn", "version"], cwd=ROOT, env=environment, text=True, capture_output=True)
    status = subprocess.run(
        ["atomlearn", "migrate", "status"], cwd=ROOT, env=environment, text=True, capture_output=True
    )
    assert version.returncode == 0, version.stderr
    assert status.returncode == 0, status.stderr
    assert json.loads(version.stdout)["core_version"] == "0.14.0"
    assert json.loads(status.stdout)["data_root_exists"] is False
    assert not data_root.exists()
    assert tree_fingerprint(CORE_ROOT) == before
