#!/usr/bin/env python3
"""Opt-in local learned embedding and cross-encoder adapters."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable


UNSAFE_WEIGHT_SUFFIXES = {".bin", ".pt", ".pth", ".pkl", ".pickle"}
SAFE_WEIGHT_SUFFIXES = {".safetensors", ".onnx", ".xml"}
MODEL_ROLES = {"embedding", "cross_encoder"}
BACKENDS = {"torch", "onnx", "openvino"}
_MODEL_CACHE: dict[tuple[str, str, str, str], Any] = {}


class SemanticAdapterError(RuntimeError):
    """An opt-in local semantic model is unavailable or unsafe to load."""


def _text(value: Any, label: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticAdapterError(f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > limit:
        raise SemanticAdapterError(f"{label} must be at most {limit} characters")
    return result


def _model_tree(path_value: Any, backend: str = "torch") -> tuple[Path, str, int]:
    raw = _text(path_value, "model.path", 4000)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SemanticAdapterError("model.path must be absolute so opt-in model identity is unambiguous")
    root = path.resolve()
    if not root.is_dir():
        raise SemanticAdapterError(f"model.path is not a directory: {root}")
    if path.is_symlink():
        raise SemanticAdapterError("model.path cannot be a symbolic link")
    files = sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix())
    if not files:
        raise SemanticAdapterError("model.path contains no files")
    if any(item.is_symlink() for item in root.rglob("*")):
        raise SemanticAdapterError("local model directories cannot contain symbolic links")
    unsafe = [
        item.name
        for item in files
        if item.suffix.casefold() in UNSAFE_WEIGHT_SUFFIXES
        and not (
            backend == "openvino"
            and item.suffix.casefold() == ".bin"
            and item.with_suffix(".xml").is_file()
        )
    ]
    if unsafe:
        raise SemanticAdapterError(
            "local model contains pickle-capable weight files; use safetensors, ONNX, or OpenVINO: "
            + ", ".join(unsafe[:5])
        )
    if not any(item.suffix.casefold() in SAFE_WEIGHT_SUFFIXES for item in files):
        raise SemanticAdapterError("local model must contain safetensors, ONNX, or OpenVINO weights")
    modules_path = root / "modules.json"
    if modules_path.is_file():
        try:
            modules = json.loads(modules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticAdapterError(f"invalid local model modules.json: {exc}") from exc
        if not isinstance(modules, list):
            raise SemanticAdapterError("local model modules.json must contain a list")
        custom = [
            str(item.get("type"))
            for item in modules
            if not isinstance(item, dict)
            or not isinstance(item.get("type"), str)
            or not item["type"].startswith("sentence_transformers.")
        ]
        if custom:
            raise SemanticAdapterError(
                "local model modules must use installed sentence_transformers classes; custom code is disabled"
            )
    digest = hashlib.sha256()
    total = 0
    for item in files:
        relative = item.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
                total += len(block)
    return root, "sha256:" + digest.hexdigest(), total


def normalize_model_profile(payload: Any, role: str) -> dict[str, Any]:
    if role not in MODEL_ROLES:
        raise SemanticAdapterError(f"unsupported semantic model role: {role}")
    if not isinstance(payload, dict):
        raise SemanticAdapterError("semantic model profile must be a mapping")
    allowed = {"model_id", "revision", "license", "path", "backend", "batch_size"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise SemanticAdapterError("semantic model profile contains unsupported fields: " + ", ".join(unexpected))
    backend = payload.get("backend", "torch")
    if backend not in BACKENDS:
        raise SemanticAdapterError("model.backend must be torch, onnx, or openvino")
    batch_size = payload.get("batch_size", 16)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 256:
        raise SemanticAdapterError("model.batch_size must be an integer from 1 through 256")
    path, digest, size = _model_tree(payload.get("path"), backend)
    return {
        "kind": "learned_local",
        "role": role,
        "model": _text(payload.get("model_id"), "model.model_id", 500),
        "model_revision": _text(payload.get("revision"), "model.revision", 500),
        "license": _text(payload.get("license"), "model.license", 500),
        "model_path": str(path),
        "model_sha256": digest,
        "model_bytes": size,
        "backend": backend,
        "batch_size": batch_size,
        "network_access": False,
        "trust_remote_code": False,
    }


def verify_model_profile(profile: dict[str, Any]) -> None:
    path, digest, size = _model_tree(
        profile.get("model_path"), str(profile.get("backend", "torch"))
    )
    if str(path) != profile.get("model_path") or digest != profile.get("model_sha256"):
        raise SemanticAdapterError("local semantic model content or path changed after approval")
    if size != profile.get("model_bytes"):
        raise SemanticAdapterError("local semantic model size changed after approval")


def _vectors(value: Any, label: str) -> list[list[float]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        raise SemanticAdapterError(f"{label} did not return a vector list")
    if value and isinstance(value[0], (int, float)):
        value = [value]
    result: list[list[float]] = []
    dimension: int | None = None
    for index, vector in enumerate(value):
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if not isinstance(vector, list) or not vector:
            raise SemanticAdapterError(f"{label}[{index}] is not a vector")
        normalized: list[float] = []
        for component in vector:
            if isinstance(component, bool) or not isinstance(component, (int, float)) or not math.isfinite(float(component)):
                raise SemanticAdapterError(f"{label}[{index}] contains a non-finite component")
            normalized.append(float(component))
        norm = math.sqrt(sum(component * component for component in normalized))
        if norm == 0:
            raise SemanticAdapterError(f"{label}[{index}] is a zero vector")
        normalized = [component / norm for component in normalized]
        dimension = dimension or len(normalized)
        if len(normalized) != dimension:
            raise SemanticAdapterError(f"{label} returned inconsistent dimensions")
        result.append(normalized)
    return result


def _embedding_model(
    profile: dict[str, Any],
    factory: Callable[[dict[str, Any]], Any] | None,
) -> Any:
    verify_model_profile(profile)
    if factory is not None:
        return factory(profile)
    key = ("embedding", profile["model_path"], profile["model_sha256"], profile["backend"])
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SemanticAdapterError(
            "local learned embeddings require the optional `semantic` dependency set"
        ) from exc
    model = SentenceTransformer(
        profile["model_path"],
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
        token=False,
        backend=profile["backend"],
    )
    _MODEL_CACHE[key] = model
    return model


def encode_documents(
    profile: dict[str, Any],
    texts: list[str],
    *,
    factory: Callable[[dict[str, Any]], Any] | None = None,
) -> list[list[float]]:
    if not texts or not all(isinstance(text, str) and text for text in texts):
        raise SemanticAdapterError("document embedding input must contain non-empty strings")
    model = _embedding_model(profile, factory)
    method = getattr(model, "encode_document", None) or getattr(model, "encode", None)
    if method is None:
        raise SemanticAdapterError("local embedding model has no document encoder")
    return _vectors(
        method(
            texts,
            batch_size=profile["batch_size"],
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
        "document embeddings",
    )


def encode_query(
    profile: dict[str, Any],
    text: str,
    *,
    factory: Callable[[dict[str, Any]], Any] | None = None,
) -> list[float]:
    if not isinstance(text, str) or not text.strip():
        raise SemanticAdapterError("query embedding input must be non-empty")
    model = _embedding_model(profile, factory)
    method = getattr(model, "encode_query", None) or getattr(model, "encode", None)
    if method is None:
        raise SemanticAdapterError("local embedding model has no query encoder")
    return _vectors(
        method(
            [text],
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
        "query embedding",
    )[0]


def _cross_encoder_model(
    profile: dict[str, Any],
    factory: Callable[[dict[str, Any]], Any] | None,
) -> Any:
    verify_model_profile(profile)
    if factory is not None:
        return factory(profile)
    key = ("cross_encoder", profile["model_path"], profile["model_sha256"], profile["backend"])
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise SemanticAdapterError(
            "local cross-encoder reranking requires the optional `semantic` dependency set"
        ) from exc
    model = CrossEncoder(
        profile["model_path"],
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
        token=False,
        backend=profile["backend"],
    )
    _MODEL_CACHE[key] = model
    return model


def cross_encoder_scores(
    profile: dict[str, Any],
    query: str,
    documents: list[str],
    *,
    factory: Callable[[dict[str, Any]], Any] | None = None,
) -> list[float]:
    if not documents:
        return []
    model = _cross_encoder_model(profile, factory)
    raw = model.predict(
        [(query, document) for document in documents],
        batch_size=profile["batch_size"],
        show_progress_bar=False,
    )
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if not isinstance(raw, list) or len(raw) != len(documents):
        raise SemanticAdapterError("cross-encoder returned the wrong number of scores")
    scores: list[float] = []
    for value in raw:
        if isinstance(value, list):
            if len(value) != 1:
                raise SemanticAdapterError("cross-encoder must emit one relevance score per pair")
            value = value[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SemanticAdapterError("cross-encoder returned a non-finite relevance score")
        scores.append(float(value))
    return scores
