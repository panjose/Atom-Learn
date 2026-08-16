"""Side-by-side update transactions, health checks, recovery, and paired rollback."""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from . import MANAGER_VERSION
from .common import (
    FileLock,
    ManagerError,
    atomic_bytes,
    atomic_text,
    atomic_yaml,
    canonical_json,
    is_reparse_or_symlink,
    manager_root,
    now_iso,
    read_mapping,
    require_schema,
    sha256_bytes,
    sha256_file,
    version_tuple,
)
from .manifest import load_trust, validate_release_manifest
from .runtime import install_runtime, runtime_python, select_runtime, verify_installed_runtime
from .statecopy import apply_migrated_files, plan_state, restore_files, snapshot_and_migrate, state_copy_size
from .transport import download_release_asset, fetch_release_bytes
from .verify import content_tree_hash, safe_extract, verify_release


class SimulatedInterruption(ManagerError):
    """Test-only process-boundary simulation that deliberately skips auto-recovery."""


INTERRUPTIBLE_STAGES = (
    "planned",
    "downloaded",
    "verified",
    "state_copied",
    "installed",
    "runtime_installed",
    "health_checked",
    "state_applied",
    "activated",
)


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(manifest))


def load_active(root: Path) -> dict[str, Any] | None:
    path = root / "active.yaml"
    if not path.is_file():
        return None
    active = read_mapping(path)
    require_schema(active, "active")
    return active


def save_transaction(root: Path, transaction: dict[str, Any]) -> None:
    transaction["updated_at"] = now_iso()
    require_schema(transaction, "transaction")
    atomic_yaml(root / "transactions" / f"{transaction['id']}.yaml", transaction)


def load_transaction(root: Path, transaction_id: str) -> dict[str, Any]:
    transaction = read_mapping(root / "transactions" / f"{transaction_id}.yaml")
    require_schema(transaction, "transaction")
    return transaction


