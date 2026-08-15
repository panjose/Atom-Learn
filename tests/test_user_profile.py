from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
RUN_ROOT = ROOT / ".test-workspaces"


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


def workspace(label: str, data_root: Path) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"profile-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            data_root,
            "init",
            path,
            "--course-id",
            f"profile.{label}",
            "--title",
            f"Profile {label}",
            "--goal",
            "Test opt-in cross-course personalization",
        )
    )
    return path


def write_payload(path: Path, name: str, value: dict) -> Path:
    target = path / name
    target.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def signal(dimension: str, value: str, *, evidence: str = "explicit", reason: str = "explicit_request") -> dict:
    return {
        "dimension": dimension,
        "value": value,
        "direction": "prefer",
        "evidence": evidence,
        "reason_code": reason,
        "confidence": 0.9,
        "turn_refs": ["turn-1"],
    }


def observe(
    data_root: Path,
    path: Path,
    session_id: str,
    signals: list[dict],
    revision: int,
    *,
    scope: str = "user",
    context: str = "teaching",
) -> dict:
    payload = write_payload(
        path,
        f"{session_id}.yaml",
        {"session_id": session_id, "context": context, "scope": scope, "signals": signals},
    )
    if scope == "user":
        return output(
            invoke(
                data_root,
                "adapt",
                "observe-session",
                path,
                "--input",
                payload,
                "--expected-profile-revision",
                revision,
            )
        )
    return output(
        invoke(
            data_root,
            "adapt",
            "observe-session",
            path,
            "--input",
            payload,
            "--expected-adaptation-revision",
            revision,
        )
    )


