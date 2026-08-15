from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import copy
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
RUN_ROOT = ROOT / ".test-workspaces"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
CAPSULE_FIXTURE = ROOT / "tests" / "fixtures" / "self_evolution_v2" / "evolution-capsule.json"
ATTACK_FIXTURE = ROOT / "tests" / "fixtures" / "security" / "capsule-attacks.json"


def invoke(data_root: Path, *args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["ATOMLEARN_DATA_DIR"] = str(data_root.resolve())
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def output(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def write_yaml(path: Path, name: str, value: dict) -> Path:
    target = path / name
    target.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def capsule_workspace(tmp_path: Path) -> tuple[Path, Path, str]:
    data_root = (tmp_path / "user-data").resolve()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = RUN_ROOT / f"capsule-{uuid.uuid4().hex}"
    output(invoke(data_root, "init", workspace, "--course-id", "capsule.test", "--title", "Capsule test"))
    imported = output(invoke(data_root, "import-plan", workspace, "--input", PLAN, "--expected-revision", 0))
    activated = output(
        invoke(
            data_root,
            "activate",
            workspace,
            "calculus.limit.approach",
            "--expected-revision",
            imported["revision"],
        )
    )
    course_revision = activated["revision"]
    for index in range(3):
        evidence = write_yaml(
            workspace,
            f"capsule-evidence-{index}.yaml",
            {
                "atom_id": "calculus.limit.approach",
                "kind": "mastery_check",
                "prompt": "A local prompt that must never enter the Capsule.",
                "response_summary": "A private response summary.",
                "scores": {"explain": 0.4, "discriminate": 0.4},
                "feedback": "Needs remediation.",
                "rationale": "Below the mastery threshold.",
            },
        )
        recorded = output(
            invoke(
                data_root,
                "record-evidence",
                workspace,
                "--input",
                evidence,
                "--expected-revision",
                course_revision,
            )
        )
        assessed = output(
            invoke(
                data_root,
                "assess",
                workspace,
                "calculus.limit.approach",
                "--evidence-id",
                recorded["evidence_id"],
                "--expected-revision",
                recorded["revision"],
            )
        )
        assert assessed["result"] == "not_mastered"
        course_revision = assessed["revision"]
    analyzed = output(
        invoke(
            data_root,
            "evolve",
            "analyze",
            workspace,
            "--propose",
            "--expected-evolution-revision",
            0,
        )
    )
    proposal_id = analyzed["result"]["created_proposal_ids"][0]
    return data_root, workspace, proposal_id


def test_build_preview_and_one_time_local_export_keep_identical_hash(tmp_path: Path) -> None:
    data_root, workspace, proposal_id = capsule_workspace(tmp_path)
    built = output(
        invoke(data_root, "evolve", "capsule", "build", workspace, "--proposal", proposal_id)
    )
    capsule_path = Path(built["capsule_path"])
    capsule_text = capsule_path.read_text(encoding="utf-8")
    assert str(workspace) not in capsule_text
    assert "calculus.limit.approach" not in capsule_text
    assert "private response" not in capsule_text.lower()
    assert built["capsule"]["metrics"]["occurrence_bucket"] == "3_to_5"
    assert Path(built["lint_receipt"]).is_file()

    blocked = invoke(
        data_root,
        "evolve",
        "capsule",
        "export",
        capsule_path,
        "--output",
        tmp_path / "before-preview.json",
        "--confirmed",
        check=False,
    )
    assert blocked.returncode == 2
    assert "preview" in blocked.stderr.lower()

    preview = output(invoke(data_root, "evolve", "capsule", "preview", capsule_path))
    assert preview["uploaded"] is False
    assert preview["capsule_hash"] == built["capsule_hash"]
    assert "complete local" not in preview["markdown"].lower()
    exported_path = (tmp_path / "exported-capsule.json").resolve()
    exported = output(
        invoke(
            data_root,
            "evolve",
            "capsule",
            "export",
            capsule_path,
            "--output",
            exported_path,
            "--confirmed",
        )
    )
    assert exported["uploaded"] is False
    assert exported["capsule_hash"] == preview["capsule_hash"] == built["capsule_hash"]
    assert json.loads(exported_path.read_text(encoding="utf-8")) == built["capsule"]
    second_export = invoke(
        data_root,
        "evolve",
        "capsule",
        "export",
        capsule_path,
        "--output",
        tmp_path / "second-export.json",
        "--confirmed",
        check=False,
    )
    assert second_export.returncode == 2
    assert "already been exported" in second_export.stderr

    rebuilt = output(
        invoke(data_root, "evolve", "capsule", "build", workspace, "--proposal", proposal_id)
    )
    assert rebuilt["capsule"]["capsule_id"] != built["capsule"]["capsule_id"]


def test_privacy_lint_rejects_content_identifiers_and_small_samples(tmp_path: Path) -> None:
    data_root = (tmp_path / "user-data").resolve()
    base = json.loads(CAPSULE_FIXTURE.read_text(encoding="utf-8"))
    attacks = json.loads(ATTACK_FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert {item["name"] for item in attacks} >= {
        "raw_free_text",
        "nested_free_text",
        "windows_path",
        "posix_path",
        "source_url",
        "source_doi",
        "email",
        "uuid",
        "precise_timestamp",
        "atom_identifier",
    }
    for attack in attacks:
        payload = copy.deepcopy(base)
        target = payload
        for key in attack["path"][:-1]:
            target = target[key]
        target[attack["path"][-1]] = attack["value"]
        path = tmp_path / f"attack-{attack['name']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = invoke(data_root, "evolve", "capsule", "lint", path, check=False)
        assert result.returncode == 2
        assert "privacy lint failed" in result.stderr
        assert not Path(str(path) + ".lint.json").exists()

    too_small = dict(base)
    too_small["metrics"] = {**base["metrics"], "occurrence_bucket": "2"}
    small_path = tmp_path / "too-small.json"
    small_path.write_text(json.dumps(too_small), encoding="utf-8")
    result = invoke(data_root, "evolve", "capsule", "lint", small_path, check=False)
    assert result.returncode == 2
    assert "too small" in result.stderr


def test_maintainer_dedup_and_fixture_conversion_never_auto_patch(tmp_path: Path) -> None:
    data_root = (tmp_path / "user-data").resolve()
    first = json.loads(CAPSULE_FIXTURE.read_text(encoding="utf-8"))
    second = {**first, "capsule_id": "cap-fedcba9876543210fedcba9876543210"}
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")
    store = (tmp_path / "maintainer-store").resolve()
    ingested = output(
        invoke(data_root, "evolve", "capsule", "maintainer-ingest", first_path, "--store", store)
    )
    duplicate = output(
        invoke(data_root, "evolve", "capsule", "maintainer-ingest", second_path, "--store", store)
    )
    assert ingested["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert ingested["fingerprint"] == duplicate["fingerprint"]
    assert duplicate["triage"]["duplicate_count"] == 2
    assert duplicate["triage"]["automatic_code_change"] is False

    fixture_path = tmp_path / "failure-fixture.yaml"
    converted = output(
        invoke(
            data_root,
            "evolve",
            "capsule",
            "fixture-convert",
            first_path,
            "--output",
            fixture_path,
            "--confirmed",
        )
    )
    assert converted["fixture"]["status"] == "needs_reproduction"
    assert converted["fixture"]["requires_reproduction_test"] is True
    assert converted["fixture"]["automatic_patch_allowed"] is False
    assert fixture_path.is_file()


def test_capsule_cli_has_no_submit_or_telemetry_surface() -> None:
    source = (ROOT / "atom-learn" / "scripts" / "capsule.py").read_text(encoding="utf-8")
    assert "sub.add_parser(\"submit\"" not in source
    assert "requests" not in source
    assert "urllib" not in source
    assert "socket" not in source
