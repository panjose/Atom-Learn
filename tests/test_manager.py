from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
import warnings
import zipfile
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
MANAGER_PACKAGE = ROOT / "manager"
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "self_evolution_v2" / "user-profile.json"
sys.path.insert(0, str(MANAGER_PACKAGE))

from atomlearn_manager.runtime import build_runtime_bundle, platform_identity
from atomlearn_manager.manifest import key_fingerprint


def test_release_capability_smoke_exercises_real_document_rag_exam_and_research_paths(tmp_path: Path) -> None:
    from atomlearn_manager.common import sha256_bytes
    from atomlearn_manager.manager import _capability_smoke

    version = "0.13.0"
    fixture_source = ROOT / "atom-learn" / "assets" / "smoke-fixtures.json"
    fixture_target = tmp_path / "releases" / version / "atom-learn" / "assets" / "smoke-fixtures.json"
    fixture_target.parent.mkdir(parents=True)
    shutil.copy2(fixture_source, fixture_target)
    transaction_root = tmp_path / "transaction"
    transaction_root.mkdir()
    manifest = {
        "capabilities": {"required_smoke": ["core", "bridge", "documents", "rag", "exam", "research"]},
        "smoke_fixture_sha256": sha256_bytes(fixture_source.read_bytes()),
    }
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    _capability_smoke(
        tmp_path,
        version,
        manifest,
        [sys.executable, str(ROOT / "atom-learn" / "scripts" / "atomlearn.py")],
        transaction_root,
        environment,
    )
    workspace = transaction_root / "capability-smoke" / "workspace"
    assert (workspace / ".atomlearn" / "rag" / "state.yaml").is_file()
    assert (workspace / ".atomlearn" / "exam" / "state.yaml").is_file()
    assert (workspace / ".atomlearn" / "research" / "state.yaml").is_file()


def environment(*, fail_after: str | None = None, fake_free: int | None = None) -> dict[str, str]:
    value = os.environ.copy()
    value["PYTHONUTF8"] = "1"
    value["PYTHONPATH"] = str(MANAGER_PACKAGE)
    if fail_after:
        value["ATOMLEARN_MANAGER_FAIL_AFTER"] = fail_after
    if fake_free is not None:
        value["ATOMLEARN_MANAGER_FAKE_FREE_BYTES"] = str(fake_free)
    return value


