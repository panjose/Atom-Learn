from __future__ import annotations

import copy
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


def workspace(label: str) -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"exam-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"exam.{label}",
            "--title",
            f"Exam {label}",
            "--goal",
            "Prepare from a source-grounded past-paper corpus",
        )
    )
    output(invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0))
    output(invoke("exam", "init", path, "--title", "Calculus final", "--target-date", "2099-01-10"))
    return path


def difficulty(level: int, *, official: bool = False) -> dict:
    return {
        "basis": "official" if official else "rubric",
        "conceptual_load": level,
        "reasoning_depth": level,
        "knowledge_integration": level,
        "execution_load": level,
        "time_pressure": level,
        "confidence": 0.9,
        "official_level": float(level) if official else None,
    }


def mapping(point_id: str, label: str, atom_id: str | None, weight: float = 1.0) -> dict:
    return {
        "id": point_id,
        "label": label,
        "atom_id": atom_id,
        "weight": weight,
        "confidence": 0.9,
        "basis": "direct",
    }


def question(
    question_id: str,
    paper_id: str,
    number: str,
    points: float,
    level: int,
    mappings: list[dict],
) -> dict:
    return {
        "id": question_id,
        "paper_id": paper_id,
        "number": number,
        "type": "calculation",
        "points": points,
        "stem_summary": f"Structured calculus task {number}",
        "source_locator": f"question {number}",
        "family_id": "derivative-calculation" if "derivative" in question_id else None,
        "cognitive_levels": ["apply", "analyze"] if level >= 3 else ["apply"],
        "tags": ["past-paper"],
        "difficulty": difficulty(level),
        "knowledge_points": mappings,
    }


def corpus() -> dict:
    papers = [
        {
            "id": f"paper-{year}",
            "title": f"Calculus {year}",
            "year": year,
            "session": "annual",
            "kind": "official_past_exam",
            "total_points": 100,
            "source_id": f"source-{year}",
            "locator": f"pages for {year}",
        }
        for year in [2023, 2024, 2025]
    ]
    derivative = mapping(
        "derivative.definition",
        "Derivative definition",
        "calculus.derivative.definition",
    )
    return {
        "papers": papers,
        "questions": [
            question("paper-2023.derivative", "paper-2023", "1", 20, 2, [copy.deepcopy(derivative)]),
            question("paper-2024.derivative", "paper-2024", "2", 25, 4, [copy.deepcopy(derivative)]),
            question(
                "paper-2025.derivative",
                "paper-2025",
                "3",
                30,
                4,
                [
                    {**copy.deepcopy(derivative), "weight": 0.7},
                    mapping(
                        "derivative.geometric",
                        "Geometric meaning of derivative",
                        "calculus.derivative.geometric",
                        0.3,
                    ),
                ],
            ),
            question(
                "paper-2025.limit",
                "paper-2025",
                "4",
                10,
                2,
                [mapping("limit.approach", "Limit approach", "calculus.limit.approach")],
            ),
            question(
                "paper-2024.integration",
                "paper-2024",
                "5",
                15,
                3,
                [mapping("integration.basic", "Basic integration", None)],
            ),
        ],
    }


def test_exam_corpus_analysis_maps_common_points_difficulty_and_coverage() -> None:
    path = workspace("analysis")
    imported = output(
        invoke(
            "exam",
            "import",
            path,
            "--input",
            payload(path, "exam.yaml", corpus()),
            "--expected-exam-revision",
            0,
        )
    )
    assert imported["exam_revision"] == 1
    analysis = imported["result"]["analysis"]
    assert analysis["corpus"]["paper_count"] == 3
    assert analysis["corpus"]["question_count"] == 5
    assert analysis["corpus"]["atom_mapping_coverage"] == 0.8
    assert analysis["knowledge_points"][0]["id"] == "derivative.definition"
    assert analysis["knowledge_points"][0]["paper_count"] == 3
    assert analysis["knowledge_points"][0]["corpus_tier"] == "core"
    assert analysis["knowledge_points"][0]["confidence_tier"] == "high"
    assert analysis["difficulty_distribution"]["advanced"] == 2
    assert analysis["coverage_gaps"][0]["id"] == "integration.basic"
    assert any("not be presented as a prediction" in item for item in analysis["limitations"])
    assert (path / "EXAM_BLUEPRINT.md").is_file()
    assert (path / "EXAM_STUDY_PLAN.md").is_file()
    assert output(invoke("validate", path))["ok"] is True


def test_targeted_plan_combines_exam_emphasis_learner_gap_and_prerequisites() -> None:
    path = workspace("plan")
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", corpus())))
    plan = output(invoke("exam", "plan", path, "--mode", "mixed", "--limit", 3))
    target = next(item for item in plan["queue"] if item["atom_id"] == "calculus.derivative.definition")
    assert target["exam_emphasis_score"] >= 0.9
    assert target["learner_gap_score"] == 0.95
    assert target["action"] == "repair_prerequisites"
    assert target["prerequisite_atom_ids"] == ["calculus.limit.approach", "calculus.rate.average"]
    assert target["representative_question_ids"][0] == "paper-2023.derivative"
    assert plan["coverage_gaps"][0]["id"] == "integration.basic"
    assert isinstance(plan["days_remaining"], int) and plan["days_remaining"] > 0
    status = output(invoke("status", path, "--json"))
    assert status["exam"]["question_count"] == 5
    assert status["exam"]["atom_mapping_coverage"] == 0.8


