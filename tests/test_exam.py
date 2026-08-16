from __future__ import annotations

import copy
import json
import subprocess
import sys
import uuid
from datetime import date, timedelta
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


def test_exam_process_splits_questions_links_marking_and_proposes_reviewable_atom_mappings() -> None:
    path = workspace("automatic")
    process_payload = {
        "documents": [
            {
                "paper": {
                    "id": "auto-2025",
                    "title": "Automatic calculus paper",
                    "year": 2025,
                    "session": "annual",
                    "kind": "official_past_exam",
                    "total_points": 15,
                    "source_id": "auto-2025-source",
                    "locator": "pages 1-2",
                },
                "questions": "Question 1. Calculate a derivative from the derivative definition. [10 marks]\nShow every limit step.\nQuestion 2. Explain the geometric meaning of a derivative. [5 marks]",
                "answers": "1. The difference quotient limit gives the derivative.\n2. It is the tangent slope.",
                "marking_scheme": "Q1: definition 3 marks, algebra 4 marks, limit 3 marks\nQ2: tangent 3 marks, interpretation 2 marks",
            }
        ]
    }
    processed = output(
        invoke("exam", "process", path, "--input", payload(path, "process.yaml", process_payload))
    )
    assert processed["exam_revision"] == 1
    assert processed["result"]["processing"][0]["question_count"] == 2
    assert len(processed["result"]["imported_questions"]) == 2
    assert processed["result"]["analysis"]["answer_marking_association"] == {"linked": 2}
    review_queue = processed["result"]["mapping_review_queue"]
    assert review_queue
    assert "calculus.derivative.definition" in review_queue[0]["candidate_atom_ids"]

    review = {
        "reviews": [
            {
                "question_id": review_queue[0]["question_id"],
                "mapping_id": review_queue[0]["mapping_id"],
                "decision": "confirm",
            }
        ]
    }
    reviewed = output(
        invoke(
            "exam",
            "review-mappings",
            path,
            "--input",
            payload(path, "review.yaml", review),
            "--expected-exam-revision",
            1,
        )
    )
    assert reviewed["exam_revision"] == 2
    assert reviewed["result"]["review_count"] == 1
    assert output(invoke("exam", "validate", path))["ok"] is True


def test_exam_difficulty_calibration_uses_official_anchors_and_updates_effective_levels() -> None:
    path = workspace("calibration")
    data = corpus()
    bundle = {"papers": data["papers"][:2], "questions": data["questions"][:2]}
    anchor = bundle["questions"][0]["difficulty"]
    anchor["basis"] = "official"
    anchor["official_level"] = 4.0
    output(invoke("exam", "import", path, "--input", payload(path, "anchors.yaml", bundle)))

    calibrated = output(
        invoke("exam", "calibrate", path, "--expected-exam-revision", 1)
    )
    assert calibrated["result"] == {
        "offset": 2.0,
        "anchor_count": 1,
        "mae_before": 2.0,
        "mae_after": 0.0,
    }
    status = output(invoke("exam", "status", path))
    assert status["difficulty_calibration"]["offset"] == 2.0
    assert output(invoke("exam", "validate", path))["ok"] is True


def test_exam_process_fails_closed_when_question_boundaries_are_not_detectable() -> None:
    path = workspace("split-guard")
    raw = {
        "documents": [
            {
                "paper": {
                    "id": "bad-paper",
                    "title": "Unstructured paper",
                    "year": 2025,
                    "session": "",
                    "kind": "practice_set",
                    "total_points": None,
                    "source_id": "bad-source",
                    "locator": "page 1",
                },
                "questions": "A paragraph with no stable question numbering.",
            }
        ]
    }
    blocked = invoke("exam", "process", path, "--input", payload(path, "bad-process.yaml", raw), check=False)
    assert blocked.returncode == 2
    assert "no recognizable numbered question headings" in blocked.stderr
    assert output(invoke("exam", "status", path))["question_count"] == 0


def test_exam_process_source_consumes_shared_document_ir_and_retains_block_provenance() -> None:
    path = workspace("document-ir")
    output(invoke("rag", "init", path))
    source = {
        "sources": [
            {
                "id": "past-paper-source",
                "title": "Calculus past paper",
                "authority": "official",
                "text": (
                    "# Past paper\n"
                    "Question 1. Calculate a derivative from the derivative definition. [10 marks]\n"
                    "Show every limit step.\n"
                    "Question 2. Explain the geometric meaning of a derivative. [5 marks]"
                ),
            }
        ]
    }
    output(invoke("rag", "ingest", path, "--input", payload(path, "rag-source.yaml", source)))

    processed = output(
        invoke(
            "exam",
            "process-source",
            path,
            "--source-id",
            "past-paper-source",
            "--paper-id",
            "paper-ir-2025",
            "--year",
            2025,
        )
    )
    assert processed["exam_revision"] == 1
    assert processed["result"]["document_ir"]["source_revision"] == 1
    assert processed["result"]["processing"][0]["question_count"] == 2
    assert output(invoke("exam", "status", path))["question_count"] == 2
    bank = yaml.safe_load((path / ".atomlearn" / "exam" / "bank.yaml").read_text(encoding="utf-8"))
    assert all(item["source_locator"].startswith("document-ir blocks block-") for item in bank["questions"])
    assert output(invoke("exam", "validate", path))["ok"] is True


