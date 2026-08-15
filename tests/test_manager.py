from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
MANAGER_PACKAGE = ROOT / "manager"
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "self_evolution_v2" / "user-profile.json"


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
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


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
    scripts.mkdir(parents=True)
    assets.mkdir(parents=True)
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
    core = {
        "kind": "atomlearn.core-manifest",
        "manifest_version": 1,
        "skill_name": "atom-learn",
        "core_version": version,
        "release_channel": "development",
        "schemas": {
            "workspace_core": {"read": [schema_version], "write": schema_version},
            "user_profile": {"read": [schema_version], "write": schema_version},
            "user_strategy": {"read": [schema_version], "write": schema_version},
        },
        "artifact_sha256": "sha256:" + "0" * 64,
    }
    (assets / "core-manifest.yaml").write_text(
        yaml.safe_dump(core, sort_keys=False), encoding="utf-8"
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
        gate_report(tmp_path / f"gate-{version}-{uuid.uuid4().hex}.json", version, commit),
    )
    return parsed(result)


def init_manager(tmp_path: Path, public_key: str) -> Path:
    root = (tmp_path / "manager-root").resolve()
    parsed(manager(root, "init", "--key-id", "test-release", "--public-key", public_key))
    return root


def apply(root: Path, release_info: dict, version: str, *extra: object, check: bool = True, env=None):
    return manager(
        root,
        "update",
        "apply",
        version,
        "--manifest",
        release_info["manifest"],
        "--artifact",
        release_info["artifact"],
        "--confirmed",
        *extra,
        check=check,
        env=env,
    )


def test_signed_side_by_side_upgrade_and_paired_rollback(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    manager_root = init_manager(tmp_path, public_key)
    old = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="a")
    current = build(tmp_path, ROOT, "0.13.0", private_key, commit_char="b")

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


def test_signature_hash_channel_and_hostile_archives_fail_closed(tmp_path: Path) -> None:
    private_key, public_key = signing_material(tmp_path)
    root = init_manager(tmp_path, public_key)
    info = build(tmp_path, synthetic_source(tmp_path, "0.12.0"), "0.12.0", private_key, commit_char="c")
    manifest = json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))
    manifest["version"] = "0.12.1"
    manifest["tag"] = "v0.12.1"
    manifest["artifact"]["filename"] = "atomlearn-0.12.1.zip"
    manifest["source"]["artifact_url"] = (
        "https://github.com/panjose/Atom-Learn/releases/download/v0.12.1/atomlearn-0.12.1.zip"
    )
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
