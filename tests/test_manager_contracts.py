from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest
import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
MANAGER_ROOT = ROOT / "manager"
SCHEMAS = MANAGER_ROOT / "atomlearn_manager" / "schemas"
sys.path.insert(0, str(MANAGER_ROOT))
sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))


def test_episode_and_exam_schedule_states_are_declared_in_trusted_migration_catalogs() -> None:
    from atomlearn_manager.statecopy import WORKSPACE_TARGETS as MANAGER_WORKSPACE_TARGETS
    from migrations import WORKSPACE_TARGETS as CORE_WORKSPACE_TARGETS
    from platform_state import load_core_manifest

    expected = ".atomlearn/episodes/state.yaml"
    assert CORE_WORKSPACE_TARGETS["workspace_episodes"] == expected
    assert MANAGER_WORKSPACE_TARGETS["workspace_episodes"] == expected
    assert load_core_manifest()["schemas"]["workspace_episodes"] == {"read": [1], "write": 1}
    schedule = ".atomlearn/exam/schedule.yaml"
    assert CORE_WORKSPACE_TARGETS["workspace_exam_schedule"] == schedule
    assert MANAGER_WORKSPACE_TARGETS["workspace_exam_schedule"] == schedule
    assert load_core_manifest()["schemas"]["workspace_exam_schedule"] == {"read": [1], "write": 1}


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
    assert ledger["schema_version"] == 2
    assert ledger["core_version"] == "0.15.0"
    assert ledger["claim_policy"] == {
        "stable_runtime_profiles": ["base"],
        "developer_extras": ["ocr", "scale", "semantic"],
        "engineering": "verified",
        "harness_behavior": "not_evaluated",
        "learning_effect": "not_established",
        "causal_learning_claims_allowed": False,
    }
    identifiers = [item["id"] for item in ledger["capabilities"]]
    assert len(identifiers) == len(set(identifiers))
    for capability in ledger["capabilities"]:
        availability = capability["availability"]
        if capability["status"] == "planned":
            assert capability["default_mode"] == "unavailable"
            assert capability["public_claim"] is False
            assert capability["implementation"] == []
            assert capability["verification"] == []
            assert availability == {
                "level": "planned",
                "runtime": "none",
                "artifact": "not_distributed",
                "user_entrypoint": False,
            }
        if availability["level"] == "stable":
            assert capability["status"] == "implemented"
            assert availability["user_entrypoint"] is True
            assert (availability["runtime"], availability["artifact"]) in {
                ("base", "core_runtime"),
                ("manager", "manager_distribution"),
            }
        if availability["runtime"] == "optional_extra":
            assert availability["level"] in {"developer", "experimental"}
            assert availability["artifact"] == "not_distributed"
    learned = next(item for item in ledger["capabilities"] if item["id"] == "rag.learned_local_scale_v1")
    assert learned["availability"] == {
        "level": "developer",
        "runtime": "optional_extra",
        "artifact": "not_distributed",
        "user_entrypoint": True,
    }


