from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
import yaml
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


def test_capability_ledger_is_strict_versioned_and_truthful() -> None:
    schema = json.loads(
        (ROOT / "atom-learn" / "assets" / "schemas" / "capability-ledger.schema.json").read_text(encoding="utf-8")
    )
    ledger = yaml.safe_load((ROOT / "atom-learn" / "assets" / "capabilities.yaml").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(ledger)
    assert ledger["core_version"] == "0.13.0"
    identifiers = [item["id"] for item in ledger["capabilities"]]
    assert len(identifiers) == len(set(identifiers))
    for capability in ledger["capabilities"]:
        if capability["status"] == "planned":
            assert capability["default_mode"] == "unavailable"
            assert capability["public_claim"] is False
            assert capability["implementation"] == []
            assert capability["verification"] == []


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
    report_path = (tmp_path / "gate.json").resolve()
    command = [
        sys.executable,
        str(ROOT / "release" / "gate.py"),
        "write",
        "--tag",
        "v0.13.0",
        "--commit-sha",
        "b" * 40,
        "--output",
        str(report_path),
    ]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    blocked = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert blocked.returncode == 2
    assert "attested release CI" in blocked.stderr
    environment["ATOMLEARN_RELEASE_GATES_PASSED"] = "1"
    created = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["commit_sha"] == "b" * 40
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_path.read_bytes() == json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    overwrite = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert overwrite.returncode == 2
    assert "File exists" in overwrite.stderr


def test_release_manifest_network_failures_are_typed_and_never_assert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from atomlearn_manager import manager as manager_module
    from atomlearn_manager.common import ManagerError

    def denied(*args: object, **kwargs: object) -> None:
        raise HTTPError("https://github.com/private/release.json", 404, "Not Found", {}, None)

    monkeypatch.setattr(manager_module, "urlopen", denied)
    with pytest.raises(ManagerError) as caught:
        manager_module._manifest_from_source("https://github.com/private/release.json")
    payload = caught.value.as_dict()
    assert payload == {
        "ok": False,
        "error": {
            "code": "release_manifest_http_error",
            "message": "Release manifest request failed with HTTP 404; the release may be private, unavailable, or inaccessible",
            "retryable": False,
            "details": {"host": "github.com", "status": 404},
        },
    }

    monkeypatch.setattr(manager_module, "load_trust", lambda root: {})
    monkeypatch.setattr(manager_module, "_manifest_from_source", lambda source: (None, "offline"))
    with pytest.raises(ManagerError, match="required to plan") as missing:
        manager_module.plan_update(tmp_path, "0.13.0", "https://github.com/private/release.json", None, None, [], "stable")
    assert missing.value.code == "release_manifest_required"
    assert "assert manifest is not None" not in (MANAGER_ROOT / "atomlearn_manager" / "manager.py").read_text(encoding="utf-8")


def test_manager_cli_serializes_stable_error_envelopes(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "atomlearn_manager.cli",
            "--manager-root",
            str((tmp_path / "missing-manager").resolve()),
            "version",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(MANAGER_ROOT)},
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error["ok"] is False
    assert error["error"]["code"] == "manager_error"


def test_tag_release_workflow_is_signed_gated_and_immutable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "needs: release-gates" in workflow
    assert "secrets.ATOMLEARN_RELEASE_PRIVATE_KEY" in workflow
    assert "--channel stable" in workflow
    assert "--manager-artifact" in workflow
    assert "gh release create" in workflow
    assert "branches:" not in workflow
