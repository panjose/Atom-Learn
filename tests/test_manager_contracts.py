from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
MANAGER_ROOT = ROOT / "manager"
SCHEMAS = MANAGER_ROOT / "atomlearn_manager" / "schemas"
sys.path.insert(0, str(MANAGER_ROOT))
sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))


def test_manager_schemas_are_strict_and_valid() -> None:
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_manager_is_a_separate_distribution_and_console_surface() -> None:
    project = (MANAGER_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "atomlearn-manager"' in project
    assert 'atomlearn-manager = "atomlearn_manager.cli:main"' in project
    assert 'atomlearn-release = "atomlearn_manager.builder:main"' in project
    assert 'atomlearn-core = "atomlearn_manager.launcher:main"' in project
    assert '"atom-learn"' not in project
    common = (MANAGER_ROOT / "atomlearn_manager" / "common.py").read_text(encoding="utf-8")
    manager = (MANAGER_ROOT / "atomlearn_manager" / "manager.py").read_text(encoding="utf-8")
    assert "from atomlearn" not in common + manager


def test_course_runtime_has_no_update_apply_command() -> None:
    atomlearn = importlib.import_module("atomlearn")
    parser = atomlearn.build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    assert "update" not in action.choices
    assert "rollback-core" not in action.choices


def test_stable_manifest_contract_requires_signature_and_immutable_release_source() -> None:
    schema = json.loads((SCHEMAS / "release-manifest.schema.json").read_text(encoding="utf-8"))
    assert "signature" in schema["required"]
    assert schema["properties"]["source"]["properties"]["kind"]["const"] == "github_release"
    assert schema["properties"]["signature"]["properties"]["algorithm"]["const"] == "ed25519"