def test_import_is_incremental_revision_guarded_and_duplicate_safe() -> None:
    path = workspace("incremental")
    data = corpus()
    first = {"papers": data["papers"][:1], "questions": data["questions"][:1]}
    output(invoke("exam", "import", path, "--input", payload(path, "first.yaml", first)))
    second_question = question(
        "paper-2023.limit",
        "paper-2023",
        "6",
        5,
        1,
        [mapping("limit.approach", "Limit approach", "calculus.limit.approach")],
    )
    second_path = payload(path, "second.yaml", {"papers": [], "questions": [second_question]})
    stale = invoke(
        "exam",
        "import",
        path,
        "--input",
        second_path,
        "--expected-exam-revision",
        0,
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale exam revision" in stale.stderr
    imported = output(
        invoke(
            "exam",
            "import",
            path,
            "--input",
            second_path,
            "--expected-exam-revision",
            1,
        )
    )
    assert imported["exam_revision"] == 2
    duplicate = invoke("exam", "import", path, "--input", second_path, check=False)
    assert duplicate.returncode == 2
    assert "already imported question ID" in duplicate.stderr
    assert output(invoke("exam", "validate", path))["ok"] is True


def test_full_question_text_bad_weights_and_unknown_atoms_fail_closed() -> None:
    path = workspace("guards")
    data = corpus()
    data["questions"] = data["questions"][:1]
    data["papers"] = data["papers"][:1]
    raw = copy.deepcopy(data)
    raw["questions"][0]["stem_text"] = "A complete copyrighted question should remain in the source layer."
    blocked = invoke("exam", "import", path, "--input", payload(path, "raw.yaml", raw), check=False)
    assert blocked.returncode == 2
    assert "not full question text" in blocked.stderr

    weights = copy.deepcopy(data)
    weights["questions"][0]["knowledge_points"][0]["weight"] = 0.4
    blocked = invoke("exam", "import", path, "--input", payload(path, "weights.yaml", weights), check=False)
    assert blocked.returncode == 2
    assert "weights must sum to 1.0" in blocked.stderr

    unknown = copy.deepcopy(data)
    unknown["questions"][0]["knowledge_points"][0]["atom_id"] = "calculus.unknown"
    blocked = invoke("exam", "import", path, "--input", payload(path, "unknown.yaml", unknown), check=False)
    assert blocked.returncode == 2
    assert "not in the course graph" in blocked.stderr


def test_exam_context_applies_general_adaptation_but_not_research_orientation() -> None:
    path = workspace("adaptation")
    signals = {
        "session_id": "exam-session",
        "context": "exam",
        "signals": [
            {
                "dimension": "challenge.level",
                "value": "stretch",
                "direction": "prefer",
                "evidence": "explicit",
                "reason_code": "explicit_request",
                "confidence": 0.95,
                "turn_refs": ["turn-1"],
            }
        ],
    }
    output(invoke("adapt", "observe-session", path, "--input", payload(path, "signals.yaml", signals)))
    guidance = output(invoke("adapt", "guidance", path, "--context", "exam"))
    assert guidance["active_preferences"][0]["dimension"] == "challenge.level"
    assert "transfer and edge-case" in guidance["instructions"][0]


def test_exam_analysis_reports_rag_source_traceability() -> None:
    path = workspace("rag")
    output(invoke("rag", "init", path))
    source = {
        "sources": [
            {
                "id": "source-2023",
                "title": "2023 calculus exam",
                "authority": "user",
                "text": "# Question 1\nUse the derivative definition to calculate a polynomial derivative.",
            }
        ]
    }
    output(invoke("rag", "ingest", path, "--input", payload(path, "sources.yaml", source)))
    data = corpus()
    bundle = {"papers": data["papers"][:1], "questions": data["questions"][:1]}
    imported = output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", bundle)))
    traceability = imported["result"]["analysis"]["source_traceability"]
    assert traceability["rag_linked_papers"] == 1
    assert traceability["unlinked_source_ids"] == []


def test_tampered_exam_bank_fails_root_validation() -> None:
    path = workspace("tamper")
    data = corpus()
    bundle = {"papers": data["papers"][:1], "questions": data["questions"][:1]}
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", bundle)))
    bank_path = path / ".atomlearn" / "exam" / "bank.yaml"
    bank = yaml.safe_load(bank_path.read_text(encoding="utf-8"))
    bank["questions"][0]["full_solution"] = "This does not belong in canonical exam state."
    bank_path.write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    blocked = invoke("validate", path, check=False)
    assert blocked.returncode == 2
    assert "unsupported fields: full_solution" in blocked.stderr


def test_exam_plan_discloses_and_can_verify_provisional_skips() -> None:
    path = workspace("flexible-skip")
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", corpus())))
    revision = output(invoke("status", path, "--json"))["course"]["revision"]
    for atom_id in [
        "calculus.limit.approach",
        "calculus.rate.average",
        "calculus.derivative.definition",
    ]:
        skipped = output(
            invoke(
                "skip",
                path,
                atom_id,
                "--mode",
                "provisional",
                "--reason-code",
                "already_mastered",
                "--confirmed",
                "--expected-revision",
                revision,
            )
        )
        revision = skipped["revision"]
    mixed = output(invoke("exam", "plan", path, "--mode", "mixed", "--limit", 10))
    target = next(item for item in mixed["queue"] if item["atom_id"] == "calculus.derivative.definition")
    assert target["action"] == "verify_skip"
    assert target["provisional_skip"] is True
    assert any("assumptions, not mastery" in warning for warning in mixed["warnings"])
    learning = output(invoke("exam", "plan", path, "--mode", "learning", "--limit", 10))
    assert "calculus.derivative.definition" not in {item["atom_id"] for item in learning["queue"]}
