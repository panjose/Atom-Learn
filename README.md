# AtomLearn

AtomLearn 是一个“知识原子化”的渐进式 AI 学习 Skill：先把教材或知识库重组为带依赖关系的知识图谱，再围绕单一 Active Atom 教学、答疑、验证与复习。

> Never advance while the current atom remains unclear.  
> 当前知识原子尚未真正掌握，就不进入下一个知识原子。

## 已实现能力

- 从教材、PDF、笔记或多份资料生成 Knowledge Atom DAG
- 严格维持唯一 Active Atom 与先修守卫
- 当前问题、阻塞性先修、未来问题和 Parking Lot 分流
- 以 explain/apply/discriminate/transfer/teach-back Evidence 判断掌握
- 跨会话状态恢复、revision 并发保护与事件审计
- 1/3/7/30 天间隔复习，可由课程配置覆盖
- 经用户确认的 Atom 拆分、合并与稳定 ID alias
- 从规范化 YAML 自动生成五个可读 Markdown 视图

## 安装

运行环境需要 Python 3.10+ 和 PyYAML：

```powershell
python -m pip install PyYAML
```

将仓库中的 `atom-learn` 目录复制或链接到个人 Codex Skills 目录，例如：

```text
~/.codex/skills/atom-learn/
```

也可以在开发时直接让 Codex 使用仓库内的 `atom-learn/SKILL.md`。

## 快速验证

```powershell
python atom-learn/scripts/atomlearn.py init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py validate courses/calculus
python atom-learn/scripts/atomlearn.py status courses/calculus --json
```

完整命令和教学行为由 [SKILL.md](atom-learn/SKILL.md) 说明；结构化输入格式见 [SCHEMA.md](atom-learn/references/SCHEMA.md)。运行时课程状态存放在用户选择的课程工作区，而不是 Skill 安装目录。

## 设计文档

- [产品与技术设计](docs/PRODUCT_DESIGN.md)
- [详细实施方案](docs/IMPLEMENTATION_PLAN.md)

## 开发验证

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py
```

仓库提供微积分和操作系统两个小型课程计划作为测试夹具。自动测试使用 `.test-workspaces/` 中的独立工作区，不会改写示例文件。
