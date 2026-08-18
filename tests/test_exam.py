from __future__ import annotations

import copy
import json
import subprocess
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "atom-learn" / "scripts" / "atomlearn.py"
PLAN = ROOT / "examples" / "calculus-mini" / "plan.yaml"
RUN_ROOT = ROOT / ".test-workspaces"
sys.path.insert(0, str(ROOT / "atom-learn" / "scripts"))

from exam import ExamEngine


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
    result = {
        "basis": "official" if official else "rubric",
        "conceptual_load": level,
        "reasoning_depth": level,
        "knowledge_integration": level,
        "execution_load": level,
        "time_pressure": level,
        "confidence": 0.9,
        "official_level": float(level) if official else None,
    }
    if official:
        result["official"] = {
            "level": float(level),
            "source": "exam board specification",
            "source_locator": f"difficulty table level {level}",
            "reviewer_id": "fixture-reviewer",
        }
    return result


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
    before_review = yaml.safe_load((path / ".atomlearn" / "exam" / "bank.yaml").read_text(encoding="utf-8"))
    assert before_review["questions"][0]["difficulty"]["effective_basis"] == "structural_complexity"
    assert before_review["questions"][0]["difficulty"]["official_difficulty"]["qualified"] is False

    official = {
        "records": [
            {
                "question_id": bundle["questions"][0]["id"],
                "level": 4.0,
                "source": "exam board specification",
                "source_locator": "official rubric, row 1",
                "reviewer_id": "reviewer-a",
            }
        ]
    }
    recorded = output(
        invoke(
            "exam", "record-official", path,
            "--input", payload(path, "official.yaml", official),
            "--expected-exam-revision", 1,
        )
    )
    assert recorded["result"]["reviewed_source_locators"] == 1

    calibrated = output(
        invoke("exam", "calibrate", path, "--expected-exam-revision", 2)
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
                "population": "enrolled calculus learners", "window_start": "2025-01-01", "window_end": "2025-06-30",
            },
            {
                "question_id": "paper-2024.derivative", "attempt_count": 5, "correct_rate": 0.1,
                "median_seconds": 600, "discrimination": None, "irt_b": None,
                "source": "pilot", "source_locator": "pilot/item-2",
                "population": "pilot learners", "window_start": "2025-07-01", "window_end": "2025-07-31",
            },
        ]
    }
    recorded = output(invoke("exam", "record-empirical", path, "--input", payload(path, "empirical.yaml", empirical)))
    assert recorded["result"]["qualified_question_ids"] == ["paper-2023.derivative"]
    bank = yaml.safe_load((path / ".atomlearn" / "exam" / "bank.yaml").read_text(encoding="utf-8"))
    difficulties = {item["id"]: item["difficulty"] for item in bank["questions"]}
    assert difficulties["paper-2023.derivative"]["effective_basis"] == "empirical"
    assert difficulties["paper-2023.derivative"]["empirical_difficulty"]["source_locator"] == "cohort-2025/item-1"
    assert difficulties["paper-2023.derivative"]["empirical_difficulty"]["population"] == "enrolled calculus learners"
    assert difficulties["paper-2024.derivative"]["effective_basis"] == "structural_complexity"
    assert difficulties["paper-2024.derivative"]["empirical_difficulty"]["qualification_reasons"] == ["insufficient_attempts"]


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


def schedule_payload(*, start: date, target: date, minutes: int = 480) -> dict:
    return {
        # PyYAML resolves unquoted ISO dates in user-authored templates to date
        # objects; the CLI must normalize those exactly like quoted strings.
        "start_date": start,
        "target_date": target,
        "available_weekdays": [1, 2, 3, 4, 5, 6, 7],
        "minutes_per_day": minutes,
        "durations": {"learn": 20, "remediate": 20, "review": 15, "practice": 20, "prerequisite": 20},
        "desired_retention": 0.9,
        "final_review_days": 2,
        "mode": "mixed",
    }


