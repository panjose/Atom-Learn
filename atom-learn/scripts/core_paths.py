"""Resolve the Skill tree both from source and from the isolated runtime wheel."""

from __future__ import annotations

from pathlib import Path


def resolve_core_root() -> Path:
    source = Path(__file__).resolve().parents[1]
    if (source / "assets" / "core-manifest.yaml").is_file():
        return source
    try:
        import atomlearn_assets
    except ImportError as exc:  # pragma: no cover - exercised by isolated runtime smoke
        raise RuntimeError("Installed AtomLearn runtime is missing its read-only asset package") from exc
    installed = Path(atomlearn_assets.__file__).resolve().parent
    if not (installed / "assets" / "core-manifest.yaml").is_file():
        raise RuntimeError("Installed AtomLearn runtime asset package is incomplete")
    return installed


CORE_ROOT = resolve_core_root()
