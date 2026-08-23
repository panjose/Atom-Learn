from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
if str(RELEASE) not in sys.path:
    sys.path.insert(0, str(RELEASE))

import open_source_gate


def test_open_source_readiness_gate_passes_current_tracked_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(RELEASE / "open_source_gate.py"), "--skip-history", "--json"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["history_scanned"] is False
    assert report["tracked_files_scanned"] > 100


def test_secret_detector_reports_only_labels() -> None:
    synthetic = b"prefix ghp_" + (b"A" * 30) + b" suffix"
    assert open_source_gate.secret_labels(synthetic) == ["GitHub classic token"]


def test_private_and_credential_shaped_paths_fail_closed() -> None:
    assert open_source_gate.forbidden_path(".private/release-key.txt")
    assert open_source_gate.forbidden_path("archive/credentials.json")
    assert open_source_gate.forbidden_path("certificate/signing.pem")
    assert open_source_gate.forbidden_path("courseware/.atomlearn/CURRENT.md")
    assert not open_source_gate.forbidden_path("tests/fixtures/public-example.json")