def _manifest_from_source(source: str, *, offline: bool = False) -> tuple[dict[str, Any] | None, str]:
    if source.startswith(("https://", "http://")):
        if offline:
            return None, "offline"
        if not source.startswith("https://"):
            raise ManagerError("Release manifests may be fetched only over HTTPS")
        try:
            content, _ = fetch_release_bytes(source, accept="application/json", limit=2 * 1024 * 1024)
        except ManagerError:
            raise
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagerError("Remote release manifest is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ManagerError("Remote release manifest must be an object")
        return value, source
    path = Path(source)
    return read_mapping(path), str(path.resolve())


def check_release(root: Path, manifest_source: str, channel: str, *, offline: bool = False) -> dict[str, Any]:
    trust = load_trust(root)
    manifest, source = _manifest_from_source(manifest_source, offline=offline)
    active = load_active(root)
    if manifest is None:
        return {
            "ok": True,
            "offline": True,
            "current_version": active["current_version"] if active else None,
            "available": False,
            "reason": "offline_requested",
        }
    validate_release_manifest(manifest, trust, requested_channel=channel)
    current = active["current_version"] if active else None
    available = current is None or version_tuple(manifest["version"]) > version_tuple(current)
    return {
        "ok": True,
        "offline": False,
        "current_version": current,
        "available_version": manifest["version"],
        "available": available,
        "channel": manifest["channel"],
        "manifest_hash": _manifest_hash(manifest),
        "source": source,
    }


def _assert_isolated(root: Path, data_root: Path | None, workspaces: list[Path]) -> None:
    roots = [path.resolve() for path in ([data_root] if data_root else []) + workspaces]
    for path in roots:
        if path == root or root in path.parents or path in root.parents:
            raise ManagerError("Manager install root must be isolated from user data and course workspaces")


def plan_update(
    root: Path,
    version: str,
    manifest_source: str,
    artifact_path: Path | None,
    runtime_bundle_path: Path | None,
    data_root: Path | None,
    workspaces: list[Path],
    channel: str,
) -> dict[str, Any]:
    trust = load_trust(root)
    manifest, _ = _manifest_from_source(manifest_source)
    if manifest is None:  # defensive: only explicit check mode may operate without a manifest
        raise ManagerError(
            "A verified release manifest is required to plan an update",
            code="release_manifest_required",
        )
    validate_release_manifest(manifest, trust, requested_channel=channel)
    if manifest["version"] != version:
        raise ManagerError("Requested update version does not match the signed manifest")
    active = load_active(root)
    if active and version_tuple(version) <= version_tuple(active["current_version"]):
        raise ManagerError("Update only accepts a newer version; use paired rollback for downgrades")
    _assert_isolated(root, data_root, workspaces)
    state = plan_state(data_root, workspaces, manifest)
    verified = False
    archive = None
    if artifact_path is not None:
        archive = verify_release(manifest, artifact_path)
        verified = True
    selected_runtime = select_runtime(manifest)
    runtime_verified = False
    if runtime_bundle_path is not None:
        if selected_runtime is None:
            raise ManagerError("A local runtime bundle cannot be used with a legacy manifest")
        from .runtime import inspect_runtime_bundle

        inspect_runtime_bundle(runtime_bundle_path, selected_runtime)
        runtime_verified = True
    fake_free = os.environ.get("ATOMLEARN_MANAGER_FAKE_FREE_BYTES")
    free = int(fake_free) if fake_free is not None else shutil.disk_usage(root).free
    state_bytes = state_copy_size(data_root, workspaces)
    runtime_size = int(selected_runtime["size"]) if selected_runtime else 0
    required = int(manifest["artifact"]["size"]) * 2 + runtime_size * 3 + state_bytes * 2 + 32 * 1024 * 1024
    return {
        "ok": True,
        "current_version": active["current_version"] if active else None,
        "target_version": version,
        "channel": channel,
        "artifact_verified": verified,
        "artifact_file_count": archive["file_count"] if archive else None,
        "runtime": {
            "id": selected_runtime["id"] if selected_runtime else None,
            "bundle_verified": runtime_verified,
            "isolated": selected_runtime is not None,
        },
        "disk": {"free_bytes": free, "required_bytes": required, "sufficient": free >= required},
        "state": state,
        "activation": "side_by_side_atomic_pointer",
        "old_release_retained": bool(active),
    }


def _download_artifact(manifest: dict[str, Any], source: Path | None, destination: Path) -> None:
    expected_size = int(manifest["artifact"]["size"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        if not source.is_file() or is_reparse_or_symlink(source):
            raise ManagerError(f"Artifact source must be a regular non-link file: {source}")
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    else:
        url = manifest["source"]["artifact_url"]
        try:
            download_release_asset(url, destination, expected_size=expected_size)
        except ManagerError:
            raise


def _download_runtime_bundle(expected: dict[str, Any], source: Path | None, destination: Path) -> None:
    expected_size = int(expected["size"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        if not source.is_file() or is_reparse_or_symlink(source):
            raise ManagerError(f"Runtime bundle source must be a regular non-link file: {source}")
        if source.name != expected["filename"]:
            raise ManagerError("Runtime bundle source filename does not match the signed manifest")
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    else:
        try:
            download_release_asset(expected["url"], destination, expected_size=expected_size)
        except ManagerError:
            raise


def _maybe_interrupt(stage: str) -> None:
    if os.environ.get("ATOMLEARN_MANAGER_FAIL_AFTER") == stage:
        raise SimulatedInterruption(f"Simulated process interruption after {stage}")


def _installed_files(release: Path) -> list[tuple[str, bytes]]:
    result = []
    for path in sorted(release.rglob("*"), key=lambda item: item.as_posix()):
        if is_reparse_or_symlink(path):
            raise ManagerError(f"Installed release contains a link or reparse point: {path}")
        if path.is_file():
            result.append((path.relative_to(release).as_posix(), path.read_bytes()))
    return result


def verify_installed(root: Path, version: str, manifest: dict[str, Any]) -> None:
    release = root / "releases" / version
    if not release.is_dir() or is_reparse_or_symlink(release):
        raise ManagerError(f"Installed release directory is missing or unsafe: {release}")
    if content_tree_hash(_installed_files(release)) != manifest["core_content_sha256"]:
        raise ManagerError(f"Installed release content hash is invalid: {version}")


def _smoke(root: Path, version: str, manifest: dict[str, Any], transaction_root: Path | None = None, actual_paths: tuple[Path | None, list[Path]] | None = None) -> None:
    core = root / "releases" / version / "atom-learn" / "scripts" / "atomlearn.py"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    selected_runtime = select_runtime(manifest)
    if selected_runtime is not None:
        python = runtime_python(root / "runtimes" / selected_runtime["id"])
        prefix = [str(python), "-m", "atomlearn"]
    else:
        prefix = [sys.executable, str(core)]
    commands = [[*prefix, "version"], [*prefix, "--help"]]
    if transaction_root is not None:
        data_copy = transaction_root / "state-copy" / "data"
        workspace_parent = transaction_root / "state-copy" / "workspaces"
        mirrors = sorted([path for path in workspace_parent.iterdir() if path.is_dir()]) if workspace_parent.is_dir() else []
        commands.append(
            [*prefix, "migrate", "validate", "--data-dir", str(data_copy.resolve()), *sum((["--workspace", str(path.resolve())] for path in mirrors), [])]
        )
        for mirror in mirrors:
            commands.append([*prefix, "validate", str(mirror.resolve())])
            commands.append([*prefix, "status", str(mirror.resolve()), "--json"])
        environment["ATOMLEARN_DATA_DIR"] = str(data_copy.resolve())
    elif actual_paths is not None:
        data_root, workspaces = actual_paths
        command = [*prefix, "migrate", "validate"]
        if data_root is not None:
            command.extend(["--data-dir", str(data_root)])
        for workspace in workspaces:
            command.extend(["--workspace", str(workspace)])
        commands.append(command)
    for command in commands:
        result = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        if result.returncode != 0:
            raise ManagerError(f"Core {version} health check failed: {' '.join(command[len(prefix):])}: {result.stderr.strip()}")
        if command[-1] == "version":
            try:
                reported = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ManagerError("Core version health check did not return JSON") from exc
            if reported.get("core_version") != version:
                raise ManagerError("Core health check reported a mismatched version")
    if manifest.get("manifest_version") == 2 and transaction_root is not None:
        _capability_smoke(root, version, manifest, prefix, transaction_root, environment)


def _smoke_command(prefix: list[str], arguments: list[str], environment: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        [*prefix, *arguments], env=environment, capture_output=True, text=True, encoding="utf-8",
        timeout=120, check=False,
    )
    if result.returncode != 0:
        raise ManagerError(f"Capability smoke failed for {' '.join(arguments[:3])}: {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ManagerError(f"Capability smoke did not return JSON for {' '.join(arguments[:3])}") from exc
    if not isinstance(value, dict):
        raise ManagerError("Capability smoke result must be a JSON object")
    return value


def _minimal_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "\n".join(
        [
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET",
            "0.5 w",
            "100 680 m 500 680 l S",
            "100 640 m 500 640 l S",
            "100 600 m 500 600 l S",
            "100 600 m 100 680 l S",
            "300 600 m 300 680 l S",
            "500 600 m 500 680 l S",
            "BT /F1 10 Tf 110 655 Td (Concept) Tj ET",
            "BT /F1 10 Tf 310 655 Td (Meaning) Tj ET",
            "BT /F1 10 Tf 110 615 Td (Secant quotient) Tj ET",
            "BT /F1 10 Tf 310 615 Td (Tangent slope) Tj ET",
            r"BT /F1 10 Tf 72 560 Td (Formula: f\(x\)=x^2) Tj ET",
        ]
    ).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(content)


def _capability_smoke(
    root: Path,
    version: str,
    manifest: dict[str, Any],
    prefix: list[str],
    transaction_root: Path,
    environment: dict[str, str],
) -> None:
    required = set(manifest["capabilities"]["required_smoke"])
    supported = {"core", "bridge", "documents", "rag", "exam", "research"}
    unknown = sorted(required - supported)
    if unknown:
        raise ManagerError("Release requires unknown smoke capabilities: " + ", ".join(unknown))
    release = root / "releases" / version
    fixture_path = release / "atom-learn" / "assets" / "smoke-fixtures.json"
    fixture_bytes = fixture_path.read_bytes()
    if sha256_bytes(fixture_bytes) != manifest["smoke_fixture_sha256"]:
        raise ManagerError("Smoke fixture identity changed after release installation")
    fixture = json.loads(fixture_bytes.decode("utf-8"))
    scratch = transaction_root / "capability-smoke"
    scratch.mkdir(parents=True, exist_ok=False)
    workspace = scratch / "workspace"
    if {"documents", "rag", "exam", "research"} & required:
        _smoke_command(
            prefix,
            ["init", str(workspace), "--course-id", "release.smoke", "--title", "Release smoke", "--goal", "Verify signed runtime"],
            environment,
        )
    if {"documents", "rag"} & required:
        sources_dir = scratch / "sources"
        sources_dir.mkdir(parents=True, exist_ok=False)
        text_path = sources_dir / "smoke.txt"
        html_path = sources_dir / "smoke.html"
        pdf_path = sources_dir / "smoke.pdf"
        docx_path = sources_dir / "smoke.docx"
        atomic_bytes(text_path, fixture["text"].encode("utf-8"))
        atomic_bytes(html_path, fixture["html"].encode("utf-8"))
        try:
            pdf_fixture = base64.b64decode(fixture["pdf_base64"], validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise ManagerError("Signed PDF smoke fixture is missing or invalid") from exc
        atomic_bytes(pdf_path, pdf_fixture)
        docx_fixture = scratch / "docx-fixture.json"
        atomic_bytes(docx_fixture, canonical_json(fixture["docx"]))
        script = (
            "import json,sys; from docx import Document; "
            "f=json.load(open(sys.argv[1],encoding='utf-8')); d=Document(); d.add_heading(f['heading'],1); "
            "d.add_paragraph(f['paragraph']); t=d.add_table(rows=len(f['table']),cols=len(f['table'][0])); "
            "[(setattr(t.cell(i,j),'text',v)) for i,row in enumerate(f['table']) for j,v in enumerate(row)]; d.save(sys.argv[2])"
        )
        created = subprocess.run(
            [prefix[0], "-c", script, str(docx_fixture), str(docx_path)], env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        if created.returncode != 0:
            raise ManagerError(f"DOCX smoke fixture creation failed: {created.stderr.strip()}")
        sources = {
            "sources": [
                {"id": f"smoke-{path.suffix[1:]}", "title": f"Smoke {path.suffix}", "authority": "official", "path": str(path)}
                for path in [text_path, html_path, pdf_path, docx_path]
            ]
        }
        sources_payload = scratch / "sources.yaml"
        atomic_yaml(sources_payload, sources)
        _smoke_command(prefix, ["rag", "init", str(workspace)], environment)
        ingested = _smoke_command(prefix, ["rag", "ingest", str(workspace), "--input", str(sources_payload)], environment)
        if ingested.get("result", {}).get("sources") != 4:
            raise ManagerError("Document/RAG smoke did not ingest all TXT HTML PDF and DOCX fixtures")
        query_payload = scratch / "query.yaml"
        atomic_yaml(query_payload, {"query": "derivative local rate", "top_k": 10})
        searched = _smoke_command(prefix, ["rag", "search", str(workspace), "--input", str(query_payload)], environment)
        source_ids = {item.get("source_id") for item in searched.get("results", [])}
        if not {"smoke-txt", "smoke-html", "smoke-pdf", "smoke-docx"} <= source_ids:
            raise ManagerError("Document/RAG smoke could not retrieve every signed fixture type")
        for name, query, expected_locator in [
            ("pdf", "secant quotient tangent slope", "table"),
            ("docx", "gradient steepest ascent direction", "table 1"),
        ]:
            locator_query = scratch / f"{name}-locator-query.yaml"
            atomic_yaml(locator_query, {"query": query, "top_k": 10})
            located = _smoke_command(prefix, ["rag", "search", str(workspace), "--input", str(locator_query)], environment)
            candidates = [item for item in located.get("results", []) if item.get("source_id") == f"smoke-{name}"]
            if not any(expected_locator in item.get("locator", "").lower() for item in candidates):
                raise ManagerError(f"Document/RAG smoke did not preserve the {name.upper()} table locator")
        _smoke_command(prefix, ["rag", "validate", str(workspace)], environment)
    if "exam" in required:
        _smoke_command(prefix, ["exam", "init", str(workspace), "--title", "Release exam smoke", "--target-date", "2099-01-01"], environment)
        exam_payload = scratch / "exam-source.yaml"
        atomic_yaml(
            exam_payload,
            {
                "documents": [
                    {
                        "paper": {
                            "id": "release-smoke-paper", "title": "Release smoke paper", "year": 2098,
                            "session": "release", "kind": "practice_set", "total_points": 5,
                            "source_id": "smoke-txt", "locator": "question 1",
                        },
                        "questions": "Question 1. Explain a derivative as a local rate. [5 marks]",
                        "answers": "1. It is the limiting rate of change.",
                        "marking_scheme": "Q1: definition 2 marks, local-rate interpretation 3 marks",
                    }
                ]
            },
        )
        processed = _smoke_command(prefix, ["exam", "process", str(workspace), "--input", str(exam_payload)], environment)
        if processed.get("result", {}).get("processing", [{}])[0].get("question_count") != 1:
            raise ManagerError("Exam smoke did not transform its signed source fixture into question state")
        _smoke_command(prefix, ["exam", "status", str(workspace)], environment)
        _smoke_command(prefix, ["exam", "validate", str(workspace)], environment)
    if "research" in required:
        _smoke_command(
            prefix,
            [
                "research", "init", str(workspace), "--field", "Runtime reliability",
                "--question", "Does the signed runtime preserve evidence?", "--scope", "Release smoke",
            ],
            environment,
        )
        research_payload = scratch / "research-source.yaml"
        atomic_yaml(
            research_payload,
            {
                "papers": [
                    {
                        "id": "paper.release.smoke", "title": "Signed runtime evidence preservation",
                        "authors": ["AtomLearn Maintainers"], "year": 2098, "role": "method", "priority": 1,
                        "status": "queued", "tags": ["release-smoke"], "prerequisite_paper_ids": [],
                        "cites": [], "locator": "smoke-pdf: page 1", "url": "", "doi": "",
                    }
                ]
            },
        )
        imported = _smoke_command(prefix, ["research", "import", str(workspace), "--input", str(research_payload)], environment)
        if imported.get("result", {}).get("total_papers") != 1:
            raise ManagerError("Research smoke did not transform its signed source locator into paper state")
        _smoke_command(prefix, ["research", "status", str(workspace)], environment)
        _smoke_command(prefix, ["research", "validate", str(workspace)], environment)


def _mark_read_only(release: Path) -> None:
    for path in release.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IREAD)


def _recover_transaction(root: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    try:
        restore_files(transaction["state_files"], lambda: save_transaction(root, transaction))
        if transaction["pointer_switched"]:
            active = load_active(root)
            if active and active["transaction_id"] != transaction["id"]:
                raise ManagerError("Active pointer moved after the failed transaction; refusing automatic recovery")
            previous = transaction["previous_active"]
            active_path = root / "active.yaml"
            if previous is None:
                if active_path.exists():
                    archived = root / "transactions" / f"abandoned-active-{transaction['id']}.yaml"
                    if archived.exists():
                        raise ManagerError("Recovery archive already exists while active pointer still needs recovery")
                    os.replace(active_path, archived)
            else:
                atomic_yaml(active_path, previous)
            transaction["pointer_switched"] = False
        transaction["status"] = "recovered"
        transaction["stage"] = "recovered"
        save_transaction(root, transaction)
        return transaction
    except ManagerError as exc:
        transaction["status"] = "needs_manual_recovery"
        transaction["error"] = str(exc)
        save_transaction(root, transaction)
        raise


def apply_update(
    root: Path,
    version: str,
    manifest_source: str,
    artifact_source: Path | None,
    runtime_bundle_source: Path | None,
    data_root: Path | None,
    workspaces: list[Path],
    channel: str,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Update apply requires --confirmed after reviewing update plan")
    _assert_isolated(root, data_root, workspaces)
    with FileLock(root / ".manager.lock"):
        trust = load_trust(root)
        manifest, manifest_origin = _manifest_from_source(manifest_source)
        if manifest is None:  # defensive: apply never has an offline mode
            raise ManagerError(
                "A verified release manifest is required to apply an update",
                code="release_manifest_required",
            )
        validate_release_manifest(manifest, trust, requested_channel=channel)
        if manifest["version"] != version:
            raise ManagerError("Requested update version does not match the signed manifest")
        previous_active = load_active(root)
        if previous_active and version_tuple(version) <= version_tuple(previous_active["current_version"]):
            raise ManagerError("Update only accepts a newer Core; use rollback for a paired downgrade")
        plan = plan_update(root, version, manifest_source, artifact_source, runtime_bundle_source, data_root, workspaces, channel)
        if not plan["disk"]["sufficient"]:
            raise ManagerError("Insufficient disk space for side-by-side release and state copies")
        transaction_id = "txn-" + uuid.uuid4().hex
        transaction_root = root / "staging" / transaction_id.removeprefix("txn-")[:12]
        transaction_root.mkdir(parents=True, exist_ok=False)
        staged_artifact = transaction_root / manifest["artifact"]["filename"]
        selected_runtime = select_runtime(manifest)
        staged_runtime_bundle = transaction_root / selected_runtime["filename"] if selected_runtime else None
        transaction = {
            "kind": "atomlearn.manager-transaction",
            "schema_version": 1,
            "id": transaction_id,
            "target_version": version,
            "previous_version": previous_active["current_version"] if previous_active else None,
            "previous_active": previous_active,
            "status": "in_progress",
            "stage": "planned",
            "manifest_hash": _manifest_hash(manifest),
            "artifact_hash": manifest["artifact"]["sha256"],
            "release_manifest_path": manifest_origin,
            "artifact_path": str(staged_artifact),
            "runtime_id": selected_runtime["id"] if selected_runtime else None,
            "runtime_bundle_path": str(staged_runtime_bundle) if staged_runtime_bundle else None,
            "data_root": str(data_root) if data_root else None,
            "workspaces": [str(path) for path in workspaces],
            "state_files": [],
            "pointer_switched": False,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "error": None,
        }
        save_transaction(root, transaction)
        try:
            _maybe_interrupt("planned")
            _download_artifact(manifest, artifact_source, staged_artifact)
            transaction["stage"] = "downloaded"
            save_transaction(root, transaction)
            _maybe_interrupt("downloaded")
            verify_release(manifest, staged_artifact)
            transaction["stage"] = "verified"
            save_transaction(root, transaction)
            _maybe_interrupt("verified")
            transaction["state_files"] = snapshot_and_migrate(transaction_root, data_root, workspaces, manifest)
            transaction["stage"] = "state_copied"
            save_transaction(root, transaction)
            _maybe_interrupt("state_copied")
            release = root / "releases" / version
            manifest_copy = root / "manifests" / f"{version}.json"
            if release.exists():
                if manifest_copy.exists() and _manifest_hash(read_mapping(manifest_copy)) != _manifest_hash(manifest):
                    raise ManagerError("Existing side-by-side release has a different signed manifest")
                verify_installed(root, version, manifest)
                if not manifest_copy.exists():
                    atomic_text(manifest_copy, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            else:
                extracted = safe_extract(staged_artifact, transaction_root / "unpacked", version)
                os.replace(extracted, release)
                _mark_read_only(release)
                if manifest_copy.exists():
                    raise ManagerError(f"Release manifest exists without its release directory: {manifest_copy}")
                atomic_text(manifest_copy, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            transaction["stage"] = "installed"
            save_transaction(root, transaction)
            _maybe_interrupt("installed")
            verify_installed(root, version, manifest)
            if selected_runtime is not None and staged_runtime_bundle is not None:
                _download_runtime_bundle(selected_runtime, runtime_bundle_source, staged_runtime_bundle)
                install_runtime(staged_runtime_bundle, selected_runtime, root / "runtimes" / selected_runtime["id"])
            transaction["stage"] = "runtime_installed"
            save_transaction(root, transaction)
            _maybe_interrupt("runtime_installed")
            _smoke(root, version, manifest, transaction_root)
            transaction["stage"] = "health_checked"
            save_transaction(root, transaction)
            _maybe_interrupt("health_checked")
            apply_migrated_files(transaction["state_files"], lambda: save_transaction(root, transaction))
            transaction["stage"] = "state_applied"
            save_transaction(root, transaction)
            _maybe_interrupt("state_applied")
            active = {
                "kind": "atomlearn.manager-active",
                "schema_version": 1,
                "current_version": version,
                "previous_version": previous_active["current_version"] if previous_active else None,
                "manifest_hash": _manifest_hash(manifest),
                "transaction_id": transaction_id,
            }
            if selected_runtime is not None:
                active["runtime_id"] = selected_runtime["id"]
                active["skill_protocol_version"] = manifest["skill_protocol"]["version"]
            require_schema(active, "active")
            atomic_yaml(root / "active.yaml", active)
            transaction["pointer_switched"] = True
            transaction["stage"] = "activated"
            save_transaction(root, transaction)
            _maybe_interrupt("activated")
            if manifest.get("manifest_version") == 2 and "bridge" in manifest["capabilities"]["required_smoke"]:
                from .codex import resolve_core_skill

                resolved = resolve_core_skill(root)
                if resolved["core_version"] != version:
                    raise ManagerError("Post-activation bridge resolved a different Core version")
            transaction["status"] = "committed"
            transaction["stage"] = "committed"
            save_transaction(root, transaction)
            return {"ok": True, "active": active, "transaction": transaction_id, "old_release_retained": previous_active is not None}
        except SimulatedInterruption:
            raise
        except Exception as exc:
            transaction["status"] = "failed"
            transaction["error"] = str(exc)
            save_transaction(root, transaction)
            try:
                _recover_transaction(root, transaction)
            except ManagerError:
                pass
            raise ManagerError(f"Update failed; previous Core remains active: {exc}") from exc


def recover_latest(root: Path) -> dict[str, Any]:
    with FileLock(root / ".manager.lock"):
        candidates = []
        for path in (root / "transactions").glob("txn-*.yaml"):
            transaction = read_mapping(path)
            require_schema(transaction, "transaction")
            if transaction["status"] in {"in_progress", "failed", "needs_manual_recovery"}:
                candidates.append(transaction)
        if not candidates:
            return {"ok": True, "recovered": False, "reason": "no_unfinished_transaction"}
        transaction = sorted(candidates, key=lambda item: item["updated_at"])[-1]
        recovered = _recover_transaction(root, transaction)
        return {"ok": True, "recovered": True, "transaction": recovered["id"], "active": load_active(root)}


def rollback(root: Path, version: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Rollback requires --confirmed")
    with FileLock(root / ".manager.lock"):
        active = load_active(root)
        if not active or active.get("previous_version") != version:
            raise ManagerError("Rollback is limited to the paired previous version")
        current_transaction = load_transaction(root, active["transaction_id"])
        target_manifest = read_mapping(root / "manifests" / f"{version}.json")
        validate_release_manifest(target_manifest, load_trust(root))
        verify_installed(root, version, target_manifest)
        rollback_runtime = select_runtime(target_manifest)
        if rollback_runtime is not None:
            verify_installed_runtime(root / "runtimes" / rollback_runtime["id"], rollback_runtime)
        transaction_id = "txn-" + uuid.uuid4().hex
        reverse_files = [
            {
                **item,
                "backup": item["migrated"],
                "migrated": item["backup"],
                "original_hash": item["migrated_hash"],
                "migrated_hash": item["original_hash"],
                "applied": False,
            }
            for item in current_transaction["state_files"]
        ]
        transaction = {
            "kind": "atomlearn.manager-transaction",
            "schema_version": 1,
            "id": transaction_id,
            "target_version": version,
            "previous_version": active["current_version"],
            "previous_active": active,
            "status": "in_progress",
            "stage": "planned",
            "manifest_hash": _manifest_hash(target_manifest),
            "artifact_hash": target_manifest["artifact"]["sha256"],
            "release_manifest_path": str(root / "manifests" / f"{version}.json"),
            "artifact_path": f"installed:{version}",
            "runtime_id": rollback_runtime["id"] if rollback_runtime else None,
            "runtime_bundle_path": f"installed:{rollback_runtime['id']}" if rollback_runtime else None,
            "data_root": current_transaction["data_root"],
            "workspaces": current_transaction["workspaces"],
            "state_files": reverse_files,
            "pointer_switched": False,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "error": None,
        }
        save_transaction(root, transaction)
        try:
            apply_migrated_files(reverse_files, lambda: save_transaction(root, transaction))
            transaction["stage"] = "state_applied"
            save_transaction(root, transaction)
            data = Path(transaction["data_root"]) if transaction["data_root"] else None
            workspaces = [Path(path) for path in transaction["workspaces"]]
            _smoke(root, version, target_manifest, actual_paths=(data, workspaces))
            rolled = {
                "kind": "atomlearn.manager-active",
                "schema_version": 1,
                "current_version": version,
                "previous_version": active["current_version"],
                "manifest_hash": _manifest_hash(target_manifest),
                "transaction_id": transaction_id,
            }
            if rollback_runtime is not None:
                rolled["runtime_id"] = rollback_runtime["id"]
                rolled["skill_protocol_version"] = target_manifest["skill_protocol"]["version"]
            atomic_yaml(root / "active.yaml", rolled)
            transaction["pointer_switched"] = True
            transaction["status"] = "rolled_back"
            transaction["stage"] = "rolled_back"
            save_transaction(root, transaction)
            return {"ok": True, "active": rolled, "transaction": transaction_id}
        except Exception as exc:
            transaction["status"] = "failed"
            transaction["error"] = str(exc)
            save_transaction(root, transaction)
            _recover_transaction(root, transaction)
            raise ManagerError(f"Rollback failed; current Core remains active: {exc}") from exc


def status(root: Path) -> dict[str, Any]:
    active = load_active(root)
    unfinished = []
    transaction_dir = root / "transactions"
    if transaction_dir.is_dir():
        for path in transaction_dir.glob("txn-*.yaml"):
            value = read_mapping(path)
            require_schema(value, "transaction")
            if value["status"] in {"in_progress", "failed", "needs_manual_recovery"}:
                unfinished.append({"id": value["id"], "status": value["status"], "stage": value["stage"]})
    active_valid = False
    if active:
        manifest = read_mapping(root / "manifests" / f"{active['current_version']}.json")
        validate_release_manifest(manifest, load_trust(root))
        verify_installed(root, active["current_version"], manifest)
        selected_runtime = select_runtime(manifest)
        if selected_runtime is not None:
            if active.get("runtime_id") != selected_runtime["id"]:
                raise ManagerError("Active pointer runtime does not match the signed release")
            if active.get("skill_protocol_version") != manifest["skill_protocol"]["version"]:
                raise ManagerError("Active pointer Skill protocol does not match the signed release")
            verify_installed_runtime(root / "runtimes" / selected_runtime["id"], selected_runtime)
        active_valid = active["manifest_hash"] == _manifest_hash(manifest)
    return {
        "ok": active_valid if active else True,
        "manager_version": MANAGER_VERSION,
        "active": active,
        "active_valid": active_valid,
        "installed_versions": sorted(path.name for path in (root / "releases").iterdir() if path.is_dir()) if (root / "releases").is_dir() else [],
        "installed_runtimes": sorted(path.name for path in (root / "runtimes").iterdir() if path.is_dir()) if (root / "runtimes").is_dir() else [],
        "unfinished_transactions": unfinished,
        "recovery_required": bool(unfinished),
    }
