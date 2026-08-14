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
RESEARCH_PLAN = ROOT / "examples" / "research-mini" / "plan.yaml"
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
    path = RUN_ROOT / f"lineage-{label}-{uuid.uuid4().hex}"
    output(
        invoke(
            "init",
            path,
            "--course-id",
            f"lineage.{label}",
            "--title",
            f"Lineage {label}",
            "--goal",
            "Understand how concepts connect",
        )
    )
    output(invoke("import-plan", path, "--input", PLAN, "--expected-revision", 0))
    output(invoke("lineage", "init", path))
    return path


def semantic_map() -> dict:
    return {
        "annotations": [
            {
                "atom_id": "calculus.derivative.definition",
                "roles": ["definition", "principle"],
                "central_question": "How can instantaneous change be defined without dividing by zero?",
                "contribution": "Turns average change into a local quantity through a limit.",
                "boundaries": ["Existence of the limit still has to be checked."],
            }
        ],
        "relations": [
            {
                "id": "average-rate-motivates-derivative",
                "from_atom_id": "calculus.rate.average",
                "to_atom_id": "calculus.derivative.definition",
                "type": "motivates",
                "rationale": "Shrinking the interval of an average rate motivates the derivative limit.",
                "confidence": 0.95,
                "source_refs": [{"source_id": "calculus-notes", "locator": "derivative definition"}],
            }
        ],
        "threads": [
            {
                "id": "rate-to-square-derivation",
                "title": "From average rate to a derivative calculation",
                "kind": "derivation",
                "goal": "Show the problem, definition, and first derivation as one thread.",
                "atom_ids": [
                    "calculus.rate.average",
                    "calculus.derivative.definition",
                    "calculus.derivative.compute-square",
                ],
                "narrative": "Refine average change with a limit, then apply the definition to x squared.",
                "confidence": 0.95,
            }
        ],
    }


def test_structural_overview_finds_spines_hubs_bridges_and_root_status() -> None:
    path = workspace("structure")
    overview = output(invoke("lineage", "overview", path, "--lens", "structure"))
    structure = overview["structure"]
    assert structure["root_atom_ids"] == ["calculus.limit.approach", "calculus.rate.average"]
    assert set(structure["leaf_atom_ids"]) == {
        "calculus.derivative.compute-square",
        "calculus.derivative.geometric",
    }
    assert structure["main_learning_spine"][:2] in [
        ["calculus.limit.approach", "calculus.derivative.definition"],
        ["calculus.rate.average", "calculus.derivative.definition"],
    ]
    hub = next(item for item in structure["hubs"] if item["atom_id"] == "calculus.derivative.definition")
    assert (hub["incoming"], hub["outgoing"], hub["degree"]) == (2, 2, 4)
    assert structure["branch_points"][0]["atom_id"] == "calculus.derivative.definition"
    assert structure["convergence_points"][0]["atom_id"] == "calculus.derivative.definition"
    assert any(item["from_module"] == "Limits" for item in structure["bridges"])
    assert (path / "KNOWLEDGE_LINEAGE.md").is_file()
    assert output(invoke("validate", path))["ok"] is True
    assert output(invoke("status", path, "--json"))["lineage"]["valid"] is True


def test_semantic_import_trace_and_revision_guards() -> None:
    path = workspace("semantic")
    import_path = payload(path, "lineage.yaml", semantic_map())
    imported = output(
        invoke(
            "lineage",
            "import",
            path,
            "--input",
            import_path,
            "--expected-lineage-revision",
            0,
        )
    )
    assert imported["lineage_revision"] == 1
    trace = output(invoke("lineage", "trace", path, "calculus.derivative.definition", "--depth", 2))
    assert trace["main_prerequisite_path"][-1] == "calculus.derivative.definition"
    assert {item["atom_id"] for item in trace["upstream"]} == {
        "calculus.limit.approach",
        "calculus.rate.average",
    }
    assert trace["annotation"]["roles"] == ["definition", "principle"]
    assert trace["semantic_relations"][0]["type"] == "motivates"
    assert trace["threads"][0]["id"] == "rate-to-square-derivation"
    stale = invoke(
        "lineage",
        "import",
        path,
        "--input",
        import_path,
        "--expected-lineage-revision",
        0,
        check=False,
    )
    assert stale.returncode == 2
    assert "Stale lineage revision" in stale.stderr


