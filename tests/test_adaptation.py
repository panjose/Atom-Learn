from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
RUN_ROOT = ROOT / ".test-workspaces"


def invoke(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
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


def payload(path: Path, name: str, data: dict) -> Path:
    destination = path / name
    destination.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destination


def workspace(label: str, *, import_plan: bool = False) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"adaptation-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"adaptation.{label}",
            "--title",
            f"Adaptation {label}",
            "--goal",
            "Adapt to the learner without weakening learning guards",
        )
    )
    if import_plan:
        output(invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0))
    return path


def observe(path: Path, session_id: str, signals: list[dict], revision: int, context: str = "teaching") -> dict:
    source = payload(
        path,
        f"{session_id}.yaml",
        {"session_id": session_id, "context": context, "signals": signals},
    )
    return output(
        invoke(
            "adapt",
            "observe-session",
            path,
            "--input",
            source,
            "--expected-adaptation-revision",
            revision,
        )
    )


def signal(
    dimension: str,
    value: str,
    *,
    evidence: str = "explicit",
    reason_code: str = "explicit_request",
    direction: str = "prefer",
    confidence: float = 0.9,
) -> dict:
    return {
        "dimension": dimension,
        "value": value,
        "direction": direction,
        "evidence": evidence,
        "reason_code": reason_code,
        "confidence": confidence,
        "turn_refs": ["turn-1"],
    }


def test_explicit_session_preference_activates_immediately_and_reaches_course_status() -> None:
    path = workspace("explicit")
    observed = observe(path, "session-1", [signal("response.detail", "concise")], 0)
    assert observed["adaptation_revision"] == 1
    assert observed["result"]["changed_dimensions"] == ["response.detail"]
    guidance = observed["result"]["guidance"]
    assert guidance["active_preferences"][0]["source"] == "explicit"
    assert "concise" in guidance["instructions"][0].lower()
    status = output(invoke("status", path, "--json"))
    assert status["adaptation"]["active_preferences"][0]["value"] == "concise"
    assert (path / "PERSONALIZATION.md").is_file()
    assert output(invoke("validate", path))["ok"] is True


def test_behavioral_preference_requires_corroboration_across_distinct_sessions() -> None:
    path = workspace("behavioral")
    first = observe(
        path,
        "session-a",
        [
            signal(
                "example.mode",
                "code",
                evidence="behavioral",
                reason_code="repeated_request",
                confidence=0.9,
            )
        ],
        0,
    )
    assert first["result"]["guidance"]["active_preferences"] == []
    profile = output(invoke("adapt", "profile", path))
    assert profile["preferences"]["example.mode"]["status"] == "provisional"
    second = observe(
        path,
        "session-b",
        [
            signal(
                "example.mode",
                "code",
                evidence="behavioral",
                reason_code="accepted_format",
                confidence=0.85,
            )
        ],
        1,
    )
    preference = output(invoke("adapt", "profile", path))["preferences"]["example.mode"]
    assert preference["status"] == "active"
    assert preference["active_value"] == "code"
    assert preference["session_count"] == 2
    assert second["result"]["changed_dimensions"] == ["example.mode"]
    duplicate = invoke(
        "adapt",
        "observe-session",
        path,
        "--input",
        path / "session-b.yaml",
        "--expected-adaptation-revision",
        2,
        check=False,
    )
    assert duplicate.returncode == 2
    assert "already been observed" in duplicate.stderr

    rejected = observe(
        path,
        "session-c",
        [signal("example.mode", "code", direction="avoid", reason_code="user_rejection")],
        2,
    )
    assert rejected["result"]["guidance"]["active_preferences"] == []
    preference = output(invoke("adapt", "profile", path))["preferences"]["example.mode"]
    assert preference["status"] == "provisional"
    assert preference["candidates"]["code"]["score"] == 0.0