def test_joint_mapping_is_provisional_and_keeps_stem_answer_rubric_evidence() -> None:
    path = workspace("joint-mapping")
    process_payload = {
        "documents": [
            {
                "paper": {
                    "id": "joint-2025", "title": "Joint mapping paper", "year": 2025, "session": "",
                    "kind": "official_past_exam", "total_points": 10, "source_id": "joint-source", "locator": "paper page 1",
                },
                "questions": "Question 1. Calculate a derivative. [10 marks]",
                "answers": "Question 1. Use the derivative definition and difference quotient limit.",
                "marking_scheme": "Question 1. derivative definition 4 marks; limit evaluation 6 marks",
            }
        ]
    }
    result = output(invoke("exam", "process", path, "--input", payload(path, "joint.yaml", process_payload)))
    proposal = result["result"]["mapping_review_queue"][0]
    bank = yaml.safe_load((path / ".atomlearn" / "exam" / "bank.yaml").read_text(encoding="utf-8"))
    mapping_record = bank["questions"][0]["knowledge_points"][0]
    assert mapping_record["review_status"] == "pending"
    assert mapping_record["mapping_method"] == "joint-stem-answer-rubric-v1"
    assert mapping_record["candidate_scores"][0].keys() == {"atom_id", "stem", "answer", "rubric", "joint"}
    assert len(mapping_record["evidence_locators"]) == 3
    assert proposal["proposed_atom_id"] is not None
    assert output(invoke("exam", "analyze", path))["corpus"]["atom_mapping_coverage"] == 0.0


def test_empirical_difficulty_is_separate_qualified_and_source_located() -> None:
    path = workspace("empirical")
    data = corpus()
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", data)))
    empirical = {
        "aggregates": [
            {
                "question_id": "paper-2023.derivative", "attempt_count": 60, "correct_rate": 0.25,
                "median_seconds": 500, "discrimination": 0.4, "irt_b": None,
                "source": "anonymized cohort", "source_locator": "cohort-2025/item-1",
            },
            {
                "question_id": "paper-2024.derivative", "attempt_count": 5, "correct_rate": 0.1,
                "median_seconds": 600, "discrimination": None, "irt_b": None,
                "source": "pilot", "source_locator": "pilot/item-2",
            },
        ]
    }
    recorded = output(invoke("exam", "record-empirical", path, "--input", payload(path, "empirical.yaml", empirical)))
    assert recorded["result"]["qualified_question_ids"] == ["paper-2023.derivative"]
    bank = yaml.safe_load((path / ".atomlearn" / "exam" / "bank.yaml").read_text(encoding="utf-8"))
    difficulties = {item["id"]: item["difficulty"] for item in bank["questions"]}
    assert difficulties["paper-2023.derivative"]["effective_basis"] == "empirical"
    assert difficulties["paper-2023.derivative"]["empirical_difficulty"]["source_locator"] == "cohort-2025/item-1"
    assert difficulties["paper-2024.derivative"]["effective_basis"] == "structural_complexity"


def test_item_families_require_review_and_report_held_out_memorization_risk() -> None:
    path = workspace("families")
    documents = []
    for year in [2024, 2025]:
        documents.append(
            {
                "paper": {
                    "id": f"family-{year}", "title": f"Family paper {year}", "year": year, "session": "",
                    "kind": "official_past_exam", "total_points": 10, "source_id": f"family-source-{year}", "locator": "page 1",
                },
                "questions": f"Question 1. Calculate the derivative of x^{year - 2022} from the derivative definition. [10 marks]",
                "answers": "Question 1. Apply the difference quotient limit.",
                "marking_scheme": "Question 1. definition 4 marks; algebra 3 marks; limit 3 marks",
            }
        )
    output(invoke("exam", "process", path, "--input", payload(path, "families-process.yaml", {"documents": documents})))
    proposed = output(invoke("exam", "propose-families", path, "--threshold", 0.5))
    family_id = proposed["result"]["proposed_family_ids"][0]
    review = {
        "reviews": [
            {
                "family_id": family_id, "decision": "confirm", "canonical_id": None, "label": None,
                "transfer_evidence": {
                    "seen_attempts": 5, "seen_success_rate": 1.0, "held_out_attempts": 5,
                    "held_out_success_rate": 0.4, "source_locator": "attempt-log/family-transfer",
                },
            }
        ]
    }
    output(invoke("exam", "review-families", path, "--input", payload(path, "family-review.yaml", review)))
    family = output(invoke("exam", "analyze", path))["question_families"]["families"][0]
    assert family["review_status"] == "confirmed"
    assert family["memorization_risk"] == "high"
    assert len(family["question_ids"]) == 2


def test_daily_exam_plan_fails_closed_when_capacity_cannot_fit_required_work() -> None:
    path = workspace("daily-plan")
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", corpus())))
    start = date.today()
    daily = {
        "start_date": start.isoformat(), "target_date": (start + timedelta(days=1)).isoformat(),
        "available_weekdays": [start.isoweekday()], "minutes_per_day": 10,
        "durations": {"learn": 20, "remediate": 20, "review": 20, "practice": 20, "prerequisite": 20},
        "desired_retention": 0.9, "final_review_days": 1, "mode": "mixed",
    }
    plan = output(invoke("exam", "daily-plan", path, "--input", payload(path, "daily.yaml", daily)))
    assert plan["status"] == "infeasible"
    assert plan["gap_minutes"] > 0
    assert plan["unscheduled_tasks"]
    assert any("without lowering the mastery threshold" in item for item in plan["adjustments"])
