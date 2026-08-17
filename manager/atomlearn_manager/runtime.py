"""Build, verify, select, and install signed per-release runtime bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import stat
import subprocess
import sys
import uuid
import venv
import zipfile
from pathlib import Path
from typing import Any

from .common import ManagerError, atomic_bytes, canonical_json, is_reparse_or_symlink, require_schema, sha256_bytes, sha256_file
from .verify import MAX_FILES, MAX_RATIO, MAX_UNCOMPRESSED, _safe_member


RECIPE_NAME = "runtime-recipe.json"


def platform_identity() -> tuple[str, str, str]:
    system = "windows" if os.name == "nt" else "linux"
    machine = host_platform.machine().lower()
    architecture = "amd64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else machine
    if architecture not in {"amd64", "arm64"}:
        raise ManagerError(f"Unsupported runtime architecture: {machine}", code="runtime_platform_unsupported")
    return system, architecture, f"{sys.version_info.major}.{sys.version_info.minor}"


def runtime_id(version: str, system: str, architecture: str, python_minor: str) -> str:
    return f"{version}-{system}-{architecture}-py{python_minor.replace('.', '')}"


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
    identifier = runtime_id(core_version, system, architecture, python_minor)
    records = [_wheel_record(path) for path in wheels]
    recipe = {
        "kind": "atomlearn.runtime-bundle",
        "schema_version": 1,
        "id": identifier,
        "core_version": core_version,
        "platform": system,
        "architecture": architecture,
        "python_minor": python_minor,
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
        entries = [(RECIPE_NAME, canonical_json(recipe)), *[(f"wheels/{path.name}", path.read_bytes()) for path in wheels]]
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
            if name != RECIPE_NAME and not name.startswith("wheels/"):
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
        for field in ["id", "platform", "architecture", "python_minor", "core_wheel"]:
            if recipe[field] != expected[field]:
                raise ManagerError(f"Runtime bundle recipe disagrees with signed manifest field: {field}")
        if sha256_bytes(canonical) != expected["recipe_sha256"]:
            raise ManagerError("Runtime recipe hash does not match the signed manifest")
    return {"recipe": recipe, "files": files, "recipe_sha256": sha256_bytes(canonical)}


def select_runtime(manifest: dict[str, Any]) -> dict[str, Any] | None:
    if manifest.get("manifest_version") != 2:
        return None
    system, architecture, python_minor = platform_identity()
    matches = [
        item
        for item in manifest["runtime_bundles"]
        if item["platform"] == system and item["architecture"] == architecture and item["python_minor"] == python_minor
    ]
    if len(matches) != 1:
        raise ManagerError(
            f"Release has no unique runtime for {system}/{architecture}/Python {python_minor}",
            code="runtime_variant_unavailable",
            details={"platform": system, "architecture": architecture, "python_minor": python_minor},
        )
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
        or state.get("content_sha256") != _runtime_content_hash(runtime_root)
    ):
        raise ManagerError("Installed runtime state does not match the signed manifest")
    return state


def install_runtime(bundle: Path, expected: dict[str, Any], destination: Path) -> dict[str, Any]:
    inspected = inspect_runtime_bundle(bundle, expected)
    if destination.exists():
        return verify_installed_runtime(destination, expected)
    staging = destination.with_name(f".{destination.name}.building-{uuid.uuid4().hex[:12]}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    wheelhouse = destination.parent.parent / "wheelhouses" / expected["id"]
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
        core_wheel = wheelhouse / inspected["recipe"]["core_wheel"]["filename"]
        command = [
            str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse),
            "--disable-pip-version-check", str(core_wheel),
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
            "content_sha256": _runtime_content_hash(staging),
            "inventory": sorted(inventory, key=lambda item: item["name"].casefold()),
        }
        atomic_bytes(staging / "atomlearn-runtime.json", canonical_json(state))
        _mark_runtime_read_only(staging)
        os.replace(staging, destination)
        return state
    finally:
        # Staging paths are transaction-owned and retained on failure for recovery/audit.
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic offline AtomLearn runtime bundle")
    parser.add_argument("wheel_dir", help="Directory containing the Core wheel and its complete locked wheel set")
    parser.add_argument("--output-dir", required=True, help="New runtime bundle output directory")
    parser.add_argument("--core-version", required=True, help="Core semantic version represented by the wheel set")
    parser.add_argument("--platform", choices=["linux", "windows"], required=True, help="Target operating system")
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True, help="Target CPU architecture")
    parser.add_argument("--python-minor", choices=["3.10", "3.11", "3.12", "3.13"], required=True, help="Target Python minor")
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        result = build_runtime_bundle(
            Path(args.wheel_dir), Path(args.output_dir), core_version=args.core_version,
            system=args.platform, architecture=args.architecture, python_minor=args.python_minor,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ManagerError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
