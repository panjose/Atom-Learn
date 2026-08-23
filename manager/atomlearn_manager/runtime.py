"""Build, verify, select, and install signed immutable runtime profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import shutil
import stat
import subprocess
import sys
import uuid
import venv
import zipfile
from pathlib import Path
from typing import Any

from .common import (
    ManagerError,
    atomic_bytes,
    canonical_json,
    is_reparse_or_symlink,
    read_mapping,
    require_schema,
    sha256_bytes,
    sha256_file,
)
from .verify import MAX_FILES, MAX_RATIO, MAX_UNCOMPRESSED, _safe_member


RECIPE_NAME = "runtime-recipe.json"
SMOKE_REPORT_NAME = "runtime-smoke-report.json"


PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "base": {
        "capabilities": ["base"],
        "stability": "stable",
        "python_modules": [],
        "native_requirements": [],
        "model_policy": {"mode": "none", "silent_download": False},
        "smoke_suites": ["base"],
    },
    "scale": {
        "capabilities": ["base", "scale"],
        "stability": "stable",
        "python_modules": ["usearch"],
        "native_requirements": [],
        "model_policy": {"mode": "none", "silent_download": False},
        "smoke_suites": ["base", "scale-index"],
    },
    "semantic-cpu": {
        "capabilities": ["base", "semantic-cpu"],
        "stability": "stable",
        "python_modules": ["sentence_transformers"],
        "native_requirements": [],
        "model_policy": {
            "mode": "pinned_local",
            "silent_download": False,
            "trust_remote_code": False,
            "allowed_weight_formats": ["safetensors"],
            "forbidden_formats": ["bin", "pkl", "pickle", "pt"],
        },
        "smoke_suites": ["base", "semantic-retrieval"],
    },
    "ocr": {
        "capabilities": ["base", "ocr"],
        "stability": "stable",
        "python_modules": ["pypdfium2", "pytesseract", "PIL"],
        "native_requirements": [
            {"id": "tesseract", "command": "tesseract", "version_args": ["--version"]}
        ],
        "model_policy": {"mode": "none", "silent_download": False},
        "smoke_suites": ["base", "ocr-layout"],
    },
    "semantic-gpu": {
        "capabilities": ["base", "semantic-gpu"],
        "stability": "experimental",
        "python_modules": ["sentence_transformers", "torch"],
        "native_requirements": [],
        "model_policy": {
            "mode": "pinned_local",
            "silent_download": False,
            "trust_remote_code": False,
            "allowed_weight_formats": ["safetensors"],
            "forbidden_formats": ["bin", "pkl", "pickle", "pt"],
        },
        "smoke_suites": ["base", "semantic-gpu"],
    },
}


def platform_identity() -> tuple[str, str, str]:
    system = "windows" if os.name == "nt" else "linux"
    machine = host_platform.machine().lower()
    architecture = "amd64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else machine
    if architecture not in {"amd64", "arm64"}:
        raise ManagerError(f"Unsupported runtime architecture: {machine}", code="runtime_platform_unsupported")
    return system, architecture, f"{sys.version_info.major}.{sys.version_info.minor}"


def _dependency_lock(records: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json({"wheels": records}))


def _profile_hash(
    profile: dict[str, Any],
    dependency_lock_sha256: str,
    model_lock: dict[str, Any] | None,
    smoke_report_sha256: str,
) -> str:
    return sha256_bytes(
        canonical_json(
            {
                "profile": profile,
                "dependency_lock_sha256": dependency_lock_sha256,
                "model_lock": model_lock,
                "smoke_report_sha256": smoke_report_sha256,
            }
        )
    )


def runtime_id(
    version: str,
    system: str,
    architecture: str,
    python_minor: str,
    profile: str | None = None,
    profile_hash: str | None = None,
) -> str:
    legacy = f"{version}-{system}-{architecture}-py{python_minor.replace('.', '')}"
    if profile is None and profile_hash is None:
        return legacy
    if not profile or not profile_hash or not profile_hash.startswith("sha256:"):
        raise ManagerError("Runtime profile ID requires a name and sha256 profile hash")
    return f"{legacy}-{profile}-{profile_hash.removeprefix('sha256:')[:12]}"


def runtime_platform_key(expected: dict[str, Any]) -> str:
    return f"{expected['platform']}-{expected['architecture']}-py{expected['python_minor'].replace('.', '')}"


def runtime_path(root: Path, expected: dict[str, Any]) -> Path:
    profile_hash = expected.get("profile_hash")
    if not isinstance(profile_hash, str):
        return root / "runtimes" / expected["id"]
    core_version = expected.get("core_version")
    if not isinstance(core_version, str):
        core_version = expected["id"].split("-", 1)[0]
    return (
        root
        / "runtimes"
        / core_version
        / runtime_platform_key(expected)
        / profile_hash.removeprefix("sha256:")[:16]
    )


def _default_smoke_report(profile_name: str, suites: list[str]) -> dict[str, Any]:
    return {
        "kind": "atomlearn.runtime-profile-smoke",
        "schema_version": 1,
        "profile": profile_name,
        "status": "pending",
        "suites": suites,
        "platform_verified": False,
    }


def _wheel_record(path: Path) -> dict[str, Any]:
    return {"filename": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}


def build_runtime_bundle(
    wheel_dir: Path,
    output_dir: Path,
    *,
    core_version: str,
    system: str,
    architecture: str,
    python_minor: str,
    profile_name: str = "base",
    model_lock: dict[str, Any] | None = None,
    smoke_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wheel_dir = wheel_dir.resolve()
    output_dir = output_dir.resolve(strict=False)
    wheels = sorted(wheel_dir.glob("*.whl"), key=lambda path: path.name.casefold())
    if not wheels or any(is_reparse_or_symlink(path) for path in wheels):
        raise ManagerError("Runtime wheel directory must contain regular wheel files")
    core_prefixes = {f"atom_learn-{core_version}-", f"atom-learn-{core_version}-"}
    core_candidates = [path for path in wheels if any(path.name.startswith(prefix) for prefix in core_prefixes)]
    if len(core_candidates) != 1:
        raise ManagerError("Runtime bundle requires exactly one AtomLearn Core wheel matching the release version")
    records = [_wheel_record(path) for path in wheels]
    if profile_name not in PROFILE_SPECS:
        raise ManagerError(f"Unknown runtime profile: {profile_name}")
    profile = {"name": profile_name, **PROFILE_SPECS[profile_name]}
    if profile["model_policy"]["mode"] == "pinned_local" and model_lock is None:
        raise ManagerError(f"Runtime profile {profile_name} requires an explicit signed model lock")
    if model_lock is not None:
        if profile["model_policy"]["mode"] != "pinned_local":
            raise ManagerError(f"Runtime profile {profile_name} does not accept a model lock")
        if model_lock.get("trust_remote_code") is not False:
            raise ManagerError("Signed model locks must explicitly disable trust_remote_code")
        if str(model_lock.get("revision", "")).lower() in {"", "main", "master", "latest", "head"}:
            raise ManagerError("Signed model lock revision must be immutable rather than a floating ref")
        forbidden = set(profile["model_policy"]["forbidden_formats"])
        files = model_lock.get("files")
        if not isinstance(files, list) or not files:
            raise ManagerError("Signed model lock must contain at least one hashed file")
        for item in files:
            relative = Path(str(item.get("path", "")))
            suffix = relative.suffix.lower().removeprefix(".")
            if relative.is_absolute() or ".." in relative.parts:
                raise ManagerError("Signed model lock file paths must stay inside the model directory")
            if suffix in forbidden or suffix in {"py", "pyc", "pyd", "so", "dll", "dylib", "exe"}:
                raise ManagerError(f"Unsafe model weight format is forbidden: {suffix}")
    smoke = smoke_report or _default_smoke_report(profile_name, profile["smoke_suites"])
    require_schema(smoke, "runtime-profile-smoke")
    if smoke.get("profile") != profile_name or smoke.get("suites") != profile["smoke_suites"]:
        raise ManagerError("Runtime smoke report does not match the selected profile contract")
    if smoke.get("status") == "passed" and (
        smoke.get("platform"), smoke.get("architecture"), smoke.get("python_minor")
    ) != (system, architecture, python_minor):
        raise ManagerError("Passing runtime smoke report does not match the target platform and Python ABI")
    smoke_bytes = canonical_json(smoke)
    smoke_hash = sha256_bytes(smoke_bytes)
    dependency_lock = _dependency_lock(records)
    profile_hash = _profile_hash(profile, dependency_lock, model_lock, smoke_hash)
    identifier = runtime_id(core_version, system, architecture, python_minor, profile_name, profile_hash)
    recipe = {
        "kind": "atomlearn.runtime-bundle",
        "schema_version": 2,
        "id": identifier,
        "core_version": core_version,
        "platform": system,
        "architecture": architecture,
        "python_minor": python_minor,
        "profile": profile,
        "profile_hash": profile_hash,
        "dependency_lock_sha256": dependency_lock,
        "model_lock": model_lock,
        "smoke_report_sha256": smoke_hash,
        "core_wheel": _wheel_record(core_candidates[0]),
        "wheels": records,
    }
    require_schema(recipe, "runtime-bundle")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"atomlearn-runtime-{identifier}.zip"
    output = output_dir / filename
    if output.exists():
        raise ManagerError(f"Runtime bundle output already exists: {output}")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        entries = [
            (RECIPE_NAME, canonical_json(recipe)),
            (SMOKE_REPORT_NAME, smoke_bytes),
            *[(f"wheels/{path.name}", path.read_bytes()) for path in wheels],
        ]
        for relative, content in entries:
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100444 & 0xFFFF) << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return {
        "id": identifier,
        "path": str(output),
        "filename": filename,
        "sha256": sha256_file(output),
        "size": output.stat().st_size,
        "recipe_sha256": sha256_bytes(canonical_json(recipe)),
        "recipe": recipe,
    }


def inspect_runtime_bundle(path: Path, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file() or is_reparse_or_symlink(path):
        raise ManagerError(f"Runtime bundle must be a regular non-link file: {path}")
    if expected:
        if path.name != expected["filename"] or path.stat().st_size != expected["size"] or sha256_file(path) != expected["sha256"]:
            raise ManagerError("Runtime bundle filename size or hash does not match the signed manifest")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ManagerError("Runtime bundle is not a valid ZIP") from exc
    files: dict[str, bytes] = {}
    total = 0
    seen: set[str] = set()
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ManagerError("Runtime bundle file count is empty or excessive")
        for info in infos:
            name, is_dir = _safe_member(info)
            if is_dir:
                continue
            folded = name.casefold()
            if folded in seen:
                raise ManagerError(f"Runtime bundle has duplicate or case-colliding member: {name}")
            seen.add(folded)
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ManagerError("Runtime bundle exceeds the uncompressed-size safety limit")
            if info.compress_size == 0 and info.file_size > 0 or (
                info.compress_size > 0 and info.file_size / info.compress_size > MAX_RATIO
            ):
                raise ManagerError(f"Suspicious runtime bundle compression ratio: {name}")
            if name not in {RECIPE_NAME, SMOKE_REPORT_NAME} and not name.startswith("wheels/"):
                raise ManagerError(f"Unexpected runtime bundle member: {name}")
            files[name] = archive.read(info)
    if RECIPE_NAME not in files:
        raise ManagerError("Runtime bundle is missing its canonical recipe")
    try:
        recipe = json.loads(files[RECIPE_NAME].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("Runtime recipe is not valid UTF-8 JSON") from exc
    if not isinstance(recipe, dict):
        raise ManagerError("Runtime recipe must be an object")
    require_schema(recipe, "runtime-bundle")
    if recipe["schema_version"] == 2:
        if SMOKE_REPORT_NAME not in files:
            raise ManagerError("Runtime profile bundle is missing its signed smoke report")
        try:
            smoke_report = json.loads(files[SMOKE_REPORT_NAME].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagerError("Runtime profile smoke report is not valid UTF-8 JSON") from exc
        if not isinstance(smoke_report, dict) or files[SMOKE_REPORT_NAME] != canonical_json(smoke_report):
            raise ManagerError("Runtime profile smoke report must use canonical JSON bytes")
        require_schema(smoke_report, "runtime-profile-smoke")
        if sha256_bytes(files[SMOKE_REPORT_NAME]) != recipe["smoke_report_sha256"]:
            raise ManagerError("Runtime profile smoke report hash does not match its recipe")
        dependency_lock = _dependency_lock(recipe["wheels"])
        profile_hash = _profile_hash(
            recipe["profile"], dependency_lock, recipe["model_lock"], recipe["smoke_report_sha256"]
        )
        if dependency_lock != recipe["dependency_lock_sha256"] or profile_hash != recipe["profile_hash"]:
            raise ManagerError("Runtime profile or dependency lock hash is inconsistent")
        canonical_id = runtime_id(
            recipe["core_version"],
            recipe["platform"],
            recipe["architecture"],
            recipe["python_minor"],
            recipe["profile"]["name"],
            recipe["profile_hash"],
        )
    else:
        smoke_report = None
        canonical_id = runtime_id(
            recipe["core_version"], recipe["platform"], recipe["architecture"], recipe["python_minor"]
        )
    if recipe["id"] != canonical_id or path.name != f"atomlearn-runtime-{canonical_id}.zip":
        raise ManagerError("Runtime bundle ID or filename does not match its target coordinates")
    canonical = canonical_json(recipe)
    if files[RECIPE_NAME] != canonical:
        raise ManagerError("Runtime recipe must use canonical JSON bytes")
    records = {item["filename"]: item for item in recipe["wheels"]}
    bundled_names = {name.removeprefix("wheels/") for name in files if name.startswith("wheels/")}
    if set(records) != bundled_names or len(records) != len(recipe["wheels"]):
        raise ManagerError("Runtime recipe and bundled wheel set disagree")
    for filename, record in records.items():
        content = files[f"wheels/{filename}"]
        if len(content) != record["size"] or sha256_bytes(content) != record["sha256"]:
            raise ManagerError(f"Runtime wheel does not match its recipe: {filename}")
    if recipe["core_wheel"] != records.get(recipe["core_wheel"]["filename"]):
        raise ManagerError("Runtime Core wheel is not present exactly in the locked wheel set")
    core_prefixes = (f"atom_learn-{recipe['core_version']}-", f"atom-learn-{recipe['core_version']}-")
    core_candidates = [name for name in records if name.startswith(core_prefixes)]
    if core_candidates != [recipe["core_wheel"]["filename"]]:
        raise ManagerError("Runtime recipe does not identify exactly one matching AtomLearn Core wheel")
    if expected:
        fields = ["id", "platform", "architecture", "python_minor", "core_wheel"]
        if recipe["schema_version"] == 2:
            fields.extend(
                ["profile", "profile_hash", "dependency_lock_sha256", "model_lock", "smoke_report_sha256"]
            )
        for field in fields:
            if recipe[field] != expected[field]:
                raise ManagerError(f"Runtime bundle recipe disagrees with signed manifest field: {field}")
        if sha256_bytes(canonical) != expected["recipe_sha256"]:
            raise ManagerError("Runtime recipe hash does not match the signed manifest")
    return {
        "recipe": recipe,
        "files": files,
        "recipe_sha256": sha256_bytes(canonical),
        "smoke_report": smoke_report,
    }


def select_runtime(manifest: dict[str, Any], profile_name: str = "base") -> dict[str, Any] | None:
    if manifest.get("manifest_version") != 2:
        return None
    system, architecture, python_minor = platform_identity()
    matches = [
        item
        for item in manifest["runtime_bundles"]
        if item["platform"] == system
        and item["architecture"] == architecture
        and item["python_minor"] == python_minor
        and (item.get("profile") or {}).get("name", "base") == profile_name
    ]
    if len(matches) != 1:
        raise ManagerError(
            f"Release has no unique {profile_name} runtime for {system}/{architecture}/Python {python_minor}",
            code="runtime_variant_unavailable",
            details={
                "platform": system,
                "architecture": architecture,
                "python_minor": python_minor,
                "profile": profile_name,
            },
        )
    return matches[0]


def runtime_for_active(manifest: dict[str, Any], active: dict[str, Any]) -> dict[str, Any] | None:
    if manifest.get("manifest_version") != 2:
        return None
    runtime_id_value = active.get("runtime_id")
    matches = [item for item in manifest["runtime_bundles"] if item["id"] == runtime_id_value]
    if len(matches) != 1:
        raise ManagerError("Active pointer runtime is absent or ambiguous in the signed release")
    return matches[0]


def runtime_python(runtime_root: Path) -> Path:
    return runtime_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _runtime_content_hash(runtime_root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        (
            path
            for path in runtime_root.rglob("*")
            if path.relative_to(runtime_root).as_posix() != "atomlearn-runtime.json"
        ),
        key=lambda path: path.relative_to(runtime_root).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(runtime_root)
        if _is_canonical_venv_lib64_alias(path, runtime_root):
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0symlink\0lib\0")
            continue
        if is_reparse_or_symlink(path):
            raise ManagerError(f"Installed runtime contains a link or reparse point: {path}")
        if not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _is_canonical_venv_lib64_alias(path: Path, runtime_root: Path) -> bool:
    """Allow only CPython venv's contained top-level ``lib64 -> lib`` alias."""

    try:
        relative = path.relative_to(runtime_root)
    except ValueError:
        return False
    if relative.as_posix() != "lib64" or not path.is_symlink():
        return False
    try:
        target = os.readlink(path)
    except OSError:
        return False
    if target != "lib":
        return False
    library = runtime_root / "lib"
    return library.is_dir() and not is_reparse_or_symlink(library)


