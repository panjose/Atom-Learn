from __future__ import annotations

import os
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def headings(path: Path) -> list[str]:
    phase_seven = {"## Per-Atom Adaptive Review", "## 每 Atom 自适应复习"}
    return [
        line[3:]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ") and line not in phase_seven
    ]


def code_blocks(path: Path) -> list[str]:
    return [block.strip() for block in re.findall(r"```[^\n]*\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)]


def test_english_and_chinese_readmes_stay_structurally_aligned() -> None:
    english = ROOT / "README.md"
    chinese = ROOT / "README.zh-CN.md"
    assert headings(english) == [
        "Implemented Capabilities",
        "Installation",
        "Quick Verification",
        "Evidence v3 and Mastery Feasibility",
        "Flexible Course Intake",
        "RAG and Corrective Web Search",
        "Knowledge Lineage and Concept Maps",
        "Flexible Progression and Skips",
        "Atomic Detailed Explanations",
        "Relation-Aware Concept Routing",
        "Research Reading",
        "Exam Analysis and Targeted Preparation",
        "Session-Based Self-Adaptation",
        "Self-Evolution",
        "Open Source and Community",
        "Design Documentation",
        "Development Validation",
    ]
    assert headings(chinese) == [
        "已实现功能",
        "安装",
        "快速验证",
        "Evidence v3 与掌握可行性",
        "灵活课程输入",
        "RAG 与纠错式 Web Search",
        "知识脉络与概念地图",
        "弹性进度与跳过",
        "原子化详细讲解",
        "关系感知的概念路由",
        "科研论文阅读",
        "试题分析与针对性备考",
        "基于 Session 的自适应",
        "自进化",
        "开源与社区",
        "设计文档",
        "开发验证",
    ]
    assert code_blocks(english) == code_blocks(chinese)


def test_adaptive_review_sections_are_bilingual_and_command_aligned() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "## Per-Atom Adaptive Review" in english
    assert "## 每 Atom 自适应复习" in chinese
    review_commands = [
        "atomlearn review benchmark",
        "atomlearn review configure",
        "atomlearn review status",
        "atomlearn review queue",
        "atomlearn review pilot",
    ]
    assert all(command in english and command in chinese for command in review_commands)


def test_public_claims_disclose_delivery_and_learning_evidence_boundaries() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "Implemented describes repository code status, not stable release delivery." in english
    assert "`ocr`, `scale`, and `semantic` are developer/source extras" in english
    assert "No AtomLearn learning-gain effect has been established." in english
    assert "“已实现”描述的是仓库代码状态，不等于稳定发行交付状态。" in chinese
    assert "`ocr`、`scale` 和 `semantic` 是开发者/源码 extras" in chinese
    assert "AtomLearn 尚未建立任何学习增益效果结论。" in chinese


def test_repository_markdown_has_no_broken_relative_links() -> None:
    missing: list[str] = []
    ignored = {".git", ".pytest_cache", ".test-workspaces", "__pycache__"}
    for directory, directories, files in os.walk(ROOT):
        directories[:] = [name for name in directories if name not in ignored]
        for name in files:
            if not name.endswith(".md"):
                continue
            path = Path(directory) / name
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                relative = target.split("#", 1)[0]
                if relative and not (path.parent / relative).resolve().exists():
                    missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, "broken relative Markdown links:\n" + "\n".join(missing)


def test_open_source_license_and_package_metadata_are_complete() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert license_text == (ROOT / "manager" / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright 2026 panjose" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "pypdfium2 / PDFium" in notices
    assert "PyMuPDF" not in notices

    root_project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manager_project = (ROOT / "manager" / "pyproject.toml").read_text(encoding="utf-8")
    for project in [root_project, manager_project]:
        assert 'license = "Apache-2.0"' in project
        assert 'authors = [{ name = "panjose" }]' in project
        assert "https://github.com/panjose/Atom-Learn" in project
    assert 'pypdfium2>=4.30,<6' in root_project
    assert "PyMuPDF" not in root_project


def test_open_source_community_contracts_are_present_and_privacy_safe() -> None:
    required = [
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
        "CODE_OF_CONDUCT.md",
        "CITATION.cff",
        ".github/CODEOWNERS",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    ]
    assert all((ROOT / path).is_file() for path in required)

    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    conduct = (ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert "privacy-minimized" in contributing
    assert "exactly one Active Atom" in contributing
    assert "private vulnerability reporting" in " ".join(security.split())
    assert "Do not open a public issue" in security
    assert "Contributor Covenant 3.0" in conduct
    assert "CC BY-SA 4.0" in conduct

    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    assert citation["cff-version"] == "1.2.0"
    assert citation["license"] == "Apache-2.0"
    assert citation["repository-code"] == "https://github.com/panjose/Atom-Learn"

    for path in [
        ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
    ]:
        assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
    config = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False