def test_route_combines_prerequisite_and_semantic_edges() -> None:
    path = workspace("route")
    output(invoke("lineage", "import", path, "--input", payload(path, "lineage.yaml", semantic_map())))
    structural = output(
        invoke("lineage", "route", path, "calculus.limit.approach", "calculus.derivative.geometric")
    )
    assert structural["connected"] is True
    assert structural["atom_ids"] == [
        "calculus.limit.approach",
        "calculus.derivative.definition",
        "calculus.derivative.geometric",
    ]
    assert all(step["type"] == "prerequisite_for" for step in structural["steps"])
    semantic = output(
        invoke("lineage", "route", path, "calculus.rate.average", "calculus.derivative.definition")
    )
    assert semantic["connected"] is True
    assert semantic["steps"][0]["type"] == "motivates"


def test_exam_overlay_marks_high_emphasis_atoms() -> None:
    path = workspace("exam")
    output(invoke("exam", "init", path, "--title", "Calculus final", "--target-date", "2099-01-10"))
    exam = {
        "papers": [
            {
                "id": "paper-2025",
                "title": "Calculus 2025",
                "year": 2025,
                "session": "annual",
                "kind": "official_past_exam",
                "total_points": 100,
                "source_id": "calculus-notes",
                "locator": "paper 2025",
            }
        ],
        "questions": [
            {
                "id": "paper-2025.derivative",
                "paper_id": "paper-2025",
                "number": "1",
                "type": "calculation",
                "points": 20,
                "stem_summary": "Compute a derivative from the definition.",
                "source_locator": "question 1",
                "family_id": "derivative-definition",
                "cognitive_levels": ["apply"],
                "tags": ["past-paper"],
                "difficulty": {
                    "basis": "rubric",
                    "conceptual_load": 2,
                    "reasoning_depth": 2,
                    "knowledge_integration": 2,
                    "execution_load": 2,
                    "time_pressure": 2,
                    "confidence": 0.9,
                    "official_level": None,
                },
                "knowledge_points": [
                    {
                        "id": "derivative.definition",
                        "label": "Derivative definition",
                        "atom_id": "calculus.derivative.definition",
                        "weight": 1.0,
                        "confidence": 0.9,
                        "basis": "direct",
                    }
                ],
            }
        ],
    }
    output(invoke("exam", "import", path, "--input", payload(path, "exam.yaml", exam)))
    overlay = output(invoke("lineage", "overview", path, "--lens", "exam"))["exam"]
    assert overlay["enabled"] is True
    assert overlay["top_atoms"][0]["id"] == "calculus.derivative.definition"


def test_research_overlay_counts_concepts_required_by_mapped_papers() -> None:
    path = workspace("research")
    output(
        invoke(
            "research",
            "init",
            path,
            "--field",
            "Reliable research agents",
            "--question",
            "Which concepts support reliable agents?",
            "--scope",
            "Architectures and evaluations",
        )
    )
    research_plan = yaml.safe_load(RESEARCH_PLAN.read_text(encoding="utf-8"))
    research_plan["papers"][0]["concept_atom_ids"] = ["calculus.derivative.definition"]
    research_plan["papers"][1]["concept_atom_ids"] = [
        "calculus.derivative.definition",
        "calculus.limit.approach",
    ]
    output(
        invoke(
            "research",
            "import",
            path,
            "--input",
            payload(path, "research.yaml", research_plan),
            "--expected-research-revision",
            0,
        )
    )
    overlay = output(invoke("lineage", "overview", path, "--lens", "research"))["research"]
    assert overlay["enabled"] is True
    assert overlay["atom_demand"][0]["atom_id"] == "calculus.derivative.definition"
    assert overlay["atom_demand"][0]["paper_count"] == 2


def test_lineage_fails_closed_on_ungrounded_or_tampered_maps() -> None:
    path = workspace("guardrails")
    ungrounded = semantic_map()
    ungrounded["relations"][0]["source_refs"] = []
    blocked = invoke(
        "lineage", "import", path, "--input", payload(path, "ungrounded.yaml", ungrounded), check=False
    )
    assert blocked.returncode == 2
    assert "needs a source reference" in blocked.stderr

    lineage_map_path = path / ".atomlearn" / "lineage" / "map.yaml"
    lineage_map = yaml.safe_load(lineage_map_path.read_text(encoding="utf-8"))
    lineage_map["threads"].append(
        {
            "id": "tampered-thread",
            "title": "Tampered",
            "kind": "custom",
            "goal": "Invalid archived reference",
            "atom_ids": ["calculus.limit.approach", "calculus.unknown"],
            "narrative": "This should fail validation.",
            "confidence": 0.8,
        }
    )
    lineage_map_path.write_text(yaml.safe_dump(lineage_map, allow_unicode=True, sort_keys=False), encoding="utf-8")
    invalid = invoke("validate", path, check=False)
    assert invalid.returncode == 2
    assert "lineage" in invalid.stderr.lower()
    assert "unknown Atom" in invalid.stderr