def _mark_runtime_read_only(runtime_root: Path) -> None:
    for path in runtime_root.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)


def _verify_wheelhouse(wheelhouse: Path, recipe: dict[str, Any]) -> None:
    expected = {item["filename"]: item for item in recipe["wheels"]}
    if not wheelhouse.is_dir() or is_reparse_or_symlink(wheelhouse):
        raise ManagerError("Installed runtime wheelhouse is missing or unsafe")
    actual = {path.name: path for path in wheelhouse.iterdir()}
    if set(actual) != set(expected):
        raise ManagerError("Installed runtime wheelhouse does not match its signed recipe")
    for filename, path in actual.items():
        record = expected[filename]
        if (
            not path.is_file()
            or is_reparse_or_symlink(path)
            or path.stat().st_size != record["size"]
            or sha256_file(path) != record["sha256"]
        ):
            raise ManagerError(f"Installed runtime wheelhouse file is invalid: {filename}")


def verify_installed_runtime(runtime_root: Path, expected: dict[str, Any]) -> dict[str, Any]:
    state_path = runtime_root / "atomlearn-runtime.json"
    python = runtime_python(runtime_root)
    if not runtime_root.is_dir() or is_reparse_or_symlink(runtime_root) or not python.is_file() or is_reparse_or_symlink(python):
        raise ManagerError(f"Installed runtime is missing or unsafe: {runtime_root}")
    try:
        raw = state_path.read_bytes()
        state = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError(f"Installed runtime state is unreadable: {runtime_root}") from exc
    if not isinstance(state, dict) or raw != canonical_json(state):
        raise ManagerError("Installed runtime state must be canonical JSON")
    if (
        state.get("id") != expected["id"]
        or state.get("bundle_sha256") != expected["sha256"]
        or state.get("recipe_sha256") != expected["recipe_sha256"]
        or state.get("profile_hash") != expected.get("profile_hash")
        or state.get("dependency_lock_sha256") != expected.get("dependency_lock_sha256")
        or state.get("content_sha256") != _runtime_content_hash(runtime_root)
    ):
        raise ManagerError("Installed runtime state does not match the signed manifest")
    return state


