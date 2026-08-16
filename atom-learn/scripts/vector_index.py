#!/usr/bin/env python3
"""Crash-safe generation-based optional HNSW indexes for AtomLearn RAG."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from atomlearn import atomic_text, iso, read_data, write_yaml


INDEX_KINDS = {"default", "semantic"}
_DEPS_VERIFIED = False


class VectorIndexError(RuntimeError):
    """An optional vector index cannot be built, verified, or queried."""


def _deps() -> tuple[Any, Any]:
    global _DEPS_VERIFIED
    try:
        backend_version = version("usearch")
    except PackageNotFoundError as exc:
        raise VectorIndexError("HNSW indexing requires the optional `scale` dependency set") from exc
    if not _DEPS_VERIFIED:
        try:
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import numpy; from usearch.index import Index; "
                    "i=Index(ndim=2,metric='cos',dtype='f32'); "
                    "i.add(1,numpy.asarray([1,0],dtype=numpy.float32),threads=1); "
                    "assert i.search(numpy.asarray([1,0],dtype=numpy.float32),1,threads=1)[0].key==1",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VectorIndexError(f"USearch HNSW health check could not run: {exc}") from exc
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise VectorIndexError(
                f"USearch {backend_version} failed its isolated native health check{suffix}"
            )
        _DEPS_VERIFIED = True
    try:
        import numpy
        from usearch.index import Index
    except ImportError as exc:
        raise VectorIndexError("HNSW indexing requires the optional `scale` dependency set") from exc
    return Index, numpy


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def corpus_signature(rows: list[dict[str, Any]], profile: dict[str, Any]) -> str:
    canonical = {
        "profile": {
            "model": profile["model"],
            "dimension": profile["dimension"],
            "kind": profile.get("kind"),
            "model_revision": profile.get("model_revision"),
            "model_sha256": profile.get("model_sha256"),
        },
        "chunks": [
            {
                "chunk_id": row["chunk_id"],
                "source_id": row["source_id"],
                "source_revision": row["source_revision"],
                "content_sha256": row["content_sha256"],
                "vector_sha256": "sha256:"
                + hashlib.sha256(
                    json.dumps(row["vector"], separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            for row in sorted(rows, key=lambda item: item["chunk_id"])
        ],
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class VectorIndexStore:
    def __init__(self, root: Path):
        self.root = root
        self.active_path = root / "active.yaml"

    def _active(self) -> dict[str, Any]:
        if not self.active_path.is_file():
            return {"kind": "atomlearn.vector-index-pointer", "schema_version": 1, "indexes": {}}
        value = read_data(self.active_path)
        if not isinstance(value, dict) or not isinstance(value.get("indexes"), dict):
            raise VectorIndexError("vector index active pointer is invalid")
        return value

    def _metadata(self, kind: str) -> tuple[Path, dict[str, Any]] | None:
        pointer = self._active().get("indexes", {}).get(kind)
        if not pointer:
            return None
        relative = pointer.get("metadata_path")
        if not isinstance(relative, str):
            raise VectorIndexError(f"{kind} vector index pointer has no metadata path")
        path = self.root / relative
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VectorIndexError(f"cannot read {kind} vector index metadata: {exc}") from exc
        if metadata.get("kind") != kind or metadata.get("status") != "ready":
            raise VectorIndexError(f"{kind} vector index metadata is not ready")
        return path, metadata

    def _next_generation(self, kind: str) -> int:
        destination = self.root / kind
        destination.mkdir(parents=True, exist_ok=True)
        values = [
            int(match.group(1))
            for item in destination.iterdir()
            if item.is_dir() and (match := re.fullmatch(r"g(\d{6})", item.name))
        ]
        return max(values, default=0) + 1

    @staticmethod
    def _profile_space_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
        fields = ["model", "dimension", "kind", "model_revision", "model_sha256"]
        return all(left.get(field) == right.get(field) for field in fields)

    @classmethod
    def _profile_matches(cls, left: dict[str, Any], right: dict[str, Any]) -> bool:
        return cls._profile_space_matches(left, right) and left.get("corpus_epoch") == right.get("corpus_epoch")

    def build(
        self,
        kind: str,
        profile: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        incremental: bool = True,
        tombstone_rebuild_ratio: float = 0.2,
    ) -> dict[str, Any]:
        if kind not in INDEX_KINDS:
            raise VectorIndexError(f"unsupported vector index kind: {kind}")
        if not rows:
            raise VectorIndexError(f"cannot build {kind} vector index without vectors")
        dimension = profile.get("dimension")
        if not isinstance(dimension, int) or not 1 <= dimension <= 8192:
            raise VectorIndexError("vector index profile has an invalid dimension")
        for row in rows:
            if len(row.get("vector", [])) != dimension:
                raise VectorIndexError(f"{row.get('chunk_id')} vector dimension disagrees with profile")
        Index, numpy = _deps()
        signature = corpus_signature(rows, profile)
        previous = self._metadata(kind) if incremental else None
        previous_path: Path | None = None
        previous_metadata: dict[str, Any] | None = None
        if previous:
            previous_path, previous_metadata = previous
            if not self._profile_space_matches(previous_metadata["profile"], profile):
                previous_path = None
                previous_metadata = None

        row_by_id = {row["chunk_id"]: row for row in rows}
        old_entries = {
            item["chunk_id"]: item for item in (previous_metadata or {}).get("entries", [])
        }
        removed = sorted(set(old_entries) - set(row_by_id))
        changed = sorted(
            chunk_id
            for chunk_id in set(old_entries) & set(row_by_id)
            if old_entries[chunk_id]["vector_sha256"]
            != "sha256:"
            + hashlib.sha256(
                json.dumps(row_by_id[chunk_id]["vector"], separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        added = sorted(set(row_by_id) - set(old_entries))
        inherited_tombstones = int((previous_metadata or {}).get("tombstones_inherited", 0))
        total_tombstones = inherited_tombstones + len(removed)
        tombstone_ratio = total_tombstones / max(1, len(old_entries) + inherited_tombstones)
        use_incremental = bool(
            previous_metadata
            and tombstone_ratio <= tombstone_rebuild_ratio
            and (removed or changed or added)
        )
        if (
            previous_metadata
            and previous_metadata.get("corpus_signature") == signature
            and (self.root / previous_metadata["index_path"]).is_file()
            and file_sha256(self.root / previous_metadata["index_path"])
            == previous_metadata.get("index_sha256")
        ):
            return {
                "kind": kind,
                "generation": previous_metadata["generation"],
                "build_mode": "unchanged",
                "chunks": len(rows),
                "corpus_signature": signature,
            }

        generation = self._next_generation(kind)
        directory = self.root / kind / f"g{generation:06d}"
        directory.mkdir(parents=False, exist_ok=False)
        index_path = directory / "index.bin"
        metadata_path = directory / "metadata.json"

        if use_incremental and previous_path and previous_metadata:
            previous_index = self.root / previous_metadata["index_path"]
            if file_sha256(previous_index) != previous_metadata["index_sha256"]:
                raise VectorIndexError("previous HNSW generation content hash is invalid")
            index = Index.restore(str(previous_index), view=False)
            if index is None:
                raise VectorIndexError("previous HNSW generation could not be restored")
            removed_labels = [old_entries[chunk_id]["label"] for chunk_id in removed]
            changed_labels = [old_entries[chunk_id]["label"] for chunk_id in changed]
            if removed_labels or changed_labels:
                index.remove(
                    numpy.asarray(removed_labels + changed_labels, dtype=numpy.uint64),
                    compact=False,
                    threads=1,
                )
            for chunk_id in changed:
                label = old_entries[chunk_id]["label"]
                index.add(
                    label,
                    numpy.asarray(row_by_id[chunk_id]["vector"], dtype=numpy.float32),
                    threads=1,
                )
            next_label = max((item["label"] for item in old_entries.values()), default=-1) + 1
            for chunk_id in added:
                index.add(
                    next_label,
                    numpy.asarray(row_by_id[chunk_id]["vector"], dtype=numpy.float32),
                    threads=1,
                )
                old_entries[chunk_id] = {"label": next_label}
                next_label += 1
            labels = {
                chunk_id: old_entries[chunk_id]["label"]
                for chunk_id in row_by_id
            }
            build_mode = "incremental"
        else:
            ordered_ids = sorted(row_by_id)
            labels = {chunk_id: label for label, chunk_id in enumerate(ordered_ids)}
            index = Index(
                ndim=dimension,
                metric="cos",
                dtype="f32",
                connectivity=16,
                expansion_add=200,
                expansion_search=max(50, min(400, len(rows))),
            )
            index.add(
                numpy.asarray([labels[chunk_id] for chunk_id in ordered_ids]),
                numpy.asarray(
                    [row_by_id[chunk_id]["vector"] for chunk_id in ordered_ids],
                    dtype=numpy.float32,
                ),
                threads=1,
            )
            build_mode = "full"
        index.expansion_search = max(50, min(400, len(rows)))
        index.save(str(index_path))
        relative_index = index_path.relative_to(self.root).as_posix()
        entries = []
        for chunk_id in sorted(row_by_id):
            row = row_by_id[chunk_id]
            vector_sha = "sha256:" + hashlib.sha256(
                json.dumps(row["vector"], separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            entries.append(
                {
                    "label": labels[chunk_id],
                    "chunk_id": chunk_id,
                    "source_id": row["source_id"],
                    "source_revision": row["source_revision"],
                    "content_sha256": row["content_sha256"],
                    "vector_sha256": vector_sha,
                }
            )
        metadata = {
            "schema_version": 1,
            "kind": kind,
            "status": "ready",
            "backend": "usearch",
            "generation": generation,
            "build_mode": build_mode,
            "created_at": iso(),
            "profile": profile,
            "corpus_signature": signature,
            "chunk_count": len(entries),
            "element_capacity": int(index.capacity),
            "tombstones_inherited": total_tombstones if use_incremental else 0,
            "index_path": relative_index,
            "index_sha256": file_sha256(index_path),
            "entries": entries,
        }
        atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

        verifier = Index.restore(str(index_path), view=False)
        if verifier is None:
            raise VectorIndexError("new HNSW generation could not be restored")
        verifier.expansion_search = max(50, min(400, len(rows)))
        sample = entries[: min(5, len(entries))]
        labels_found = [
            int(
                verifier.search(
                    numpy.asarray(row_by_id[item["chunk_id"]]["vector"], dtype=numpy.float32),
                    1,
                    threads=1,
                )[0].key
            )
            for item in sample
        ]
        if any(found != item["label"] for found, item in zip(labels_found, sample)):
            raise VectorIndexError("new HNSW generation failed self-retrieval verification")

        active = self._active()
        active.setdefault("indexes", {})[kind] = {
            "generation": generation,
            "metadata_path": metadata_path.relative_to(self.root).as_posix(),
            "activated_at": iso(),
        }
        write_yaml(self.active_path, active)
        return {
            "kind": kind,
            "generation": generation,
            "build_mode": build_mode,
            "chunks": len(entries),
            "added": len(added),
            "changed": len(changed),
            "tombstoned": len(removed),
            "corpus_signature": signature,
        }

    def status(
        self,
        kind: str,
        *,
        profile: dict[str, Any] | None = None,
        signature: str | None = None,
    ) -> dict[str, Any]:
        selected = self._metadata(kind)
        if selected is None:
            return {"kind": kind, "status": "missing"}
        metadata_path, metadata = selected
        index_path = self.root / metadata["index_path"]
        if not index_path.is_file() or file_sha256(index_path) != metadata.get("index_sha256"):
            return {"kind": kind, "status": "corrupt", "generation": metadata.get("generation")}
        stale = bool(
            (profile is not None and not self._profile_matches(metadata.get("profile", {}), profile))
            or (signature is not None and signature != metadata.get("corpus_signature"))
        )
        return {
            "kind": kind,
            "status": "stale" if stale else "ready",
            "generation": metadata["generation"],
            "build_mode": metadata["build_mode"],
            "chunks": metadata["chunk_count"],
            "profile": metadata["profile"],
            "corpus_signature": metadata["corpus_signature"],
            "metadata_path": metadata_path.relative_to(self.root).as_posix(),
        }

    def search(
        self,
        kind: str,
        profile: dict[str, Any],
        signature: str | None,
        query_vector: list[float],
        k: int,
        *,
        source_ids: set[str] | None = None,
    ) -> list[tuple[float, str]]:
        selected = self._metadata(kind)
        if selected is None:
            raise VectorIndexError(f"{kind} HNSW index is not built")
        _, metadata = selected
        if not self._profile_matches(metadata["profile"], profile) or (
            signature is not None and metadata["corpus_signature"] != signature
        ):
            raise VectorIndexError(f"{kind} HNSW index is stale")
        index_path = self.root / metadata["index_path"]
        if file_sha256(index_path) != metadata["index_sha256"]:
            raise VectorIndexError(f"{kind} HNSW index content hash is invalid")
        Index, numpy = _deps()
        allowed_entries = [
            item for item in metadata["entries"]
            if not source_ids or item["source_id"] in source_ids
        ]
        if not allowed_entries:
            return []
        allowed_labels = {item["label"] for item in allowed_entries}
        by_label = {item["label"]: item["chunk_id"] for item in metadata["entries"]}
        index = Index.restore(str(index_path), view=False)
        if index is None:
            raise VectorIndexError(f"{kind} HNSW index could not be restored")
        index.expansion_search = max(50, min(400, len(metadata["entries"])))
        total = len(metadata["entries"])
        query_count = min(total, max(k, k * 4))
        selected: list[tuple[float, str]] = []
        while True:
            matches = index.search(
                numpy.asarray(query_vector, dtype=numpy.float32),
                query_count,
                threads=1,
                exact=False,
            )
            selected = [
                (round(1.0 - float(match.distance), 8), by_label[int(match.key)])
                for match in matches
                if int(match.key) in allowed_labels
            ][:k]
            if len(selected) >= min(k, len(allowed_entries)) or query_count >= total:
                break
            query_count = min(total, query_count * 2)
        return selected

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            active = self._active()
            for kind in active.get("indexes", {}):
                if kind not in INDEX_KINDS:
                    errors.append(f"unknown active vector index kind: {kind}")
                    continue
                result = self.status(kind)
                if result["status"] == "corrupt":
                    errors.append(f"{kind} vector index generation is corrupt")
        except (OSError, VectorIndexError) as exc:
            errors.append(str(exc))
        return errors
