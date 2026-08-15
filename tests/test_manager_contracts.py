from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
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
    assert 'tomli>=2.0; python_version < \'3.11\'' in project
    assert '"atom-learn"' not in project
    common = (MANAGER_ROOT / "atomlearn_manager" / "common.py").read_text(encoding="utf-8")
    manager = (MANAGER_ROOT / "atomlearn_manager" / "manager.py").read_text(encoding="utf-8")
    assert "from atomlearn" not in common + manager


def test_every_manager_release_and_launcher_argument_has_help() -> None:
    from atomlearn_manager import builder, cli, launcher

    def require_help(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    require_help(child)
                continue
            assert action.help not in {None, argparse.SUPPRESS}, f"missing help for {action.dest}"

    for parser in [builder.build_parser(), cli.build_parser(), launcher.build_parser()]:
        require_help(parser)


def test_course_runtime_has_no_update_apply_command() -> None:
    atomlearn = importlib.import_module("atomlearn")
    parser = atomlearn.build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    assert "update" not in action.choices
    assert "rollback-core" not in action.choices


def test_stable_manifest_contract_requires_signature_and_immutable_release_source() -> None:
    schema = json.loads((SCHEMAS / "release-manifest.schema.json").read_text(encoding="utf-8"))
    assert "signature" in schema["required"]
    assert "manager_artifact" in schema["required"]
    assert schema["properties"]["source"]["properties"]["kind"]["const"] == "github_release"
    assert schema["properties"]["signature"]["properties"]["algorithm"]["const"] == "ed25519"


def test_release_gate_fixture_attests_every_phase6_boundary() -> None:
    schema = json.loads((SCHEMAS / "release-gate-report.schema.json").read_text(encoding="utf-8"))
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "releases" / "gate-report-v0.13.0.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(fixture)
    assert set(fixture["gates"]) == {
        "full_tests",
        "skill_validator",
        "migration_fixtures",
        "manager_upgrade_tests",
        "security_archive_tests",
        "property_tests",
        "fault_injection",
        "privacy_attacks",
        "replay_compatibility",
    }
    assert all(fixture["gates"].values())


def test_release_gate_report_requires_attested_ci_and_never_overwrites(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "release" / "gate.py"),
        "write",
        "--tag",
        "v0.13.0",
        "--commit-sha",
        "b" * 40,
        "--output",
        str((tmp_path / "gate.json").resolve()),
    ]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    blocked = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert blocked.returncode == 2
    assert "attested release CI" in blocked.stderr
    environment["ATOMLEARN_RELEASE_GATES_PASSED"] = "1"
    created = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["commit_sha"] == "b" * 40
    overwrite = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert overwrite.returncode == 2
    assert "File exists" in overwrite.stderr


def test_tag_release_workflow_is_signed_gated_and_immutable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "needs: release-gates" in workflow
    assert "secrets.ATOMLEARN_RELEASE_PRIVATE_KEY" in workflow
    assert "--channel stable" in workflow
    assert "--manager-artifact" in workflow
    assert "gh release create" in workflow
    assert "branches:" not in workflow