def test_new_explicit_correction_overrides_profile_and_retirement_is_reversible() -> None:
    path = workspace("correction")
    observe(path, "session-1", [signal("response.detail", "concise")], 0)
    corrected = observe(
        path,
        "session-2",
        [signal("response.detail", "detailed", reason_code="user_correction", confidence=1.0)],
        1,
    )
    assert corrected["result"]["guidance"]["active_preferences"][0]["value"] == "detailed"
    retired = output(
        invoke(
            "adapt",
            "retire",
            path,
            "response.detail",
            "--reason-code",
            "privacy_request",
            "--expected-adaptation-revision",
            2,
        )
    )
    assert retired["result"]["status"] == "retired"
    assert output(invoke("adapt", "profile", path))["preferences"]["response.detail"]["status"] == "retired"
    reactivated = observe(path, "session-3", [signal("response.detail", "balanced")], 3)
    active = reactivated["result"]["guidance"]["active_preferences"][0]
    assert active["value"] == "balanced"
    assert active["source"] == "explicit"


def test_context_specific_research_preference_does_not_leak_into_teaching() -> None:
    path = workspace("context")
    observe(
        path,
        "research-session",
        [signal("research.orientation", "evidence_first")],
        0,
        context="research",
    )
    research = output(invoke("adapt", "guidance", path, "--context", "research"))
    teaching = output(invoke("adapt", "guidance", path, "--context", "teaching"))
    assert research["active_preferences"][0]["dimension"] == "research.orientation"
    assert teaching["active_preferences"] == []


def test_raw_chat_sensitive_dimensions_and_stale_revisions_are_rejected() -> None:
    path = workspace("privacy")
    raw = payload(
        path,
        "raw.yaml",
        {
            "session_id": "session-raw",
            "context": "teaching",
            "raw_messages": ["Store this complete private conversation."],
            "signals": [signal("response.detail", "concise")],
        },
    )
    blocked = invoke("adapt", "observe-session", path, "--input", raw, check=False)
    assert blocked.returncode == 2
    assert "never pass raw messages" in blocked.stderr
    sensitive = payload(
        path,
        "sensitive.yaml",
        {
            "session_id": "session-sensitive",
            "context": "general",
            "signals": [signal("political.identity", "unknown")],
        },
    )
    blocked = invoke("adapt", "observe-session", path, "--input", sensitive, check=False)
    assert blocked.returncode == 2
    assert "dimension is unsupported" in blocked.stderr
    observed = observe(path, "session-valid", [signal("language.mode", "match_user")], 0)
    stale = invoke(
        "adapt",
        "retire",
        path,
        "language.mode",
        "--reason-code",
        "user_rejection",
        "--expected-adaptation-revision",
        0,
        check=False,
    )
    assert observed["adaptation_revision"] == 1
    assert stale.returncode == 2
    assert "Stale adaptation revision" in stale.stderr
    stored = (path / ".atomlearn" / "adaptation" / "signals.ndjson").read_text(encoding="utf-8")
    assert "private conversation" not in stored


def test_tampered_state_and_audit_ledger_fail_closed() -> None:
    path = workspace("tamper")
    observe(path, "session-1", [signal("response.detail", "concise")], 0)
    state_path = path / ".atomlearn" / "adaptation" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["raw_session_summary"] = "must not survive validation"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    blocked = invoke("adapt", "validate", path, check=False)
    assert blocked.returncode == 2
    assert "state fields are invalid" in blocked.stderr

    state.pop("raw_session_summary")
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    ledger_path = path / ".atomlearn" / "adaptation" / "ledger.ndjson"
    event = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    event["details"]["raw_message"] = "must not survive validation"
    ledger_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    blocked = invoke("validate", path, check=False)
    assert blocked.returncode == 2
    assert "session event details are invalid" in blocked.stderr


def test_evolution_reports_session_adaptation_without_sharing_its_revision() -> None:
    path = workspace("evolution-integration", import_plan=True)
    observe(path, "session-1", [signal("feedback.style", "direct")], 0)
    evolution = output(invoke("evolve", "status", path, "--json"))
    assert evolution["evolution_revision"] == 0
    assert evolution["session_adaptation"]["adaptation_revision"] == 1
    assert evolution["session_adaptation"]["active_preferences"]["feedback.style"] == "direct"
    assert output(invoke("evolve", "validate", path))["ok"] is True