def test_runtime_profile_registry_matches_manager_recipes_and_delivery_claims() -> None:
    from atomlearn_manager.runtime import PROFILE_SPECS

    schema = json.loads(
        (ROOT / "atom-learn" / "assets" / "schemas" / "runtime-profile-registry.schema.json").read_text(
            encoding="utf-8"
        )
    )
    registry = yaml.safe_load(
        (ROOT / "atom-learn" / "assets" / "runtime-profiles.yaml").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(registry)
    assert registry["stable_profiles"] == ["base"]
    assert set(PROFILE_SPECS) == {"base", "scale", "semantic-cpu", "ocr", "semantic-gpu"}
    for profile in registry["profiles"]:
        expected = dict(profile)
        expected.pop("release_status")
        name = expected.pop("name")
        assert expected == PROFILE_SPECS[name]
    semantic = next(item for item in registry["profiles"] if item["name"] == "semantic-cpu")
    assert semantic["model_policy"]["silent_download"] is False
    assert semantic["model_policy"]["trust_remote_code"] is False
    assert "safetensors" in semantic["model_policy"]["allowed_weight_formats"]
    ocr = next(item for item in registry["profiles"] if item["name"] == "ocr")
    assert ocr["native_requirements"] == [
        {"id": "tesseract", "command": "tesseract", "version_args": ["--version"]}
    ]


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
    packaged_trust = MANAGER_ROOT / "atomlearn_manager" / "trust" / "atomlearn-trust-bundle.json"
    assert packaged_trust.read_bytes() == (ROOT / "release" / "atomlearn-trust-bundle.json").read_bytes()
    assert '"bridge/scripts/resolve.py"' in project
    assert '"trust/atomlearn-trust-bundle.json"' in project


def test_every_manager_release_and_launcher_argument_has_help() -> None:
    from atomlearn_manager import builder, cli, launcher, runtime

    def require_help(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    require_help(child)
                continue
            assert action.help not in {None, argparse.SUPPRESS}, f"missing help for {action.dest}"

    for parser in [builder.build_parser(), cli.build_parser(), launcher.build_parser(), runtime.build_parser()]:
        require_help(parser)


def test_release_workflow_reads_version_on_python_310() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "import importlib.metadata as metadata" in workflow
    assert "metadata.version('atom-learn')" in workflow
    assert "import tomllib" not in workflow


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

    v2 = json.loads((SCHEMAS / "release-manifest-v2.schema.json").read_text(encoding="utf-8"))
    assert v2["properties"]["manifest_version"]["const"] == 2
    assert {"skill_protocol", "runtime_bundles", "capabilities", "smoke_fixture_sha256", "trust"} <= set(v2["required"])
    supported_smoke = set(
        v2["properties"]["capabilities"]["properties"]["required_smoke"]["items"]["enum"]
    )
    ledger = yaml.safe_load((ROOT / "atom-learn" / "assets" / "capabilities.yaml").read_text(encoding="utf-8"))
    assert set(ledger["required_smoke"]) <= supported_smoke
    stable_profile_schema = v2["properties"]["capabilities"]["properties"]["stable_runtime_profiles"]
    assert stable_profile_schema["uniqueItems"] is True
    assert set(stable_profile_schema["items"]["enum"]) == {"base", "scale", "semantic-cpu", "ocr"}


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
        "v0.15.0",
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
    from atomlearn_manager import transport
    from atomlearn_manager.common import ManagerError

    class DeniedOpener:
        def open(self, *args: object, **kwargs: object) -> None:
            raise HTTPError("https://github.com/private/release.json", 404, "Not Found", {}, None)

    monkeypatch.setattr(transport, "build_opener", lambda *args: DeniedOpener())
    monkeypatch.setattr(transport, "_credential", lambda: (None, "none"))
    with pytest.raises(ManagerError) as caught:
        manager_module._manifest_from_source("https://github.com/private/release.json")
    payload = caught.value.as_dict()
    assert payload == {
        "ok": False,
        "error": {
            "code": "release_asset_http_error",
            "message": "GitHub Release asset request failed with HTTP 404; the release may be private, unavailable, or inaccessible",
            "retryable": False,
            "details": {"host": "github.com", "status": 404, "credential_provider": "none"},
        },
    }

    monkeypatch.setattr(manager_module, "load_trust", lambda root: {})
    monkeypatch.setattr(manager_module, "_manifest_from_source", lambda source: (None, "offline"))
    with pytest.raises(ManagerError, match="required to plan") as missing:
        manager_module.plan_update(tmp_path, "0.13.0", "https://github.com/private/release.json", None, None, None, [], "stable")
    assert missing.value.code == "release_manifest_required"
    assert "assert manifest is not None" not in (MANAGER_ROOT / "atomlearn_manager" / "manager.py").read_text(encoding="utf-8")


def test_private_release_transport_uses_bounded_credential_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from atomlearn_manager import transport

    requests: list[tuple[str, str | None]] = []

    class Response:
        def __init__(self, content: bytes, url: str) -> None:
            self.content = content
            self.url = url
            self.offset = 0

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                size = len(self.content) - self.offset
            result = self.content[self.offset : self.offset + size]
            self.offset += len(result)
            return result

        def geturl(self) -> str:
            return self.url

        def close(self) -> None:
            pass

    class AssetOpener:
        calls = 0

        def open(self, request: Request, **kwargs: object) -> Response:
            requests.append((request.full_url, request.get_header("Authorization")))
            self.calls += 1
            if self.calls == 1:
                raise HTTPError(request.full_url, 404, "Not Found", {}, None)
            return Response(b"private-manifest", request.full_url)

    class MetadataOpener:
        def open(self, request: Request, **kwargs: object) -> Response:
            requests.append((request.full_url, request.get_header("Authorization")))
            payload = {"assets": [{"name": "manifest.json", "url": "https://api.github.com/repos/panjose/Atom-Learn/releases/assets/7"}]}
            return Response(json.dumps(payload).encode("utf-8"), request.full_url)

    openers = iter([AssetOpener(), MetadataOpener()])
    monkeypatch.setattr(transport, "build_opener", lambda *args: next(openers))
    monkeypatch.setattr(transport, "_credential", lambda: ("secret-token", "environment:ATOMLEARN_GITHUB_TOKEN"))
    content, provider = transport.fetch_release_bytes(
        "https://github.com/panjose/Atom-Learn/releases/download/v0.14.0/manifest.json",
        accept="application/json",
        limit=1024,
    )
    assert content == b"private-manifest"
    assert provider == "environment:ATOMLEARN_GITHUB_TOKEN"
    assert requests[0][1] is None
    assert requests[1][0].startswith("https://api.github.com/repos/panjose/Atom-Learn/releases/tags/")
    assert requests[1][1] == "Bearer secret-token"
    assert requests[2][1] == "Bearer secret-token"


def test_bridge_resolver_passes_the_marker_bound_manager_root_without_a_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from types import SimpleNamespace

    resolver = MANAGER_ROOT / "atomlearn_manager" / "bridge" / "scripts" / "resolve.py"
    spec = importlib.util.spec_from_file_location("atomlearn_bridge_resolver_test", resolver)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bridge = tmp_path / "codex home" / "skills" / "atom-learn"
    script = bridge / "scripts" / "resolve.py"
    script.parent.mkdir(parents=True)
    script.write_text("test location", encoding="utf-8")
    bound_root = (tmp_path / "custom manager root").resolve()
    (bridge / ".atomlearn-bridge.json").write_text(
        json.dumps(
            {
                "kind": "atomlearn.codex-bridge",
                "schema_version": 2,
                "owner": "atomlearn-manager",
                "manager_root": str(bound_root),
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(module.shutil, "which", lambda name: "C:/Program Files/AtomLearn/atomlearn-manager.exe")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    assert module.main() == 0
    assert calls == [
        (
            [
                "C:/Program Files/AtomLearn/atomlearn-manager.exe",
                "--manager-root",
                str(bound_root),
                "codex",
                "resolve",
                "--json",
            ],
            {"check": False},
        )
    ]


def test_stable_bootstrap_refuses_legacy_or_incompatible_bridge_protocols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atomlearn_manager import bootstrap
    from atomlearn_manager.common import ManagerError

    trust = {"keys": {}}
    trust_plan = {"ready": True}
    monkeypatch.setattr(bootstrap, "_trust_plan", lambda *args: (trust, trust_plan))
    monkeypatch.setattr(bootstrap, "validate_release_manifest", lambda *args, **kwargs: None)
    legacy = {"manifest_version": 1, "version": "0.13.0"}
    monkeypatch.setattr(bootstrap, "_manifest_from_source", lambda source: (legacy, source))
    with pytest.raises(ManagerError, match="manifest v2 bridge protocol"):
        bootstrap.bootstrap_plan(
            tmp_path / "manager",
            "0.13.0",
            expected_fingerprint="sha256:" + "1" * 64,
            manifest_source="local.json",
            home=tmp_path / "codex",
        )

    incompatible = {
        "manifest_version": 2,
        "version": "0.13.0",
        "skill_protocol": {"bridge_min": 2, "bridge_max": 3},
    }
    monkeypatch.setattr(bootstrap, "_manifest_from_source", lambda source: (incompatible, source))
    with pytest.raises(ManagerError, match="incompatible"):
        bootstrap.bootstrap_plan(
            tmp_path / "manager",
            "0.13.0",
            expected_fingerprint="sha256:" + "1" * 64,
            manifest_source="local.json",
            home=tmp_path / "codex",
        )


def test_trust_bundle_levels_and_owned_codex_bridge(tmp_path: Path) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from atomlearn_manager.codex import bridge_status, install_bridge, migrate_source_copy, source_copy_status
    from atomlearn_manager.common import ManagerError
    from atomlearn_manager.manifest import (
        accept_tofu,
        break_glass_trust,
        initialize_trust_bundle,
        key_fingerprint,
        pin_trust,
    )

    root = tmp_path / "manager"
    root.mkdir()
    trust = initialize_trust_bundle(root, ROOT / "release" / "atomlearn-trust-bundle.json", None)
    assert trust["trust_level"] == "unverified"
    fingerprint = next(iter(trust["keys"].values()))["fingerprint"]
    accepted = accept_tofu(root, fingerprint, True)
    assert accepted["trust_level"] == "verified_tofu"
    assert accepted["revision"] == 2
    pinned = pin_trust(root, fingerprint, True)
    assert pinned["trust_level"] == "pinned"
    assert pinned["revision"] == 3
    replacement_public = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode("ascii")
    replacement_fingerprint = key_fingerprint(replacement_public)
    replacement_bundle = tmp_path / "replacement-trust.json"
    replacement_bundle.write_text(
        json.dumps(
            {
                "kind": "atomlearn.trust-bundle",
                "schema_version": 1,
                "bundle_version": 2,
                "previous_bundle_version": 1,
                "repository": "panjose/Atom-Learn",
                "keys": [
                    {
                        "key_id": "replacement",
                        "algorithm": "ed25519",
                        "public_key": replacement_public,
                        "fingerprint": replacement_fingerprint,
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    replaced = break_glass_trust(root, replacement_bundle, replacement_fingerprint, True)
    assert replaced["trust_level"] == "pinned"
    assert replaced["bundle_version"] == 2
    assert replaced["revision"] == 4

    home = tmp_path / "codex"
    foreign = home / "skills" / "atom-learn"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("foreign", encoding="utf-8")
    with pytest.raises(ManagerError, match="Refusing to overwrite"):
        install_bridge(root, home)

    clean_home = tmp_path / "clean-codex"
    installed = install_bridge(root, clean_home)
    assert installed["installed"] is True
    state = bridge_status(root, clean_home)
    assert state["content_valid"] is True
    marker = json.loads((clean_home / "skills" / "atom-learn" / ".atomlearn-bridge.json").read_text(encoding="utf-8"))
    assert marker["schema_version"] == 2
    assert marker["manager_root"] == str(root.resolve())
    repeated = install_bridge(root, clean_home)
    assert repeated["installed"] is False
    assert repeated["idempotent"] is True
    assert repeated["previous_bridge"] is None
    (clean_home / "skills" / "atom-learn" / "unexpected.txt").write_text("extra", encoding="utf-8")
    assert bridge_status(root, clean_home)["exact_inventory"] is False
    repaired_extra = install_bridge(root, clean_home, repair=True, confirmed=True)
    assert Path(repaired_extra["previous_bridge"]).is_dir()
    (clean_home / "skills" / "atom-learn" / "SKILL.md").write_text("damaged", encoding="utf-8")
    assert bridge_status(root, clean_home)["content_valid"] is False
    repaired = install_bridge(root, clean_home, repair=True, confirmed=True)
    assert Path(repaired["previous_bridge"]).is_dir()
    assert bridge_status(root, clean_home)["content_valid"] is True

    unknown_home = tmp_path / "unknown-codex"
    unknown = unknown_home / "skills" / "atom-learn"
    unknown.mkdir(parents=True)
    (unknown / "SKILL.md").write_text("locally modified", encoding="utf-8")
    classification = source_copy_status(root, unknown_home)
    assert classification["classification"] == "unknown_or_modified_source_copy"
    assert classification["migratable"] is False
    with pytest.raises(ManagerError, match="will not be replaced"):
        migrate_source_copy(root, unknown_home, confirmed=True)
    assert (unknown / "SKILL.md").read_text(encoding="utf-8") == "locally modified"

    empty_home = tmp_path / "empty-directory-codex"
    empty_target = empty_home / "skills" / "atom-learn"
    (empty_target / "unexpected-empty").mkdir(parents=True)
    (empty_target / "SKILL.md").write_text("otherwise regular", encoding="utf-8")
    empty = source_copy_status(root, empty_home)
    assert empty["classification"] == "unsafe_source_copy"
    assert empty["migratable"] is False

    linked_home = tmp_path / "linked-codex"
    linked_target = linked_home / "skills" / "atom-learn"
    linked_target.parent.mkdir(parents=True)
    try:
        linked_target.symlink_to(unknown, target_is_directory=True)
    except OSError:
        pass  # Windows may deny unprivileged symlink creation.
    else:
        linked = source_copy_status(root, linked_home)
        assert linked["classification"] == "unsafe_linked_path"
        assert linked["migratable"] is False
        with pytest.raises(ManagerError, match="will not be replaced"):
            migrate_source_copy(root, linked_home, confirmed=True)
        assert linked_target.resolve() == unknown.resolve()
        broken_home = tmp_path / "broken-link-codex"
        broken_target = broken_home / "skills" / "atom-learn"
        broken_target.parent.mkdir(parents=True)
        broken_target.symlink_to(tmp_path / "does-not-exist", target_is_directory=True)
        broken = source_copy_status(root, broken_home)
        assert broken["classification"] == "unsafe_linked_path"
        assert broken["migratable"] is False


def test_trust_rotation_requires_the_current_key_signature(tmp_path: Path) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from atomlearn_manager.manifest import (
        _bundle_payload,
        initialize_trust,
        key_fingerprint,
        rotate_trust,
    )

    def public(private: Ed25519PrivateKey) -> str:
        return base64.b64encode(
            private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ).decode("ascii")

    old_private = Ed25519PrivateKey.generate()
    new_private = Ed25519PrivateKey.generate()
    old_public = public(old_private)
    new_public = public(new_private)
    root = tmp_path / "manager"
    root.mkdir()
    initialize_trust(root, "old", old_public, "panjose/Atom-Learn")
    bundle = {
        "kind": "atomlearn.trust-bundle",
        "schema_version": 1,
        "bundle_version": 2,
        "previous_bundle_version": 1,
        "repository": "panjose/Atom-Learn",
        "keys": [
            {
                "key_id": "old", "algorithm": "ed25519", "public_key": old_public,
                "fingerprint": key_fingerprint(old_public), "status": "retiring",
            },
            {
                "key_id": "new", "algorithm": "ed25519", "public_key": new_public,
                "fingerprint": key_fingerprint(new_public), "status": "active",
            },
        ],
        "signature": {"algorithm": "ed25519", "key_id": "old", "value": "AA=="},
    }
    bundle["signature"]["value"] = base64.b64encode(old_private.sign(_bundle_payload(bundle))).decode("ascii")
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    rotated = rotate_trust(root, path, True)
    assert rotated["bundle_version"] == 2
    assert rotated["keys"]["old"]["status"] == "retiring"
    assert rotated["keys"]["new"]["status"] == "active"


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


def test_manager_atomic_writes_use_short_target_independent_temp_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atomlearn_manager import common

    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def capture_replace(source: str | Path, target: str | Path) -> None:
        replaced.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(common.os, "replace", capture_replace)
    text_target = tmp_path / "a-very-long-manager-state-name-that-must-not-be-copied.yaml"
    bytes_target = tmp_path / "a-very-long-manager-payload-name-that-must-not-be-copied.bin"
    common.atomic_text(text_target, "ok\n")
    common.atomic_bytes(bytes_target, b"ok\n")

    assert text_target.read_text(encoding="utf-8") == "ok\n"
    assert bytes_target.read_bytes() == b"ok\n"
    assert [target for _, target in replaced] == [text_target, bytes_target]
    for temporary, target in replaced:
        assert temporary.parent == target.parent
        assert temporary.name.startswith(".tmp-")
        assert len(temporary.name) == 17
        assert target.name not in temporary.name


def test_runtime_bootstrap_uses_only_cpython_bundled_pip() -> None:
    from atomlearn_manager.runtime import _bundled_pip_wheel

    wheel = _bundled_pip_wheel()
    assert wheel.name.startswith("pip-")
    assert wheel.suffix == ".whl"
    runtime_source = (MANAGER_ROOT / "atomlearn_manager" / "runtime.py").read_text(encoding="utf-8")
    assert "venv.EnvBuilder(with_pip=False" in runtime_source
    assert "venv.EnvBuilder(with_pip=True" not in runtime_source


def test_tag_release_workflow_is_signed_gated_and_immutable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "needs: [release-gates, scale-rag]" in workflow
    assert "Stable named RAG gate" in workflow
    assert 'python -m pip install -e ".[dev,scale]"' in workflow
    assert "secrets.ATOMLEARN_RELEASE_PRIVATE_KEY" in workflow
    assert "--channel stable" in workflow
    assert "--manager-artifact" in workflow
    assert "atomlearn-runtime-bundle" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "--runtime-bundle" in workflow
    assert "--trust-bundle release/atomlearn-trust-bundle.json" in workflow
    assert "atomlearn-trust-bundle.json" in workflow
    assert "gh release create" in workflow
    assert "branches:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "ref: ${{ env.RELEASE_TAG }}" in workflow
    assert 'git tag --points-at HEAD --list "$RELEASE_TAG"' in workflow
    assert "Release $RELEASE_TAG already exists and cannot be replaced" in workflow
    assert "find runtime-dist -type f -name '*.zip' -print0" in workflow
    assert "Expected exactly eight runtime bundles" in workflow
    assert "find runtime-dist -maxdepth 1" not in workflow