def test_global_profile_is_lazy_and_requires_explicit_enable(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("lazy", data_root)
    policy = output(invoke(data_root, "policy", "effective", path, "--context", "teaching"))
    assert policy["user_profile_revision"] is None
    assert all(item["source"] == "core_default" for item in policy["effective"].values())
    assert not data_root.exists()
    enabled = output(invoke(data_root, "profile", "enable", path))
    assert enabled["created"] is True
    assert enabled["profile_revision"] == 1
    assert (data_root / "profiles" / "default" / "state.yaml").is_file()
    assert (path / ".atomlearn" / "profile-binding.yaml").is_file()


def test_user_scope_signal_activates_and_reaches_status(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("explicit", data_root)
    output(invoke(data_root, "profile", "enable", path))
    observed = observe(data_root, path, "global-session-1", [signal("response.detail", "concise")], 1)
    assert observed["profile_revision"] == 2
    policy = output(invoke(data_root, "policy", "effective", path, "--context", "teaching"))
    assert policy["effective"]["response.detail"]["source"] == "user_global_explicit"
    status = output(invoke(data_root, "status", path, "--json"))
    assert status["effective_policy"]["effective"]["response.detail"]["value"] == "concise"
    assert status["adaptation"]["active_preferences"][0]["source"] == "user_global_explicit"
    assert output(invoke(data_root, "validate", path))["ok"] is True


def test_workspace_and_current_turn_precedence_are_explainable(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    first = workspace("precedence-a", data_root)
    second = workspace("precedence-b", data_root)
    output(invoke(data_root, "profile", "enable", first))
    output(invoke(data_root, "profile", "enable", second, "--expected-profile-revision", 1))
    observe(data_root, first, "global-precedence", [signal("explanation.order", "example_first")], 1)
    observe(data_root, first, "workspace-precedence", [signal("explanation.order", "formal_first")], 0, scope="workspace")
    first_policy = output(invoke(data_root, "policy", "effective", first, "--context", "teaching"))
    second_policy = output(invoke(data_root, "policy", "effective", second, "--context", "teaching"))
    assert first_policy["effective"]["explanation.order"]["source"] == "workspace_explicit"
    assert second_policy["effective"]["explanation.order"]["source"] == "user_global_explicit"
    overrides = write_payload(first, "overrides.yaml", {"explanation.order": "mixed"})
    current = output(
        invoke(data_root, "policy", "effective", first, "--context", "teaching", "--overrides", overrides)
    )
    assert current["effective"]["explanation.order"]["source"] == "current_turn"
    reasons = {item["reason"] for item in current["ignored"] if item["dimension"] == "explanation.order"}
    assert reasons == {"overridden_by_current_turn"}


def test_global_inference_needs_distinct_sessions_and_context_filtering(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("inference", data_root)
    output(invoke(data_root, "profile", "enable", path))
    inferred = signal("research.orientation", "evidence_first", evidence="behavioral", reason="repeated_request")
    observe(data_root, path, "research-1", [inferred], 1, context="research")
    profile = output(invoke(data_root, "profile", "show", path))
    assert profile["preferences"]["research.orientation"]["status"] == "provisional"
    observe(data_root, path, "research-2", [inferred], 2, context="research")
    profile = output(invoke(data_root, "profile", "show", path))
    assert profile["preferences"]["research.orientation"]["status"] == "active"
    research = output(invoke(data_root, "policy", "effective", path, "--context", "research"))
    teaching = output(invoke(data_root, "policy", "effective", path, "--context", "teaching"))
    assert research["effective"]["research.orientation"]["source"] == "user_global_inferred"
    assert "research.orientation" not in teaching["effective"]
    assert any(item["reason"] == "context_not_allowed" for item in teaching["ignored"])


def test_privacy_unknown_fields_duplicate_sessions_and_stale_revision_fail_closed(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("privacy", data_root)
    output(invoke(data_root, "profile", "enable", path))
    raw = write_payload(
        path,
        "raw-global.yaml",
        {
            "session_id": "global-private",
            "context": "teaching",
            "scope": "user",
            "raw_messages": ["private"],
            "signals": [signal("response.detail", "concise")],
        },
    )
    blocked = invoke(data_root, "adapt", "observe-session", path, "--input", raw, check=False)
    assert blocked.returncode == 2
    assert "never pass raw messages" in blocked.stderr
    observe(data_root, path, "global-valid", [signal("response.detail", "concise")], 1)
    duplicate = invoke(
        data_root,
        "adapt",
        "observe-session",
        path,
        "--input",
        path / "global-valid.yaml",
        "--expected-profile-revision",
        2,
        check=False,
    )
    assert duplicate.returncode == 2
    assert "already been observed" in duplicate.stderr
    stale = invoke(
        data_root,
        "profile",
        "retire",
        path,
        "response.detail",
        "--reason-code",
        "user_rejection",
        "--expected-profile-revision",
        1,
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale user profile revision" in stale.stderr
    stored = (data_root / "profiles" / "default" / "signals.ndjson").read_text(encoding="utf-8")
    assert "private" not in stored


def test_disable_promote_export_and_reset_are_auditable(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("lifecycle", data_root)
    output(invoke(data_root, "profile", "enable", path))
    observe(data_root, path, "local-explicit", [signal("feedback.style", "direct")], 0, scope="workspace")
    promoted = output(
        invoke(
            data_root,
            "profile",
            "promote-preference",
            path,
            "feedback.style",
            "--expected-profile-revision",
            1,
        )
    )
    assert promoted["profile_revision"] == 2
    export = tmp_path / "profile-export.yaml"
    output(invoke(data_root, "profile", "export", path, "--output", export))
    assert export.is_file()
    overwrite = invoke(data_root, "profile", "export", path, "--output", export, check=False)
    assert overwrite.returncode == 2
    disabled = output(invoke(data_root, "profile", "disable", path, "--expected-binding-revision", 0))
    assert disabled["binding"]["enabled"] is False
    policy = output(invoke(data_root, "policy", "effective", path, "--context", "teaching"))
    assert policy["effective"]["feedback.style"]["source"] == "workspace_explicit"
    output(invoke(data_root, "profile", "enable", path, "--expected-profile-revision", 2, "--expected-binding-revision", 1))
    reset = output(
        invoke(data_root, "profile", "reset", path, "--expected-profile-revision", 2, "--confirmed")
    )
    assert reset["retired_dimensions"] == ["feedback.style"]
    state = yaml.safe_load((data_root / "profiles" / "default" / "state.yaml").read_text(encoding="utf-8"))
    assert state["global_enabled"] is False
    assert state["preferences"]["feedback.style"]["status"] == "retired"
    ledger = (data_root / "profiles" / "default" / "ledger.ndjson").read_text(encoding="utf-8")
    assert "user_profile.reset" in ledger


def test_protected_invariants_cannot_enter_current_turn_overrides(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("invariant", data_root)
    override = write_payload(path, "bad-overrides.yaml", {"mastery.threshold": "lower"})
    blocked = invoke(
        data_root,
        "policy",
        "effective",
        path,
        "--context",
        "teaching",
        "--overrides",
        override,
        check=False,
    )
    assert blocked.returncode == 2
    assert "protected invariants cannot be overridden" in blocked.stderr


def test_tampered_global_profile_event_fails_workspace_validation(tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    path = workspace("tamper", data_root)
    output(invoke(data_root, "profile", "enable", path))
    ledger_path = data_root / "profiles" / "default" / "ledger.ndjson"
    event = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    event["details"]["raw_message"] = "must not survive"
    ledger_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    blocked = invoke(data_root, "validate", path, check=False)
    assert blocked.returncode == 2
    assert "user profile event 1 details are invalid" in blocked.stderr
