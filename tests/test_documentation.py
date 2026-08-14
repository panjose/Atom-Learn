from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def headings(path: Path) -> list[str]:
    return [line[3:] for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## ")]


def code_blocks(path: Path) -> list[str]:
    return [block.strip() for block in re.findall(r"```[^\n]*\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)]


def test_english_and_chinese_readmes_stay_structurally_aligned() -> None:
    english = ROOT / "README.md"
    chinese = ROOT / "README.zh-CN.md"
    assert headings(english) == [
        "Implemented Capabilities",
        "Installation",
        "Quick Verification",
        "Flexible Course Intake",
        "RAG and Corrective Web Search",
        "Research Reading",
        "Self-Evolution",
        "Design Documentation",
        "Development Validation",
    ]
    assert headings(chinese) == [
        "已实现功能",
        "安装",
        "快速验证",
        "灵活课程输入",
        "RAG 与纠错式 Web Search",
        "科研论文阅读",
        "自进化",
        "设计文档",
        "开发验证",
    ]
    assert code_blocks(english) == code_blocks(chinese)


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
