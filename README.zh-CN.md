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
- 梳理知识根节点、学习主干、分支、枢纽、推导、历史演进、对比、应用及单点来龙去脉
- 严格维持唯一 Active Atom，并执行所有先修守卫
- 允许用户通过快速诊断、延后、暂定跳过或恢复 Atom 灵活调整路径，同时不伪造掌握状态
- 分流当前 Atom 问题、阻塞性先修问题、未来问题和 Parking Lot 项目
- 通过 explain/apply/discriminate/transfer/teach-back Evidence 判断掌握程度
- 跨会话恢复状态，并提供 revision 冲突保护和事件审计
- 按 1/3/7/30 天间隔安排复习，同时支持课程级覆盖
- 经用户确认后拆分或合并 Atom，并保留稳定 ID alias
- 将科研领域组织为带角色、阅读依赖和引用关系的论文图
- 围绕单一 Active Paper 完成批判性笔记、主张—证据抽取和跨论文综合
- 分析往年题和题库，得到来源可追踪的覆盖、难度与样本内重点
- 根据考试重点、学习者 Evidence 和先修关系生成针对性学习或复习队列
- 从隐私安全的 session 信号中适配回答风格、节奏、示例、反馈和科研取向
- 分析学习证据，并生成有边界、需审批的课程进化提案
- 从规范化 YAML 状态生成学习、科研、个性化和进化视图

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

## 知识脉络与概念地图

AtomLearn 将权威的先修 DAG 与来源可追踪的语义层分开。结构视角会自动识别根节点、叶节点、主学习脉络、枢纽、分支与跨模块桥；可选的 Annotation 和类型化 Relation 则解释每个概念的中心问题、角色、贡献、边界、动机、推导、对比和应用，同时不会改变学习先修关系。

```powershell
python atom-learn/scripts/atomlearn.py lineage init courses/calculus
python atom-learn/scripts/atomlearn.py lineage import courses/calculus --input lineage-import.yaml --expected-lineage-revision 0
python atom-learn/scripts/atomlearn.py lineage overview courses/calculus --lens all
python atom-learn/scripts/atomlearn.py lineage trace courses/calculus calculus.derivative.definition --depth 3
python atom-learn/scripts/atomlearn.py lineage route courses/calculus calculus.rate.average calculus.derivative.geometric
```

使用 `overview` 查看领域全图，使用 `trace` 追溯单个概念的来龙去脉，使用 `route` 解释两个概念如何相连。同一张图还可以叠加当前学习状态、题库样本内考试重点，以及已映射论文所需概念。高置信语义关系必须具有已注册来源 locator，而先修 DAG 始终是激活顺序的唯一权威。详见[知识脉络工作流](atom-learn/references/KNOWLEDGE_LINEAGE.md)、[Lineage Schema](atom-learn/references/LINEAGE_SCHEMA.md)和[知识脉络设计](docs/KNOWLEDGE_LINEAGE_DESIGN.md)。

## 弹性进度与跳过

当内容比较简单、用户已经掌握、与当前目标无关，或暂时不想学习时，AtomLearn 会提供三条明确区分的路径。诊断模式只生成最小掌握检查且不修改状态；延后模式把 Atom 移出当前推荐但不解锁后续；暂定跳过模式在用户明确确认后解锁路径，却不会写入 mastery Evidence。所有决定都会显示在进度中，并且可以撤销。

```powershell
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode diagnostic
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode defer --reason-code time_constraint
python atom-learn/scripts/atomlearn.py skip courses/calculus calculus.limit.approach --mode provisional --reason-code already_mastered --confirmed
python atom-learn/scripts/atomlearn.py unskip courses/calculus calculus.limit.approach
```

