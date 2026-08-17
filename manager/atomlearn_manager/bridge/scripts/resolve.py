"""Resolve the signed AtomLearn Core through this bridge's bound Manager root."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    bridge = Path(__file__).resolve().parents[1]
    marker_path = bridge / ".atomlearn-bridge.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"AtomLearn bridge marker is unreadable: {exc}", file=sys.stderr)
        return 2
    manager_root = Path(str(marker.get("manager_root", "")))
    if (
        marker.get("kind") != "atomlearn.codex-bridge"
        or marker.get("owner") != "atomlearn-manager"
        or marker.get("schema_version") != 2
        or not manager_root.is_absolute()
    ):
        print("AtomLearn bridge marker is missing its Manager ownership binding", file=sys.stderr)
        return 2
    executable = shutil.which("atomlearn-manager")
    if executable is None:
        print("atomlearn-manager is not available on PATH", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [executable, "--manager-root", str(manager_root), "codex", "resolve", "--json"],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