def manager(root: Path, *args: object, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "atomlearn_manager.cli", "--manager-root", str(root.resolve()), *(str(arg) for arg in args)],
        cwd=ROOT,
        env=env or environment(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def release(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "atomlearn_manager.builder", *(str(arg) for arg in args)],
        cwd=ROOT,
        env=environment(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"release command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def launch(root: Path, *args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "atomlearn_manager.launcher", "--manager-root", str(root.resolve()), *(str(arg) for arg in args)],
        cwd=ROOT,
        env=environment(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"launcher failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def parsed(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def signing_material(tmp_path: Path) -> tuple[Path, str]:
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "release-private.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private_path, base64.b64encode(public).decode("ascii")


def gate_report(path: Path, version: str, commit: str) -> Path:
    value = {
        "kind": "atomlearn.release-gate-report",
        "schema_version": 1,
        "tag": f"v{version}",
        "commit_sha": commit,
        "python": {
            "linux": ["3.10", "3.11", "3.12", "3.13"],
            "windows": ["3.10", "3.11", "3.12", "3.13"],
        },
        "gates": {
            "full_tests": True,
            "skill_validator": True,
            "migration_fixtures": True,
            "manager_upgrade_tests": True,
            "security_archive_tests": True,
            "property_tests": True,
            "fault_injection": True,
            "privacy_attacks": True,
            "replay_compatibility": True,
        },
    }
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return path


def trust_bundle_for_private(tmp_path: Path, private_path: Path) -> Path:
    private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    assert isinstance(private, Ed25519PrivateKey)
    public_key = base64.b64encode(
        private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")
    path = tmp_path / f"trust-{uuid.uuid4().hex}.json"
    path.write_text(
        json.dumps(
            {
                "kind": "atomlearn.trust-bundle",
                "schema_version": 1,
                "bundle_version": 1,
                "previous_bundle_version": None,
                "repository": "panjose/Atom-Learn",
                "keys": [
                    {
                        "key_id": "test-release",
                        "algorithm": "ed25519",
                        "public_key": public_key,
                        "fingerprint": key_fingerprint(public_key),
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_release_builder_rejects_a_private_key_missing_from_the_trust_bundle(tmp_path: Path) -> None:
    private_key, _ = signing_material(tmp_path)
    wrong_root = tmp_path / "wrong-key"
    wrong_root.mkdir()
    wrong_private, _ = signing_material(wrong_root)
    source = synthetic_source(tmp_path, "0.13.0")
    manager_wheel = tmp_path / "atomlearn_manager-0.2.0-py3-none-any.whl"
    manager_wheel.write_bytes(b"synthetic manager")
    commit = "e" * 40
    report = gate_report(tmp_path / "gate.json", "0.13.0", commit)
    result = release(
        source,
        "--output-dir", tmp_path / "release-output",
        "--tag", "v0.13.0",
        "--commit-sha", commit,
        "--artifact-url", "https://github.com/panjose/Atom-Learn/releases/download/v0.13.0/atomlearn-0.13.0.zip",
        "--channel", "stable",
        "--key-id", "test-release",
        "--private-key", private_key,
        "--trust-bundle", trust_bundle_for_private(tmp_path, wrong_private),
        "--gate-report", report,
        "--manager-artifact", manager_wheel,
        "--runtime-bundle", tmp_path / "unused-runtime.zip",
        check=False,
    )
    assert result.returncode == 2
    assert "private key does not match the active trust-bundle identity" in result.stderr


def synthetic_source(
    tmp_path: Path,
    version: str,
    *,
    reported_version: str | None = None,
    schema_version: int = 1,
) -> Path:
    source = tmp_path / f"source-{version}-{uuid.uuid4().hex}"
    scripts = source / "atom-learn" / "scripts"
    assets = source / "atom-learn" / "assets"
    agents = source / "atom-learn" / "agents"
    scripts.mkdir(parents=True)
    assets.mkdir(parents=True)
    agents.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        f'[project]\nname = "atom-learn"\nversion = "{version}"\nrequires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    (source / "README.md").write_text("# AtomLearn\n", encoding="utf-8")
    (source / "README.zh-CN.md").write_text("# AtomLearn 中文\n", encoding="utf-8")
    (source / "atom-learn" / "SKILL.md").write_text(
        "---\nname: atom-learn\ndescription: Synthetic signed manager test Core.\n---\n\n# Test\n",
        encoding="utf-8",
    )
    (agents / "openai.yaml").write_text(
        "interface:\n  display_name: AtomLearn\n  short_description: Synthetic manager test\n"
        "  default_prompt: Use $atom-learn for the signed manager test.\n",
        encoding="utf-8",
    )
    core = {
        "kind": "atomlearn.core-manifest",
        "manifest_version": 1,
        "skill_name": "atom-learn",
        "core_version": version,
        "skill_protocol_version": 1,
        "capability_ledger": "atom-learn/assets/capabilities.yaml",
        "smoke_fixtures": "atom-learn/assets/smoke-fixtures.json",
        "release_channel": "development",
        "feature_defaults": {
            "global_personalization": False,
            "strategy_experiments": False,
            "capsule_export": False,
            "release_manager": False,
        },
        "schemas": {
            "workspace_core": {"read": [schema_version], "write": schema_version},
            "workspace_adaptation": {"read": [schema_version], "write": schema_version},
            "workspace_evolution": {"read": [schema_version], "write": schema_version},
            "workspace_intake": {"read": [schema_version], "write": schema_version},
            "workspace_rag": {"read": [schema_version], "write": schema_version},
            "workspace_research": {"read": [schema_version], "write": schema_version},
            "workspace_exam": {"read": [schema_version], "write": schema_version},
            "workspace_lineage": {"read": [schema_version], "write": schema_version},
            "workspace_profile_binding": {"read": [schema_version], "write": schema_version},
            "user_profile": {"read": [schema_version], "write": schema_version},
            "user_strategy": {"read": [schema_version], "write": schema_version},
            "evolution_capsule": {"read": [schema_version], "write": schema_version},
        },
        "artifact_sha256": "sha256:" + "0" * 64,
    }
    (assets / "core-manifest.yaml").write_text(
        yaml.safe_dump(core, sort_keys=False), encoding="utf-8"
    )
    (assets / "capabilities.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "atomlearn.capability-ledger",
                "schema_version": 1,
                "core_version": version,
                "required_smoke": ["core", "bridge"],
                "capabilities": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (assets / "smoke-fixtures.json").write_text(
        json.dumps({"kind": "atomlearn.smoke-fixtures", "schema_version": 1}), encoding="utf-8"
    )
    reported = reported_version or version
    (scripts / "atomlearn.py").write_text(
        "import json, sys\n"
        f"VERSION={reported!r}\n"
        "args=sys.argv[1:]\n"
        "if args == ['version']:\n"
        " print(json.dumps({'ok': True, 'core_version': VERSION}))\n"
        "elif not args or args == ['--help']:\n"
        " print('synthetic AtomLearn help')\n"
        "elif len(args) >= 2 and args[:2] == ['migrate', 'validate']:\n"
        " print(json.dumps({'ok': True, 'core_version': VERSION}))\n"
        "elif args and args[0] in {'validate', 'status'}:\n"
        " print(json.dumps({'ok': True, 'core_version': VERSION}))\n"
        "else:\n"
        " print('unsupported', file=sys.stderr); raise SystemExit(2)\n",
        encoding="utf-8",
    )
    return source


def synthetic_core_wheel(tmp_path: Path, source: Path, version: str) -> Path:
    wheel_dir = tmp_path / f"wheels-{version}-{uuid.uuid4().hex}"
    wheel_dir.mkdir(parents=True)
    wheel = wheel_dir / f"atom_learn-{version}-py3-none-any.whl"
    module = (source / "atom-learn" / "scripts" / "atomlearn.py").read_bytes()
    dist_info = f"atom_learn-{version}.dist-info"
    with zipfile.ZipFile(wheel, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("atomlearn.py", module)
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: atom-learn\nVersion: {version}\n",
        )
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: AtomLearn tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel_dir


def runtime_matrix(tmp_path: Path, source: Path, version: str) -> list[Path]:
    wheel_dir = synthetic_core_wheel(tmp_path, source, version)
    output_dir = tmp_path / f"runtime-bundles-{version}-{uuid.uuid4().hex}"
    paths = []
    for system in ["linux", "windows"]:
        for python_minor in ["3.10", "3.11", "3.12", "3.13"]:
            built = build_runtime_bundle(
                wheel_dir,
                output_dir,
                core_version=version,
                system=system,
                architecture="amd64",
                python_minor=python_minor,
            )
            paths.append(Path(built["path"]))
    return paths


def build(
    tmp_path: Path,
    source: Path,
    version: str,
    private_key: Path,
    *,
    commit_char: str,
) -> dict:
    commit = commit_char * 40
    output_dir = tmp_path / f"release-{version}-{uuid.uuid4().hex}"
    manager_wheel = tmp_path / f"manager-wheel-{uuid.uuid4().hex}" / "atomlearn_manager-0.2.0-py3-none-any.whl"
    manager_wheel.parent.mkdir(parents=True)
    manager_wheel.write_bytes(b"synthetic signed manager wheel")
    report = gate_report(tmp_path / f"gate-{version}-{uuid.uuid4().hex}.json", version, commit)
    trust_bundle = trust_bundle_for_private(tmp_path, private_key)
    runtimes = runtime_matrix(tmp_path, source, version)
    runtime_args = [item for runtime_path in runtimes for item in ["--runtime-bundle", runtime_path]]
    result = release(
        source,
        "--output-dir",
        output_dir,
        "--tag",
        f"v{version}",
        "--commit-sha",
        commit,
        "--artifact-url",
        f"https://github.com/panjose/Atom-Learn/releases/download/v{version}/atomlearn-{version}.zip",
        "--channel",
        "stable",
        "--key-id",
        "test-release",
        "--private-key",
        private_key,
        "--gate-report",
        report,
        "--manager-artifact",
        manager_wheel,
        "--trust-bundle",
        trust_bundle,
        *runtime_args,
    )
    built = parsed(result)
    built["gate_report"] = str(report)
    return built


def init_manager(tmp_path: Path, public_key: str) -> Path:
    root = (tmp_path / "manager-root").resolve()
    parsed(manager(root, "init", "--key-id", "test-release", "--public-key", public_key))
    return root


def apply(root: Path, release_info: dict, version: str, *extra: object, check: bool = True, env=None):
    identity = "windows" if os.name == "nt" else "linux"
    python_minor = f"py{sys.version_info.major}{sys.version_info.minor}"
    runtime_bundle = next(
        path for path in release_info["runtime_bundle_paths"]
        if f"-{identity}-amd64-{python_minor}.zip" in path
    )
    return manager(
        root,
        "update",
        "apply",
        version,
        "--manifest",
        release_info["manifest"],
        "--artifact",
        release_info["artifact"],
        "--runtime-bundle",
        runtime_bundle,
        "--confirmed",
        *extra,
        check=check,
        env=env,
    )


def test_signed_side_by_side_upgrade_and_paired_rollback(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    manager_root = init_manager(tmp_path, public_key)
    old = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="a")
    current = build(tmp_path, synthetic_source(tmp_path, "0.13.0"), "0.13.0", private_key, commit_char="b")
    current_manifest = json.loads(Path(current["manifest"]).read_text(encoding="utf-8"))
    assert current_manifest["manager_artifact"]["version"] == "0.2.0"
    assert current_manifest["manager_artifact"]["sha256"] == current["manager_artifact_sha256"]
    with zipfile.ZipFile(current["artifact"]) as archive:
        embedded_gate = archive.read("atomlearn-0.13.0/release/gate-report.json")
    assert embedded_gate == Path(current["gate_report"]).read_bytes()

    check = parsed(manager(manager_root, "update", "check", "--manifest", old["manifest"]))
    assert check["available"] is True
    plan = parsed(
        manager(
            manager_root,
            "update",
            "plan",
            "0.12.0",
            "--manifest",
            old["manifest"],
            "--artifact",
            old["artifact"],
        )
    )
    assert plan["artifact_verified"] is True
    assert plan["disk"]["sufficient"] is True
    assert parsed(apply(manager_root, old, "0.12.0"))["active"]["current_version"] == "0.12.0"

    data_root = (tmp_path / "user-data").resolve()
    profile_path = data_root / "profiles" / "default" / "state.yaml"
    profile_path.parent.mkdir(parents=True)
    profile = json.loads(PROFILE_FIXTURE.read_text(encoding="utf-8"))
    for key in ["created_by_core_version", "last_written_by_core_version", "min_reader_core_version"]:
        profile[key] = "0.12.0"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    before = profile_path.read_bytes()
    workspace = (tmp_path / "course").resolve()
    created = subprocess.run(
        [
            sys.executable,
            str(ROOT / "atom-learn" / "scripts" / "atomlearn.py"),
            "init",
            str(workspace),
            "--course-id",
            "manager.course",
            "--title",
            "Manager course",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1"},
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    upgraded = parsed(
        apply(manager_root, current, "0.13.0", "--data-dir", data_root, "--workspace", workspace)
    )
    assert upgraded["active"]["previous_version"] == "0.12.0"
    status = parsed(manager(manager_root, "update", "status"))
    assert status["active_valid"] is True
    assert status["installed_versions"] == ["0.12.0", "0.13.0"]
    system, architecture, python_minor = platform_identity()
    expected_runtime_ids = [
        next(
            item["id"]
            for item in json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))["runtime_bundles"]
            if (item["platform"], item["architecture"], item["python_minor"])
            == (system, architecture, python_minor)
        )
        for info in [old, current]
    ]
    assert status["installed_runtimes"] == sorted(expected_runtime_ids)
    resolved = parsed(manager(manager_root, "codex", "resolve", "--json"))
    assert resolved["core_version"] == "0.13.0"
    assert resolved["skill_protocol_version"] == 1
    assert resolved["skill_sha256"] == current_manifest["skill_protocol"]["entrypoint_sha256"]
    assert Path(resolved["skill_path"]).is_file()
    assert parsed(launch(manager_root, "version"))["core_version"] == "0.13.0"
    assert profile_path.read_bytes() == before
    active_release = manager_root / "releases" / "0.13.0" / "atom-learn" / "scripts" / "atomlearn.py"
    assert active_release.is_file()
    assert not (active_release.stat().st_mode & stat.S_IWUSR)

    rolled = parsed(manager(manager_root, "rollback", "0.12.0", "--confirmed"))
    assert rolled["active"]["current_version"] == "0.12.0"
    assert parsed(launch(manager_root, "version"))["core_version"] == "0.12.0"
    assert profile_path.read_bytes() == before
    reused = parsed(
        apply(manager_root, current, "0.13.0", "--data-dir", data_root, "--workspace", workspace)
    )
    assert reused["active"]["current_version"] == "0.13.0"
    runtime_root = manager_root / "runtimes" / reused["active"]["runtime_id"]
    runtime_module = next(
        path for path in runtime_root.rglob("atomlearn.py") if "site-packages" in path.as_posix()
    )
    runtime_module.chmod(runtime_module.stat().st_mode | stat.S_IWRITE)
    with runtime_module.open("ab") as handle:
        handle.write(b"\n# tampered after activation\n")
    tampered_runtime = manager(manager_root, "update", "status", check=False)
    assert tampered_runtime.returncode == 2
    assert "Installed runtime state does not match the signed manifest" in tampered_runtime.stderr


def test_signature_hash_channel_and_hostile_archives_fail_closed(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    root = init_manager(tmp_path, public_key)
    info = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="c")
    manifest = json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))
    manifest["source"]["commit_sha"] = "d" * 40
    tampered = tmp_path / "tampered-manifest.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    result = manager(root, "update", "check", "--manifest", tampered, check=False)
    assert result.returncode == 2
    assert "signature verification failed" in result.stderr

    mutable = json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))
    mutable["source"]["artifact_url"] = "https://github.com/panjose/Atom-Learn/archive/refs/heads/main.zip"
    mutable_path = tmp_path / "mutable-main-manifest.json"
    mutable_path.write_text(json.dumps(mutable), encoding="utf-8")
    result = manager(root, "update", "check", "--manifest", mutable_path, check=False)
    assert result.returncode == 2
    assert "immutable tagged GitHub release asset" in result.stderr

    truncated = tmp_path / "atomlearn-0.12.0.zip"
    truncated.write_bytes(Path(info["artifact"]).read_bytes()[:100])
    result = manager(
        root,
        "update",
        "plan",
        "0.12.0",
        "--manifest",
        info["manifest"],
        "--artifact",
        truncated,
        check=False,
    )
    assert result.returncode == 2
    assert parsed(manager(root, "update", "status"))["active"] is None

    sys.path.insert(0, str(MANAGER_PACKAGE))
    from atomlearn_manager.common import ManagerError
    from atomlearn_manager.verify import inspect_archive

    attacks = [
        ("traversal.zip", [("atomlearn-1.0.0/../escape", b"x", 0)]),
        ("absolute.zip", [("C:/escape", b"x", 0)]),
        ("link.zip", [("atomlearn-1.0.0/link", b"target", (stat.S_IFLNK | 0o777) << 16)]),
        (
            "duplicate.zip",
            [
                ("atomlearn-1.0.0/duplicate", b"first", 0),
                ("atomlearn-1.0.0/duplicate", b"second", 0),
            ],
        ),
        (
            "case-collision.zip",
            [
                ("atomlearn-1.0.0/Name", b"first", 0),
                ("atomlearn-1.0.0/name", b"second", 0),
            ],
        ),
    ]
    for name, entries in attacks:
        path = tmp_path / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as archive:
                for member, content, attributes in entries:
                    item = zipfile.ZipInfo(member)
                    item.create_system = 3
                    item.external_attr = attributes
                    archive.writestr(item, content)
        with pytest.raises(ManagerError):
            inspect_archive(path, "1.0.0")


def test_health_failure_and_interrupted_activation_recover_old_core(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    root = init_manager(tmp_path, public_key)
    old = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="d")
    bad = build(
        tmp_path,
        synthetic_source(tmp_path, "0.13.0", reported_version="9.9.9"),
        "0.13.0",
        private_key,
        commit_char="e",
    )
    next_release = build(tmp_path, synthetic_source(tmp_path, "0.14.0"), "0.14.0", private_key, commit_char="f")
    parsed(apply(root, old, "0.12.0"))
    failed = apply(root, bad, "0.13.0", check=False)
    assert failed.returncode == 2
    assert parsed(manager(root, "update", "status"))["active"]["current_version"] == "0.12.0"

    interrupted = apply(root, next_release, "0.14.0", check=False, env=environment(fail_after="activated"))
    assert interrupted.returncode == 2
    status = parsed(manager(root, "update", "status"))
    assert status["recovery_required"] is True
    assert status["active"]["current_version"] == "0.14.0"
    recovered = parsed(manager(root, "update", "recover"))
    assert recovered["recovered"] is True
    assert recovered["active"]["current_version"] == "0.12.0"
    assert parsed(manager(root, "version"))["active_core_version"] == "0.12.0"


def test_missing_migration_and_disk_failure_do_not_install_or_switch(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    root = init_manager(tmp_path, public_key)
    old = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="1")
    future = build(
        tmp_path,
        synthetic_source(tmp_path, "0.13.0", schema_version=2),
        "0.13.0",
        private_key,
        commit_char="2",
    )
    parsed(apply(root, old, "0.12.0"))
    data_root = (tmp_path / "state").resolve()
    profile = data_root / "profiles" / "default" / "state.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "revision": 1,
                "min_reader_core_version": "0.12.0",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    plan = parsed(
        manager(
            root,
            "update",
            "plan",
            "0.13.0",
            "--manifest",
            future["manifest"],
            "--artifact",
            future["artifact"],
            "--data-dir",
            data_root,
        )
    )
    assert plan["state"]["counts"]["needs_review"] == 1
    blocked = apply(root, future, "0.13.0", "--data-dir", data_root, check=False)
    assert blocked.returncode == 2
    assert parsed(manager(root, "update", "status"))["active"]["current_version"] == "0.12.0"

    disk = apply(root, future, "0.13.0", check=False, env=environment(fake_free=1))
    assert disk.returncode == 2
    assert "Insufficient disk space" in disk.stderr
    assert parsed(manager(root, "update", "status"))["active"]["current_version"] == "0.12.0"


def test_every_update_persistence_stage_recovers_to_the_old_core(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    old = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="3")
    target = build(tmp_path, synthetic_source(tmp_path, "0.13.0"), "0.13.0", private_key, commit_char="4")
    stages = [
        "planned",
        "downloaded",
        "verified",
        "state_copied",
        "installed",
        "runtime_installed",
        "health_checked",
        "state_applied",
        "activated",
    ]
    manager_source = (MANAGER_PACKAGE / "atomlearn_manager" / "manager.py").read_text(encoding="utf-8")
    assert all(f'_maybe_interrupt("{stage}")' in manager_source for stage in stages)
    for stage in stages:
        stage_root = init_manager(tmp_path / stage, public_key)
        parsed(apply(stage_root, old, "0.12.0"))
        interrupted = apply(stage_root, target, "0.13.0", check=False, env=environment(fail_after=stage))
        assert interrupted.returncode == 2
        status = parsed(manager(stage_root, "update", "status"))
        assert status["recovery_required"] is True
        assert status["active"]["current_version"] == ("0.13.0" if stage == "activated" else "0.12.0")
        recovered = parsed(manager(stage_root, "update", "recover"))
        assert recovered["recovered"] is True
        assert recovered["active"]["current_version"] == "0.12.0"
        assert parsed(launch(stage_root, "version"))["core_version"] == "0.12.0"


def test_two_supported_core_upgrade_paths_reach_the_current_signed_core(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    catalog = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "migrations" / "supported-upgrade-paths.yaml").read_text(encoding="utf-8")
    )
    target_version = catalog["target_core_version"]
    target = build(tmp_path, synthetic_source(tmp_path, target_version), target_version, private_key, commit_char="7")
    assert catalog["schema_edges"] == []
    assert "schema version 1" in catalog["schema_edge_reason"]
    for index, path in enumerate(catalog["upgrade_paths"], start=1):
        old_version = path["from_core_version"]
        old = build(
            tmp_path,
            synthetic_source(tmp_path, old_version),
            old_version,
            private_key,
            commit_char=str(index + 7),
        )
        root = init_manager(tmp_path / f"upgrade-{old_version}", public_key)
        parsed(apply(root, old, old_version))
        upgraded = parsed(apply(root, target, target_version))
        assert upgraded["active"]["previous_version"] == old_version
        assert parsed(launch(root, "version"))["core_version"] == target_version


def test_trusted_migration_failure_never_mutates_live_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(MANAGER_PACKAGE))
    from atomlearn_manager import statecopy

    data_root = (tmp_path / "data").resolve()
    profile = data_root / "profiles" / "default" / "state.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump({"schema_version": 1, "revision": 2, "value": "unchanged"}, sort_keys=False),
        encoding="utf-8",
    )
    before = profile.read_bytes()

    def fail_migration(value: dict) -> dict:
        raise RuntimeError("injected trusted migration failure")

    monkeypatch.setitem(statecopy.MIGRATIONS, ("user_profile", 1), fail_migration)
    manifest = {
        "version": "0.13.0",
        "schemas": {"user_profile": {"read": [2], "write": 2}},
    }
    with pytest.raises(RuntimeError, match="injected trusted migration failure"):
        statecopy.snapshot_and_migrate(tmp_path / "transaction", data_root, [], manifest)
    assert profile.read_bytes() == before
