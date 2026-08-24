"""Fail-closed repository and wheel checks for public open-source readiness."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "README.zh-CN.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "CODE_OF_CONDUCT.md",
    "CITATION.cff",
    ".github/CODEOWNERS",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/dependabot.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/oss-readiness.yml",
    "docs/OPEN_SOURCE_RELEASE_CHECKLIST.md",
    "docs/OPEN_SOURCE_RELEASE_CHECKLIST.zh-CN.md",
]

SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub classic token": re.compile(rb"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    "GitHub fine-grained token": re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    "OpenAI API key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "Slack token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
}

FORBIDDEN_NAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
FORBIDDEN_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
FORBIDDEN_PARTS = {".atomlearn", ".private", "courses", "materials"}
PATH_SCAN_EXCEPTIONS = {"tests/fixtures/security/capsule-attacks.json"}
MAX_HISTORY_OBJECT_BYTES = 50_000_000


def command(*args: str) -> bytes:
    result = subprocess.run(
        list(args),
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"command failed ({' '.join(args)}): {stderr}")
    return result.stdout


def tracked_files() -> list[str]:
    return [item.decode("utf-8") for item in command("git", "ls-files", "-z").split(b"\0") if item]


def secret_labels(content: bytes) -> list[str]:
    return [label for label, pattern in SECRET_PATTERNS.items() if pattern.search(content)]


def check_required(paths: Iterable[str], failures: list[str]) -> None:
    tracked = set(paths)
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required public repository file: {relative}")
        elif relative not in tracked:
            failures.append(f"required public repository file is not tracked: {relative}")


def check_metadata(failures: list[str]) -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    manager_license = (ROOT / "manager" / "LICENSE").read_text(encoding="utf-8")
    if "Apache License" not in license_text or "Version 2.0, January 2004" not in license_text:
        failures.append("root LICENSE is not the expected Apache-2.0 text")
    if license_text != manager_license:
        failures.append("Core and Manager Apache-2.0 license files differ")

    for relative in ["pyproject.toml", "manager/pyproject.toml"]:
        project = (ROOT / relative).read_text(encoding="utf-8")
        if 'license = "Apache-2.0"' not in project:
            failures.append(f"{relative} does not declare Apache-2.0")
        if "https://github.com/panjose/Atom-Learn" not in project:
            failures.append(f"{relative} does not declare the public repository URL")

    root_project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', root_project, re.MULTILINE)
    if version_match is None:
        failures.append("pyproject.toml does not contain a plain semantic project version")
        current_version = "unknown"
    else:
        current_version = version_match.group(1)
    runtime = (ROOT / "manager" / "atomlearn_manager" / "runtime.py").read_text(encoding="utf-8")
    profiles = (ROOT / "atom-learn" / "assets" / "runtime-profiles.yaml").read_text(encoding="utf-8")
    current_ocr_contract = root_project + runtime + profiles
    if "PyMuPDF" in current_ocr_contract or '"fitz"' in current_ocr_contract:
        failures.append("current OCR packaging or runtime profile still declares the AGPL PyMuPDF adapter")
    if "pypdfium2" not in current_ocr_contract:
        failures.append("current OCR packaging does not declare pypdfium2")

    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    if f"`v{current_version}`" not in english or f"`v{current_version}`" not in chinese:
        failures.append("bilingual README does not identify the package release version")
    learning_path_english = "AtomLearn evaluates learning outcomes through the consented `atomlearn study` workflow"
    learning_path_chinese = "AtomLearn 通过明确同意的 `atomlearn study` 工作流评估学习效果"
    if learning_path_english not in english or learning_path_chinese not in chinese:
        failures.append("bilingual README omits the learning-effect study and evidence-layer path")
    code_blocks = lambda value: [block.strip() for block in re.findall(r"```[^\n]*\n(.*?)```", value, re.DOTALL)]
    if code_blocks(english) != code_blocks(chinese):
        failures.append("English and Chinese README code blocks are not aligned")

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    for expected in ["cff-version: 1.2.0", 'license: "Apache-2.0"', f'version: "{current_version}"']:
        if expected not in citation:
            failures.append(f"CITATION.cff is missing {expected}")


def check_ignore_policy(failures: list[str]) -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for expected in [".private/", "**/.atomlearn/", "courses/", "materials/", "build/", "dist/"]:
        if expected not in ignore:
            failures.append(f".gitignore is missing privacy/build rule: {expected}")


def forbidden_path(relative: str) -> bool:
    normalized = PurePosixPath(relative)
    lower_parts = {part.lower() for part in normalized.parts}
    name = normalized.name.lower()
    suffix = normalized.suffix.lower()
    return bool(lower_parts & FORBIDDEN_PARTS or name in FORBIDDEN_NAMES or suffix in FORBIDDEN_SUFFIXES)


def check_tracked_paths(paths: Iterable[str], failures: list[str]) -> None:
    for relative in paths:
        if forbidden_path(relative):
            failures.append(f"forbidden private or credential-shaped tracked path: {relative}")


def check_tracked_content(paths: Iterable[str], failures: list[str]) -> None:
    windows_user = re.compile(rb"[A-Za-z]:[\\/]Users[\\/][^\\/\r\n]+")
    posix_user = re.compile(rb"/(?:Users|home)/[^/\r\n]+")
    for relative in paths:
        path = ROOT / relative
        if path.is_symlink():
            failures.append(f"tracked symbolic link requires explicit public-repository review: {relative}")
            continue
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        for label in secret_labels(content):
            failures.append(f"possible {label} in tracked file: {relative}")
        if relative not in PATH_SCAN_EXCEPTIONS and (windows_user.search(content) or posix_user.search(content)):
            failures.append(f"user-specific absolute path in tracked file: {relative}")


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RuntimeError("unexpected end of git cat-file output")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def check_history(failures: list[str]) -> None:
    entries: list[tuple[bytes, str]] = []
    for line in command("git", "rev-list", "--objects", "--all").splitlines():
        object_id, separator, raw_path = line.partition(b" ")
        relative = raw_path.decode("utf-8", errors="replace") if separator else ""
        entries.append((object_id, relative))
        if relative and forbidden_path(relative):
            failures.append(f"forbidden private or credential-shaped path in Git history: {relative}")

    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    labels: set[str] = set()
    try:
        for object_id, _ in entries:
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().rstrip(b"\n")
            fields = header.split()
            if len(fields) != 3 or fields[1] == b"missing":
                raise RuntimeError("git cat-file returned an invalid object header")
            object_type = fields[1]
            object_size = int(fields[2])
            if object_size > MAX_HISTORY_OBJECT_BYTES:
                read_exact(process.stdout, object_size)
                failures.append(
                    "Git history contains an object larger than the automatic 50 MB scan limit; review it manually"
                )
            else:
                content = read_exact(process.stdout, object_size)
                if object_type == b"blob":
                    labels.update(secret_labels(content))
            if process.stdout.read(1) != b"\n":
                raise RuntimeError("git cat-file object terminator is invalid")
    finally:
        process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git cat-file history scan failed: {stderr}")
    for label in sorted(labels):
        failures.append(f"possible {label} in Git history; rotate it before making the repository public")


def check_wheels(directory: Path, failures: list[str]) -> None:
    wheels = sorted(directory.glob("*.whl")) if directory.is_dir() else []
    if not wheels:
        failures.append(f"wheel directory contains no wheels: {directory}")
        return
    saw_core = False
    saw_manager = False
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                failures.append(f"{wheel.name} does not contain exactly one METADATA file")
                continue
            metadata = archive.read(metadata_names[0]).decode("utf-8", errors="replace")
            if "License-Expression: Apache-2.0" not in metadata:
                failures.append(f"{wheel.name} lacks the Apache-2.0 license expression")
            license_names = {PurePosixPath(name).name for name in names if ".dist-info/licenses/" in name}
            if wheel.name.startswith("atom_learn-"):
                saw_core = True
                required = {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"}
                if not required <= license_names:
                    failures.append(f"{wheel.name} is missing Core license files: {sorted(required - license_names)}")
            elif wheel.name.startswith("atomlearn_manager-"):
                saw_manager = True
                if "LICENSE" not in license_names:
                    failures.append(f"{wheel.name} is missing the Manager LICENSE")
    if not saw_core:
        failures.append("wheel directory is missing the atom-learn wheel")
    if not saw_manager:
        failures.append("wheel directory is missing the atomlearn-manager wheel")


def evaluate(*, include_history: bool, wheel_dir: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    paths = tracked_files()
    check_required(paths, failures)
    check_metadata(failures)
    check_ignore_policy(failures)
    check_tracked_paths(paths, failures)
    check_tracked_content(paths, failures)
    if include_history:
        check_history(failures)
    if wheel_dir is not None:
        check_wheels(wheel_dir, failures)
    return {
        "kind": "atomlearn.open-source-readiness-report",
        "schema_version": 1,
        "ok": not failures,
        "history_scanned": include_history,
        "tracked_files_scanned": len(paths),
        "wheel_directory": str(wheel_dir) if wheel_dir is not None else None,
        "failures": failures,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Validate AtomLearn before public open-source publication")
    value.add_argument("--skip-history", action="store_true", help="Skip the Git history secret-pattern scan")
    value.add_argument("--wheel-dir", type=Path, help="Also validate built Core and Manager wheel license metadata")
    value.add_argument("--json", action="store_true", help="Emit the complete machine-readable report")
    return value


def main() -> int:
    args = parser().parse_args()
    report = evaluate(
        include_history=not args.skip_history,
        wheel_dir=args.wheel_dir.resolve() if args.wheel_dir else None,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["ok"]:
        print(
            "Open-source readiness gate passed: "
            f"{report['tracked_files_scanned']} tracked files; history_scanned={report['history_scanned']}"
        )
    else:
        print("Open-source readiness gate failed:", file=sys.stderr)
        for failure in report["failures"]:
            print(f"- {failure}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
