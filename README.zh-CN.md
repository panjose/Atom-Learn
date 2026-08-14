# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn 是一个基于知识原子化的渐进式 AI 学习 Skill。它先把教材或知识库重组为带先修依赖的知识图谱，再围绕单一 Active Atom 进行教学、答疑、掌握验证与复习安排。

> 当前知识原子尚未真正理解，就绝不推进。  
> 在当前 Knowledge Atom 真正掌握前，不进入下一个知识原子。

## 已实现功能

- 从教材、PDF、笔记或多份资料生成 Knowledge Atom DAG
- 严格维持唯一 Active Atom，并执行所有先修守卫
- 分流当前 Atom 问题、阻塞性先修问题、未来问题和 Parking Lot 项目
- 通过 explain/apply/discriminate/transfer/teach-back Evidence 判断掌握程度
- 跨会话恢复状态，并提供 revision 冲突保护和事件审计
- 按 1/3/7/30 天间隔安排复习，同时支持课程级覆盖
- 经用户确认后拆分或合并 Atom，并保留稳定 ID alias
- 分析学习证据，并生成有边界、需审批的课程进化提案
- 从规范化 YAML 状态生成五个学习视图和一个进化视图

## 安装

AtomLearn 需要 Python 3.10+ 和 PyYAML：

```powershell
python -m pip install PyYAML
```

将仓库中的 `atom-learn` 目录复制或链接到个人 Codex Skills 目录，例如：

```text
~/.codex/skills/atom-learn/
```

开发时，也可以直接让 Codex 使用仓库中的 `atom-learn/SKILL.md`。

## 快速验证

```powershell
python atom-learn/scripts/atomlearn.py init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py validate courses/calculus
python atom-learn/scripts/atomlearn.py status courses/calculus --json
```

完整命令流程和教学行为见 [SKILL.md](atom-learn/SKILL.md)，结构化输入格式见 [SCHEMA.md](atom-learn/references/SCHEMA.md)。运行时课程状态存放在学习者选择的课程工作区，而不是 Skill 安装目录。

## 自进化

AtomLearn 可以从已持久化的 Evidence、复习结果和先修回退中派生指标，并针对教学策略、复习间隔、掌握标准、依赖边或 Atom 结构生成可检验的提案。进化默认使用 `proposal_only` 模式：每项变更都必须经过预览、所需权限审批、验证、检查点保存和效果监测。

```powershell
python atom-learn/scripts/atomlearn.py evolve status courses/calculus
python atom-learn/scripts/atomlearn.py evolve analyze courses/calculus --propose
python atom-learn/scripts/atomlearn.py evolve preview courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve approve courses/calculus evo-000001 --authority learner --actor "learner"
python atom-learn/scripts/atomlearn.py evolve apply courses/calculus evo-000001
python atom-learn/scripts/atomlearn.py evolve monitor courses/calculus evo-000001
```

引擎分别维护课程 revision 和进化 revision，不在进化指标中存储学习者原始消息，并拒绝在运行时应用 `patch_skill`。只有后续尚未发生新的学习变更时才允许自动回滚；否则 AtomLearn 会要求创建保留新 Evidence 的补偿提案。完整操作流程见[有边界的自进化](atom-learn/references/EVOLUTION.md)。

## 设计文档

- [产品与技术设计](docs/PRODUCT_DESIGN.md)
- [详细实施方案](docs/IMPLEMENTATION_PLAN.md)
- [自进化设计](docs/SELF_EVOLUTION_DESIGN.md)

## 开发验证

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/evolution.py
```

仓库提供微积分和操作系统两个小型课程计划作为测试夹具。自动测试使用 `.test-workspaces/` 中的独立工作区，不会修改示例文件。
