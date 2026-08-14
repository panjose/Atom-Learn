# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn 是一个面向渐进式学习和科研论文阅读的资料驱动 AI Skill。它既可以把教材重组为带先修依赖的 Knowledge Atom 图，也可以把科研领域组织为用于批判性阅读与证据综合的导向论文图。

> 当前知识原子尚未真正理解，就绝不推进。  
> 在当前 Knowledge Atom 真正掌握前，不进入下一个知识原子。

## 已实现功能

- 支持从完整教材或知识库、用户大纲，或仅一个主题名词开始
- 为本地资料建立索引，并用 harness Web Search 补齐覆盖缺口
- 通过 RRF 融合 BM25、多语言子词检索和可选的供应商 embedding 检索
- 在稀疏输入进入课程规划前，要求显式证据判定和稳定来源定位
- 从教材、PDF、笔记或多份资料生成 Knowledge Atom DAG
- 严格维持唯一 Active Atom，并执行所有先修守卫
- 分流当前 Atom 问题、阻塞性先修问题、未来问题和 Parking Lot 项目
- 通过 explain/apply/discriminate/transfer/teach-back Evidence 判断掌握程度
- 跨会话恢复状态，并提供 revision 冲突保护和事件审计
- 按 1/3/7/30 天间隔安排复习，同时支持课程级覆盖
- 经用户确认后拆分或合并 Atom，并保留稳定 ID alias
- 将科研领域组织为带角色、阅读依赖和引用关系的论文图
- 围绕单一 Active Paper 完成批判性笔记、主张—证据抽取和跨论文综合
- 分析学习证据，并生成有边界、需审批的课程进化提案
- 从规范化 YAML 状态生成学习、科研和进化视图

## 安装

AtomLearn 需要 Python 3.10+、PyYAML、pypdf 和 python-docx：

```powershell
python -m pip install -e .
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

## 灵活课程输入

AtomLearn 支持三种主要输入模式：`sources` 用于完整教材或知识库，`outline` 用于课程大纲或用户自建结构，`topic` 用于用户只提供领域关键词、概念、技能或名词的情况。三种模式最终都会生成同一套来源可追踪的 Knowledge Atom DAG，但采用不同的资料发现和原子化策略。

```powershell
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

完整资料模式会清点并协调多份材料；大纲模式把大纲条目作为覆盖锚点，而不是最终 Atom 边界；关键词模式会主动进行术语消歧和权威来源发现，不要求学习者自己编写教学大纲。Intake 完成检查会确保每个非归档 Atom 都有来源 locator。起始 payload 模板位于 `atom-learn/assets/templates/intake-*.yaml`。完整方法见[课程输入工作流](atom-learn/references/COURSE_INTAKE.md)。

## RAG 与纠错式 Web Search

AtomLearn 会在每个学习工作区中持久化一个不绑定供应商的 RAG 索引。它可从 TXT、Markdown、RST、HTML、JSON、YAML、CSV、可搜索 PDF 和 DOCX 中提取保留结构的分块。检索通过倒数排名融合（RRF）组合 SQLite FTS5 BM25、多语言子词相似度和可选的供应商 embedding；随后由 harness 对候选证据重排，而不会把融合分数误当成可信度。

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
```

薄弱、缺失或未经验证的大纲/主题要求会以失败关闭，并生成聚焦的 Web Search 查询。harness 打开权威结果后，通过 `rag ingest-web` 仅写入带 URL、检索时间、搜索词、权威等级和稳定 locator 的有限证据段落。只有当前 intake revision 的所有强制锚点都获得显式 `supported` 判定，大纲和主题 intake 才能进入可规划状态。详见[检索与纠错式 Web Search](atom-learn/references/RAG.md)和 [RAG 设计](docs/RAG_DESIGN.md)。

科研领域发现使用同一质量门禁，并为研究问题、综述、方法谱系、评测/数据集以及批评/复现证据生成绑定 research revision 的锚点。构建论文导向的领域地图时使用 `rag requirements --context research`。

## 科研论文阅读

AtomLearn 可以围绕研究问题组织阅读，而不是把论文处理成彼此孤立的摘要。它会建立覆盖综述、奠基工作、理论与方法、基准与数据集、批评与复现以及应用工作的导向地图。每篇完成阅读的论文都会记录有证据支持的主张、局限、开放问题及其与其他工作的关系。

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research import courses/agent-research --input examples/research-mini/plan.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

研究模式最多保留一个 Active Paper，会阻止未完成论文先修的激活、提示缺失的 Knowledge Atom，并生成 `RESEARCH_MAP.md`、`CURRENT_PAPER.md`、`LITERATURE_MATRIX.md` 和 `RESEARCH_GAPS.md`。它不会保存完整论文正文，也不会在缺少最新文献检索时宣称创新性。完整方法见[科研论文阅读工作流](atom-learn/references/RESEARCH_READING.md)。

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
- [科研论文阅读设计](docs/RESEARCH_READING_DESIGN.md)
- [灵活输入设计](docs/INTAKE_DESIGN.md)
- [RAG 设计](docs/RAG_DESIGN.md)

## 开发验证

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py
```

仓库提供微积分、操作系统和一个合成科研阅读计划作为测试夹具。自动测试使用 `.test-workspaces/` 中的独立工作区，不会修改示例文件。
