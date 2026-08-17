from __future__ import annotations

import re
from pathlib import Path


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
        "Evidence v2 and Learning Measurement",
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
        "Design Documentation",
        "Development Validation",
    ]
    assert headings(chinese) == [
        "已实现功能",
        "安装",
        "快速验证",
        "Evidence v2 与学习测量",
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


def test_repository_markdown_has_no_broken_relative_links() -> None:
    missing: list[str] = []
    for path in ROOT.rglob("*.md"):
        if any(part in {".git", ".pytest_cache", ".test-workspaces"} for part in path.parts):
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (path.parent / relative).resolve().exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, "broken relative Markdown links:\n" + "\n".join(missing)