def test_revisioned_schedule_survives_missed_day_and_replans_without_lowering_mastery() -> None:
    path = workspace("revisioned-schedule")
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", corpus())))
    before = output(invoke("exam", "plan-status", path, "--as-of", "2099-01-01"))
    assert before["freshness"] == "uninitialized"
    assert not (path / ".atomlearn" / "exam" / "schedule.yaml").exists()

    mismatched = schedule_payload(start=date(2099, 1, 1), target=date(2099, 2, 1))
    rejected = invoke(
        "exam", "replan", path, "--input", payload(path, "mismatched.yaml", mismatched),
        "--reason", "initial", "--as-of", "2099-01-01", "--expected-schedule-revision", 0,
        check=False,
    )
    assert rejected.returncode == 2
    assert "must match the exam target date" in rejected.stderr
    assert not (path / ".atomlearn" / "exam" / "schedule.yaml").exists()

    configuration = schedule_payload(start=date(2099, 1, 1), target=date(2099, 1, 10))
    first = output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "schedule.yaml", configuration),
            "--reason", "initial", "--as-of", "2099-01-01", "--expected-plan-revision", 0,
        )
    )
    assert first["freshness"] == "current"
    assert first["event"] == {"type": "replanned", "plan_revision": 1}
    planned_day = next(item for item in first["plan"]["days"] if item["tasks"])
    missed = {
        "date": planned_day["date"], "status": "missed", "completed_task_ids": [],
        "actual_minutes": 0, "available_minutes": planned_day["capacity_minutes"],
    }
    recorded = output(
        invoke(
            "exam", "record-day", path, "--input", payload(path, "missed.yaml", missed),
            "--expected-plan-revision", 1,
        )
    )
    assert recorded["freshness"] == "stale"
    assert "day_missed" in recorded["invalidation_reasons"]

    next_day = (date.fromisoformat(planned_day["date"]) + timedelta(days=1)).isoformat()
    replanned = output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "schedule-retry.yaml", configuration),
            "--reason", "day_missed", "--as-of", next_day, "--expected-plan-revision", 2,
        )
    )
    assert replanned["schedule_revision"] == 3
    assert replanned["freshness"] == "current"
    assert replanned["plan"]["start_date"] == next_day
    assert "day_missed" in replanned["plan"]["replan_reasons"]
    assert output(invoke("exam", "validate", path))["ok"] is True
    view = (path / "EXAM_STUDY_PLAN.md").read_text(encoding="utf-8")
    assert "## Canonical Schedule" in view
    assert "Schedule revision: `3`" in view
    assert "### Revisioned Calendar" in view


def test_canonical_replan_emits_infeasible_and_mapping_review_invalidates_it() -> None:
    path = workspace("schedule-invalidation")
    process_payload = {
        "documents": [
            {
                "paper": {
                    "id": "mapping-2025", "title": "Mapping paper", "year": 2025, "session": "",
                    "kind": "official_past_exam", "total_points": 10,
                    "source_id": "mapping-source", "locator": "page 1",
                },
                "questions": "Question 1. Explain the derivative definition. [10 marks]",
                "answers": "Question 1. Use the difference quotient limit.",
                "marking_scheme": "Question 1. Definition 5 marks; limit 5 marks",
            }
        ],
        "options": {"semantic_mapping": "off", "mapping_review_threshold": 0.85},
    }
    processed = output(invoke("exam", "process", path, "--input", payload(path, "process.yaml", process_payload)))
    tiny = schedule_payload(start=date(2099, 1, 1), target=date(2099, 1, 10), minutes=5)
    canonical = output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "tiny.yaml", tiny),
            "--reason", "initial", "--as-of", "2099-01-01", "--expected-plan-revision", 0,
        )
    )
    assert canonical["event"]["type"] in {"replanned", "infeasible"}
    queue = processed["result"]["mapping_review_queue"]
    review = {
        "reviews": [
            {
                "question_id": queue[0]["question_id"],
                "mapping_id": queue[0]["mapping_id"],
                "decision": "remap",
                "atom_id": "calculus.derivative.definition",
                "knowledge_point_id": "derivative.definition.reviewed",
                "label": "Derivative definition",
            }
        ]
    }
    output(
        invoke(
            "exam", "review-mappings", path, "--input", payload(path, "review-all.yaml", review),
            "--expected-exam-revision", 1,
        )
    )
    status = output(invoke("exam", "plan-status", path, "--as-of", "2099-01-01"))
    assert status["freshness"] == "stale"
    assert "mapping_review_completed" in status["invalidation_reasons"]

    impossible = schedule_payload(start=date(2099, 1, 1), target=date(2099, 1, 10), minutes=5)
    failed = output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "impossible.yaml", impossible),
            "--reason", "mapping_review_completed", "--as-of", "2099-01-01", "--expected-plan-revision", 1,
        )
    )
    assert failed["event"]["type"] == "infeasible"
    assert failed["freshness"] == "infeasible"
    assert failed["plan"]["gap_minutes"] > 0

    output(
        invoke(
            "exam", "set-target", path, "--target-date", "2099-03-01",
            "--expected-exam-revision", 2,
        )
    )
    target_status = output(invoke("exam", "plan-status", path, "--as-of", "2099-01-01"))
    assert target_status["freshness"] == "stale"
    assert "exam_target_changed" in target_status["invalidation_reasons"]