def install_runtime(
    bundle: Path,
    expected: dict[str, Any],
    destination: Path,
    wheelhouse_root: Path | None = None,
) -> dict[str, Any]:
    inspected = inspect_runtime_bundle(bundle, expected)
    if destination.exists():
        return verify_installed_runtime(destination, expected)
    staging_parent = destination.parents[2] if expected.get("profile_hash") else destination.parent
    staging = staging_parent / f".building-{uuid.uuid4().hex[:12]}"
    staging.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    wheelhouse = (wheelhouse_root or destination.parent.parent / "wheelhouses") / expected["id"]
    wheelhouse.parent.mkdir(parents=True, exist_ok=True)
    if wheelhouse.exists():
        _verify_wheelhouse(wheelhouse, inspected["recipe"])
    else:
        wheelhouse_staging = wheelhouse.with_name(f".{wheelhouse.name}.building-{uuid.uuid4().hex[:12]}")
        wheelhouse_staging.mkdir(parents=True, exist_ok=False)
        for relative, content in inspected["files"].items():
            if relative.startswith("wheels/"):
                atomic_bytes(wheelhouse_staging / Path(relative).name, content)
        _verify_wheelhouse(wheelhouse_staging, inspected["recipe"])
        _mark_runtime_read_only(wheelhouse_staging)
        os.replace(wheelhouse_staging, wheelhouse)
    try:
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(staging)
        python = runtime_python(staging)
        locked_wheels = [wheelhouse / item["filename"] for item in inspected["recipe"]["wheels"]]
        command = [
            str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse),
            "--disable-pip-version-check", "--no-deps", *(str(path) for path in locked_wheels),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=300, check=False)
        if result.returncode != 0:
            raise ManagerError(f"Offline signed runtime installation failed: {result.stderr.strip()}")
        inventory_result = subprocess.run(
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        if inventory_result.returncode != 0:
            raise ManagerError("Cannot inventory the installed runtime")
        inventory = json.loads(inventory_result.stdout)
        state = {
            "kind": "atomlearn.installed-runtime",
            "schema_version": 1,
            "id": expected["id"],
            "bundle_sha256": expected["sha256"],
            "recipe_sha256": expected["recipe_sha256"],
            "profile": expected.get("profile"),
            "profile_hash": expected.get("profile_hash"),
            "dependency_lock_sha256": expected.get("dependency_lock_sha256"),
            "content_sha256": "sha256:" + "0" * 64,
            "inventory": sorted(inventory, key=lambda item: item["name"].casefold()),
        }
        _mark_runtime_read_only(staging)
        os.replace(staging, destination)
        state["content_sha256"] = _runtime_content_hash(destination)
        atomic_bytes(destination / "atomlearn-runtime.json", canonical_json(state))
        (destination / "atomlearn-runtime.json").chmod(
            stat.S_IMODE((destination / "atomlearn-runtime.json").stat().st_mode) & ~0o222
        )
        return state
    finally:
        # Staging paths are transaction-owned and retained on failure for recovery/audit.
        pass


def _import_preflight(runtime_root: Path, modules: list[str]) -> tuple[bool, list[str]]:
    if not modules:
        return True, []
    marker = "ATOMLEARN_IMPORT_PREFLIGHT="
    script = """import importlib,json,sys
mods=json.loads(sys.argv[1])
failed=[]
for module in mods:
    try:
        importlib.import_module(module)
    except Exception:
        failed.append(module)
print("ATOMLEARN_IMPORT_PREFLIGHT=" + json.dumps(failed))
raise SystemExit(bool(failed))
"""
    result = subprocess.run(
        [str(runtime_python(runtime_root)), "-c", script, json.dumps(modules)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    )
    try:
        payload = next(
            line.removeprefix(marker)
            for line in reversed(result.stdout.splitlines())
            if line.startswith(marker)
        )
        missing = json.loads(payload)
    except (StopIteration, json.JSONDecodeError):
        missing = modules
    return result.returncode == 0 and isinstance(missing, list) and not missing, list(missing)


def _model_preflight(expected: dict[str, Any], model_dir: Path | None) -> tuple[bool, str | None]:
    profile = expected.get("profile") or {}
    if profile.get("model_policy", {}).get("mode") != "pinned_local":
        return True, None
    if model_dir is None:
        return False, "model_missing"
    model_lock = expected.get("model_lock")
    if not isinstance(model_lock, dict) or model_lock.get("trust_remote_code") is not False:
        return False, "model_policy_invalid"
    if not model_dir.is_dir() or is_reparse_or_symlink(model_dir):
        return False, "model_missing"
    expected_paths = {Path(item["path"]).as_posix() for item in model_lock.get("files", [])}
    actual_paths: set[str] = set()
    for path in model_dir.rglob("*"):
        if is_reparse_or_symlink(path):
            return False, "model_unregistered_file"
        if path.is_file():
            actual_paths.add(path.relative_to(model_dir).as_posix())
    if actual_paths != expected_paths:
        return False, "model_unregistered_file"
    for item in model_lock.get("files", []):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            return False, "model_policy_invalid"
        path = model_dir / relative
        if (
            not path.is_file()
            or is_reparse_or_symlink(path)
            or path.stat().st_size != item["size"]
            or sha256_file(path) != item["sha256"]
        ):
            return False, "model_hash_mismatch"
    return True, None


def _native_preflight(expected: dict[str, Any]) -> tuple[bool, str | None, list[dict[str, Any]]]:
    results = []
    for requirement in (expected.get("profile") or {}).get("native_requirements", []):
        executable = shutil.which(requirement["command"])
        if executable is None:
            results.append({"id": requirement["id"], "available": False, "path": None})
            return False, "native_engine_missing", results
        result = subprocess.run(
            [executable, *requirement["version_args"]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        results.append(
            {
                "id": requirement["id"],
                "available": result.returncode == 0,
                "path": executable,
                "version": (result.stdout or result.stderr).splitlines()[0][:200]
                if (result.stdout or result.stderr)
                else "",
            }
        )
        if result.returncode != 0:
            return False, "native_engine_unusable", results
    return True, None, results


def profile_preflight(
    runtime_root: Path,
    expected: dict[str, Any],
    *,
    model_dir: Path | None = None,
) -> dict[str, Any]:
    verify_installed_runtime(runtime_root, expected)
    profile = expected.get("profile") or {
        "name": "base",
        "capabilities": ["base"],
        "stability": "legacy",
        "python_modules": [],
        "native_requirements": [],
        "model_policy": {"mode": "none", "silent_download": False},
        "smoke_suites": ["base"],
    }
    imports_ok, missing_modules = _import_preflight(runtime_root, profile["python_modules"])
    native_ok, native_reason, native = _native_preflight(expected)
    model_ok, model_reason = _model_preflight(expected, model_dir)
    blocked_reason = None
    if not imports_ok:
        blocked_reason = "python_adapter_missing"
    elif not native_ok:
        blocked_reason = native_reason
    elif not model_ok:
        blocked_reason = model_reason
    return {
        "ok": blocked_reason is None,
        "profile": profile["name"],
        "profile_hash": expected.get("profile_hash"),
        "capabilities": profile["capabilities"],
        "stable": profile["stability"] == "stable" and expected.get("smoke", {}).get("status") == "passed",
        "usable": blocked_reason is None,
        "blocked_reason": blocked_reason,
        "missing_python_modules": missing_modules,
        "native": native,
        "model_dir": str(model_dir.resolve()) if model_dir else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic offline AtomLearn runtime bundle")
    parser.add_argument("wheel_dir", help="Directory containing the Core wheel and its complete locked wheel set")
    parser.add_argument("--output-dir", required=True, help="New runtime bundle output directory")
    parser.add_argument("--core-version", required=True, help="Core semantic version represented by the wheel set")
    parser.add_argument("--platform", choices=["linux", "windows"], required=True, help="Target operating system")
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True, help="Target CPU architecture")
    parser.add_argument("--python-minor", choices=["3.10", "3.11", "3.12", "3.13"], required=True, help="Target Python minor")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_SPECS),
        default="base",
        help="Finite signed capability profile represented by this wheelhouse",
    )
    parser.add_argument(
        "--model-lock",
        help="Local JSON/YAML model lock with explicit revision and file hashes; required by semantic profiles",
    )
    parser.add_argument(
        "--smoke-report",
        help="Canonical profile smoke JSON/YAML; omitted bundles remain pending and cannot enter a stable release",
    )
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        result = build_runtime_bundle(
            Path(args.wheel_dir), Path(args.output_dir), core_version=args.core_version,
            system=args.platform, architecture=args.architecture, python_minor=args.python_minor,
            profile_name=args.profile,
            model_lock=read_mapping(Path(args.model_lock).resolve()) if args.model_lock else None,
            smoke_report=read_mapping(Path(args.smoke_report).resolve()) if args.smoke_report else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ManagerError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
