from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atom-learn" / "scripts"
sys.path.insert(0, str(SCRIPTS))


MODULES = [
    "atomlearn",
    "intake",
    "rag",
    "adaptation",
    "evolution",
    "research",
    "exam",
    "lineage",
    "migrations",
    "user_profile",
    "effective_policy",
]


def subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))


def local_module(module_name: str):
    if module_name == "atomlearn":
        path = SCRIPTS / "atomlearn.py"
        spec = importlib.util.spec_from_file_location("atomlearn", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["atomlearn"] = module
        spec.loader.exec_module(module)
        return module
    path = SCRIPTS / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_atomlearn_cli_{module_name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_cli_subcommand_has_visible_help_text() -> None:
    for module_name in MODULES:
        parser = local_module(module_name).build_parser()
        action = subparser_action(parser)
        documented = {choice.dest: choice.help for choice in action._choices_actions}
        assert documented.keys() == action.choices.keys(), module_name
        for command, help_text in documented.items():
            assert help_text and help_text != argparse.SUPPRESS, f"{module_name} {command} has no help"
            assert len(help_text.split()) >= 3, f"{module_name} {command} help is not descriptive"


def test_short_console_entry_point_and_supported_python_range_are_declared() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'atomlearn = "atomlearn:main"' in project
    assert 'requires-python = ">=3.10"' in project


def test_package_version_matches_core_manifest() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (ROOT / "atom-learn" / "assets" / "core-manifest.yaml").read_text(encoding="utf-8")
    assert 'version = "0.13.0"' in project
    assert "core_version: 0.13.0" in manifest


def test_core_renderer_produces_chinese_view_labels() -> None:
    atomlearn = local_module("atomlearn")
    translated = atomlearn.chinese_view(
        ["# Demo Learning Map", "## Active Atom", "- Mastered with Evidence: 1 / 2", "- None"]
    )
    assert translated == ["# Demo 学习地图", "## 当前 Active Atom", "- 有 Evidence 的已掌握项： 1 / 2", "- 无"]


def test_research_and_exam_automatic_helpers_are_deterministic() -> None:
    local_module("atomlearn")
    research = local_module("research")
    exam = local_module("exam")
    assert research.normalize_doi("https://doi.org/10.1234/Example.1") == "10.1234/example.1"
    assert research.title_fingerprint("A Method: Revisited!") == "amethodrevisited"
    sections = exam.split_numbered_sections("Question 1. First\nQ2: Second", "fixture")
    assert [item["number"] for item in sections] == ["1", "2"]