def test_semantic_mapping_gate_fails_closed_and_hybrid_candidates_keep_rag_provenance() -> None:
    path = workspace("semantic-gate")
    required = {
        "documents": [
            {
                "paper": {
                    "id": "semantic-2025", "title": "Semantic paper", "year": 2025, "session": "",
                    "kind": "practice_set", "total_points": 10,
                    "source_id": "semantic-source", "locator": "page 1",
                },
                "questions": "Question 1. Discuss an unfamiliar operation. [10 marks]",
            }
        ],
        "options": {"semantic_mapping": "required", "mapping_review_threshold": 0.85},
    }
    blocked = invoke("exam", "process", path, "--input", payload(path, "semantic.yaml", required), check=False)
    assert blocked.returncode == 2
    assert "RAG is not initialized" in blocked.stderr
    assert output(invoke("exam", "status", path))["exam_revision"] == 0

    output(invoke("rag", "init", path))
    engine = ExamEngine.load(str(path))

    class ApprovedRag:
        @staticmethod
        def status() -> dict:
            return {
                "valid": True, "rag_revision": 7,
                "embedding_profile": {"kind": "learned_local", "model": "fixture-embedding"},
                "reranker_profile": {"model": "fixture-reranker", "benchmark_profile": "core-release-v2"},
            }

        @staticmethod
        def search(search_payload: dict, *, record: bool) -> dict:
            assert search_payload["use_cross_encoder"] is True
            assert record is False
            return {
                "results": [
                    {
                        "chunk_id": "chunk-fixture", "source_id": "calculus-notes", "source_revision": 3,
                        "section": "Derivative", "locator": "Atom calculus.derivative.definition",
                        "text": "difference quotient derivative limit", "document_ir_block_ids": ["block-fixture"],
                        "rerank_score": 1.0,
                    }
                ]
            }

    with patch("exam.RagEngine.load", return_value=ApprovedRag()):
        semantic = engine._semantic_mapping_evidence("unfamiliar derivative operation", "auto")
    assert semantic["gate"] == "used"
    engine._semantic_mapping_evidence = lambda query, mode: semantic
    mappings = engine._auto_mappings("unfamiliar operation", "", "", 0.85, ["page 1"], "auto")
    selected = next(item for item in mappings if item["atom_id"] == "calculus.derivative.definition")
    assert selected["mapping_method"] == "hybrid-rag-stem-answer-rubric-v1"
    assert selected["candidate_scores"][0]["semantic"] == semantic["results"][0]["score"]
    assert 0 < selected["candidate_scores"][0]["semantic"] <= 1
    assert selected["semantic_evidence"]["rag_revision"] == 7
    assert selected["semantic_evidence"]["results"][0]["chunk_id"] == "chunk-fixture"
    assert selected["semantic_evidence"]["results"][0]["source_revision"] == 3
    assert engine._normalize_mapping(selected, "fixture") == selected


def test_new_learning_evidence_invalidates_a_bound_exam_schedule() -> None:
    path = workspace("schedule-evidence")
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", corpus())))
    configuration = schedule_payload(start=date(2099, 1, 1), target=date(2099, 1, 10))
    output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "schedule.yaml", configuration),
            "--reason", "initial", "--as-of", "2099-01-01", "--expected-plan-revision", 0,
        )
    )
    activated = output(invoke("activate", path, "calculus.limit.approach", "--expected-revision", 1))
    rebound = output(
        invoke(
            "exam", "replan", path, "--input", payload(path, "rebound.yaml", configuration),
            "--reason", "course_revision_changed", "--as-of", "2099-01-01", "--expected-plan-revision", 1,
        )
    )
    assert rebound["freshness"] == "current"
    evidence = {
        "atom_id": "calculus.limit.approach",
        "kind": "mastery_check",
        "measurement_kind": "immediate_mastery",
        "measurement_item_id": "calculus.limit.approach.fixture-v2",
        "episode_id": "episode-exam-replan-evidence",
        "assessment": {
            "method": "human", "grader_id": "atomlearn/human-adjudication-v1",
            "rubric_version": "human-v1", "calibration_set_version": None,
            "independent": True, "answer_hash": "sha256:" + "b" * 64,
        },
        "prompt": "Explain why approaching a value is not the same as taking the value.",
        "response_summary": "The learner distinguished approach from equality.",
        "scores": {"explain": 0.9, "apply": 0.9, "discriminate": 0.9},
        "feedback": "The distinction was explicit.",
        "rationale": "Source-grounded observable performance.",
    }
    output(
        invoke(
            "record-evidence", path, "--input", payload(path, "evidence.yaml", evidence),
            "--expected-revision", activated["revision"],
        )
    )
    status = output(invoke("exam", "plan-status", path, "--as-of", "2099-01-01"))
    assert status["freshness"] == "stale"
    assert status["invalidation_reasons"] == ["learning_evidence_changed"]