如果后续学习暴露知识缺口，系统可以回退到已跳过 Atom，并自动撤销原有假设。`strict_mastery` 课程会拒绝暂定绕过；包含假设的课程只能标记为 `completed_with_skips`，且 mastered、skipped 和 deferred 数量始终分开。考试计划可返回 `verify_skip`，科研阅读会显示暂定概念假设，知识脉络视图也会标出跳过和延后节点。详见[弹性进度工作流](atom-learn/references/FLEXIBLE_PROGRESSION.md)和[弹性进度设计](docs/FLEXIBLE_PROGRESSION_DESIGN.md)。

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

## 试题分析与针对性备考

AtomLearn 可以把用户提供的往年题、样题、模拟题或题库整理为来源可追踪的考试语料。每道题会映射到稳定知识点和可选 Knowledge Atom，标注题型与认知要求，并通过透明的五因素量表确定难度。分析会报告跨试卷覆盖、分值占比、样本内重点、置信度以及尚未进入课程图的知识缺口。

```powershell
python atom-learn/scripts/atomlearn.py exam init courses/calculus --title "Calculus Final" --target-date 2027-01-10
python atom-learn/scripts/atomlearn.py exam import courses/calculus --input exam-import.yaml --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam analyze courses/calculus
python atom-learn/scripts/atomlearn.py exam plan courses/calculus --mode mixed --limit 10
```

针对性队列会综合语料重点、学习者当前 Evidence、题目难度和先修顺序，给出 `learn`、`remediate`、`review` 或 `repair_prerequisites` 建议。完整题目与评分细则保留在私有资料/RAG 层；考试规范状态只保存简短摘要和 locator。频率只描述用户提供的样本，绝不会被表述为未来命题预测。详见[试题分析与备考工作流](atom-learn/references/EXAM_PREPARATION.md)和[试题备考设计](docs/EXAM_PREPARATION_DESIGN.md)。

## 基于 Session 的自适应

AtomLearn 可以从聊天 session 中学习长期有效的交互偏好，同时始终以当前请求为最高优先级。Harness 只会把细节程度、解释顺序、示例模式、节奏、反馈风格和科研取向等白名单枚举信号提炼到工作区本地画像中。用户明确表达的偏好会立即生效；行为或结果推断必须获得至少两个不同 session 的交叉印证。

```powershell
python atom-learn/scripts/atomlearn.py adapt guidance courses/calculus --context teaching
python atom-learn/scripts/atomlearn.py adapt observe-session courses/calculus --input adapt-session.yaml --expected-adaptation-revision 0
python atom-learn/scripts/atomlearn.py adapt profile courses/calculus
python atom-learn/scripts/atomlearn.py adapt retire courses/calculus response.detail --reason-code privacy_request --expected-adaptation-revision 1
```

系统禁止存储原始消息、引文、自由文本摘要、敏感特征猜测，也不跨工作区聚合。新的明确纠正会覆盖旧偏好；用户可以停用任意偏好；科研专用指引不会泄漏到教学场景；当前轮指令始终优先，且不会被自动固化。可从 `atom-learn/assets/templates/adapt-session.yaml` 开始，详见[会话自适应](atom-learn/references/SESSION_ADAPTATION.md)和[会话自适应设计](docs/SESSION_ADAPTATION_DESIGN.md)。

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
- [会话自适应设计](docs/SESSION_ADAPTATION_DESIGN.md)
- [试题备考设计](docs/EXAM_PREPARATION_DESIGN.md)
- [科研论文阅读设计](docs/RESEARCH_READING_DESIGN.md)
- [灵活输入设计](docs/INTAKE_DESIGN.md)
- [RAG 设计](docs/RAG_DESIGN.md)
- [知识脉络设计](docs/KNOWLEDGE_LINEAGE_DESIGN.md)
- [弹性进度设计](docs/FLEXIBLE_PROGRESSION_DESIGN.md)

## 开发验证

```powershell
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py atom-learn/scripts/exam.py atom-learn/scripts/lineage.py
```

仓库提供微积分、操作系统和一个合成科研阅读计划作为测试夹具。自动测试使用 `.test-workspaces/` 中的独立工作区，不会修改示例文件。
