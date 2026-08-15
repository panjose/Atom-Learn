# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn 是一个面向渐进式学习和科研论文阅读的资料驱动 AI Skill。它既可以把教材重组为带先修依赖的 Knowledge Atom 图，也可以把科研领域组织为用于批判性阅读与证据综合的导向论文图。

> 当前知识原子尚未真正理解，就绝不推进。  
> 在当前 Knowledge Atom 真正掌握前，不进入下一个知识原子。

## 已实现功能

- 支持从完整教材或知识库、用户大纲，或仅一个主题名词开始
- 为本地资料建立索引，并用 harness Web Search 补齐覆盖缺口
- 融合 BM25、默认本地 embedding 和可选供应商 embedding，再进行确定性重排
- 在稀疏输入进入课程规划前，要求显式证据判定和稳定来源定位
- 从教材、PDF、笔记或多份资料生成 Knowledge Atom DAG
- 梳理知识根节点、学习主干、分支、枢纽、推导、历史演进、对比、应用及单点来龙去脉
- 严格维持唯一 Active Atom，并执行所有先修守卫
- 把“详细讲讲”转换成有顺序的子 Atom 树，而不是一次输出多概念长讲解
- 允许用户通过快速诊断、延后、暂定跳过或恢复 Atom 灵活调整路径，同时不伪造掌握状态
- 明确陌生关联概念属于当前、前置、后续、可选支线，还是当前目标之外
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
python -m pip install -e ".[dev]"
atomlearn --help
```

可编辑安装会提供更短的 `atomlearn` 控制台命令；把 Skill 目录单独复制后，仍支持直接运行 `python atom-learn/scripts/atomlearn.py ...`。

将仓库中的 `atom-learn` 目录复制或链接到个人 Codex Skills 目录，例如：

```text
~/.codex/skills/atom-learn/
```

开发时，也可以直接让 Codex 使用仓库中的 `atom-learn/SKILL.md`。

## 快速验证

```powershell
atomlearn version
atomlearn migrate status
atomlearn init courses/calculus --course-id calculus --title "Calculus" --goal "Understand derivatives"
atomlearn import-plan courses/calculus --input examples/calculus-mini/plan.yaml --expected-revision 0
atomlearn validate courses/calculus
atomlearn status courses/calculus --json
```

每次课程渲染都会写出五份英文视图和对齐的 `*.zh-CN.md` 中文生成视图，包括 `LEARNING_MAP.zh-CN.md`、`CURRENT.zh-CN.md` 与 `PROGRESS.zh-CN.md`。Atom 标题和学习者内容保持原样；导航标签、状态和操作文字会本地化。完整命令流程和教学行为见 [SKILL.md](atom-learn/SKILL.md)，结构化输入格式见 [SCHEMA.md](atom-learn/references/SCHEMA.md)。运行时课程状态存放在学习者选择的课程工作区，而不是 Skill 安装目录。

Core `0.13.0` 新增只读兼容性 manifest 和确定性迁移规划。`atomlearn migrate status|plan|validate` 不会应用迁移；仅查看状态也不会创建平台用户数据目录。详见 [Core 版本与迁移](atom-learn/references/MIGRATIONS.md)。

跨课程个性化默认关闭，只有学习者明确运行 `atomlearn profile enable <workspace>` 后才启用。全局画像只包含白名单枚举信号，不自动导入旧 workspace 历史，并可在不删除审计记录的情况下停用、退役、导出或重置。`atomlearn policy effective|explain` 会合并当前轮、workspace、全局、策略和 Core 层，并给出逐值来源。详见[用户画像](atom-learn/references/USER_PROFILE.md)和[Effective Policy](atom-learn/references/EFFECTIVE_POLICY.md)。

教学策略实验需要通过 `atomlearn strategy enable-experiments` 再次独立选择加入。候选在实时使用前必须先经过 shadow；分组按 Atom episode 确定性生成；显式偏好会把该次 exposure 排除出比较；只有已评估的 Evidence 才能成为 outcome。晋升必须具有可比层、延迟复习、质量提升并通过 guardrail；暂停会移除 overlay，但不会改写学习历史。详见[策略实验](atom-learn/references/STRATEGY_EXPERIMENTS.md)。

## 灵活课程输入

AtomLearn 支持三种主要输入模式：`sources` 用于完整教材或知识库，`outline` 用于课程大纲或用户自建结构，`topic` 用于用户只提供领域关键词、概念、技能或名词的情况。三种模式最终都会生成同一套来源可追踪的 Knowledge Atom DAG，但采用不同的资料发现和原子化策略。

首次使用通常应走可恢复的 `start` 向导。只提供主题的用户输入一个短语即可；资料和大纲用户只需提供一个经过公开 JSON Schema 校验的 JSON/YAML 文档。向导会创建课程、intake 与 RAG 状态，为资料建立索引，在覆盖不足时返回结构化 Web Search 工作，并在之后通过同一命令接收生成的课程计划。

```powershell
python atom-learn/scripts/atomlearn.py start courses/causal --topic "causal inference"
python atom-learn/scripts/atomlearn.py start courses/calculus --input atom-learn/assets/templates/start-sources.yaml
python atom-learn/scripts/atomlearn.py start courses/calculus --print-schema
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

完整资料模式会清点并协调多份材料；大纲模式把大纲条目作为覆盖锚点，而不是最终 Atom 边界；关键词模式会主动进行术语消歧和权威来源发现，不要求学习者自己编写教学大纲。Intake 完成检查会确保每个非归档 Atom 都有来源 locator。统一起始 payload 位于 `atom-learn/assets/templates/start-*.yaml`，机器可读契约是 [start.schema.json](atom-learn/assets/schemas/start.schema.json)。完整方法见[统一 Start 向导](atom-learn/references/START_WIZARD.md)和[课程输入工作流](atom-learn/references/COURSE_INTAKE.md)。

## RAG 与纠错式 Web Search

AtomLearn 会在每个学习工作区中持久化一个不绑定供应商的 RAG 索引。除 TXT、Markdown、RST、JSON、YAML 和 CSV 外，它还会保留 HTML 与 DOCX 结构、PDF 表格与公式，以及带 locator 的 OCR 输出。检索会融合 SQLite FTS5 BM25、默认本地多语言哈希 embedding 和可选供应商 embedding，再应用可测试的确定性重排器。最终的直接支持判定由 harness 完成；排序分数绝不会被当成可信度。

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
python atom-learn/scripts/atomlearn.py rag correct courses/calculus --input rag-correction.yaml
python atom-learn/scripts/atomlearn.py rag evaluate courses/calculus --input rag-evaluation.yaml
```

`rag correct` 会把薄弱、缺失或未经验证的要求转换成结构化 harness Web Search 任务，写入返回的有限证据，刷新检索，并重复运行，直到门禁通过或仍无法建立支持。`supported` 判定只能引用为该要求实际检索到的候选分块。`rag evaluate` 会根据标注集测量 recall@k、MRR、nDCG@k、引用正确率和无支持主张率。只有当前 intake revision 的所有强制锚点都得到显式支持，大纲和主题 intake 才能进入可规划状态。详见[检索与纠错式 Web Search](atom-learn/references/RAG.md)和 [RAG 设计](docs/RAG_DESIGN.md)。

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

## 原子化详细讲解

当用户要求把某个 Atom 详细讲解、分步骤说明或拆得更细时，AtomLearn 会判断请求是否跨越多个可独立教学和检查的目标。如果是，系统会创建 2–12 个有顺序的子 Atom，而不是一次返回长答案。第一个子 Atom 会立即成为 Active；只有前一个子 Atom 获得 mastered Evidence 后，系统才会激活下一个。

```powershell
python atom-learn/scripts/atomlearn.py expand courses/calculus calculus.derivative.definition --plan expand-derivative.yaml
python atom-learn/scripts/atomlearn.py expand courses/calculus calculus.derivative.definition --plan expand-derivative.yaml --confirmed --expected-revision 4
```

父 Atom 仍然是必须完成的整合目标。所有子 Atom 掌握后，系统会以 `integrating` 阶段激活父 Atom，并要求新的综合检查；通过之前不会解锁下游。子 Atom 还可以继续展开为嵌套树，但任意时刻仍只有一个 Active Atom。快速诊断仍可用于真正 test-out；展开子项不能暂定跳过，但可以延后和恢复。学习图和知识脉络会把包含关系与先修边分开展示。详见[原子化详细展开](atom-learn/references/DETAILED_EXPANSION.md)和[详细展开设计](docs/DETAILED_EXPANSION_DESIGN.md)。

可使用 `atom-learn/assets/templates/expand-plan.yaml` 作为起始 payload。

## 关系感知的概念路由

当讲解中出现用户不懂的关联概念时，AtomLearn 不会立刻开启另一段长讲解，而是先判断关系。系统会展示一张简洁卡片，说明它是当前 Atom 内部内容、必要前置、后续已安排内容、可选拓展，还是当前目标之外的内容，并同时给出判断原因、对进度的影响、目标位置和可选动作。

```powershell
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml --action learn_prerequisite --confirmed --expected-revision 4
python atom-learn/scripts/atomlearn.py route-concept courses/calculus --input concept-route.yaml --action add_optional_branch --confirmed --expected-revision 4
```

预览不会修改状态。确认加入必要前置后，系统会暂时回退学习，并在完成后恢复被打断的 Atom；确认加入可选支线后，它会显示在学习图和知识脉络中，但不会阻塞必修课程完成，也不会排在新的必修内容之前。对于后续已安排内容，系统会明确指出负责它的未来 Atom；一句背景说明不会演变成提前展开的多概念课程。详见[关系感知的概念路由](atom-learn/references/CONCEPT_ROUTING.md)和[概念路由设计](docs/CONCEPT_ROUTING_DESIGN.md)。

可使用 `atom-learn/assets/templates/concept-route.yaml` 作为起始 payload。

## 科研论文阅读

AtomLearn 可以围绕研究问题组织阅读，而不是把论文处理成彼此孤立的摘要。它会规范化 DOI、合并 DOI/标题重复项、验证供应商元数据、从 Crossref/OpenAlex 或 harness 快照获取外向引用关系，并建立角色感知的论文导向地图。完成阅读的论文会形成保留来源的跨论文主张主题，明确保留一致、矛盾、证据强度、局限和 provenance。

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research import courses/agent-research --input examples/research-mini/plan.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research reconcile-metadata courses/agent-research --input research-metadata.yaml --expected-research-revision 1
python atom-learn/scripts/atomlearn.py research fetch-metadata courses/agent-research --provider crossref --expected-research-revision 2
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

研究模式最多保留一个 Active Paper，会阻止未完成论文先修的激活、提示缺失的 Knowledge Atom，并生成 `RESEARCH_MAP.md`、`CURRENT_PAPER.md`、`LITERATURE_MATRIX.md` 和 `RESEARCH_GAPS.md`。元数据冲突与未解析外部引用保持可审计；单一来源的综合主题不会伪装成共识。它不会保存完整论文正文，也不会在缺少最新文献检索时宣称创新性。完整方法见[科研论文阅读工作流](atom-learn/references/RESEARCH_READING.md)。

## 试题分析与针对性备考

AtomLearn 可以把用户提供的往年题、样题、模拟题或题库整理为来源可追踪的考试语料。它会自动切分有编号的题目、关联答案/评分细则、提出可复核的知识点与 Atom 映射，并用透明的五因素量表估计难度；官方锚点可用于校准非官方估计。分析会报告跨试卷覆盖、分值占比、样本内重点、置信度、复核状态以及尚未进入课程图的知识缺口。

```powershell
python atom-learn/scripts/atomlearn.py exam init courses/calculus --title "Calculus Final" --target-date 2027-01-10
python atom-learn/scripts/atomlearn.py exam process courses/calculus --input exam-process.yaml --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam review-mappings courses/calculus --input exam-mapping-review.yaml --expected-exam-revision 1
python atom-learn/scripts/atomlearn.py exam calibrate courses/calculus --expected-exam-revision 2
python atom-learn/scripts/atomlearn.py exam analyze courses/calculus
python atom-learn/scripts/atomlearn.py exam plan courses/calculus --mode mixed --limit 10
# For structured data, use `exam import ... --expected-exam-revision 0` instead of `exam process`.
```

针对性队列会综合语料重点、学习者当前 Evidence、校准后的题目难度和先修顺序，给出 `learn`、`remediate`、`review` 或 `repair_prerequisites` 建议。完整题目、答案与评分细则保留在私有资料/RAG 层；考试规范状态只保存简短摘要、关联和 locator。频率只描述用户提供的样本，绝不会被表述为未来命题预测。详见[试题分析与备考工作流](atom-learn/references/EXAM_PREPARATION.md)和[试题备考设计](docs/EXAM_PREPARATION_DESIGN.md)。

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
- [自进化 v2 设计提案](docs/SELF_EVOLUTION_V2_DESIGN.md)
- [自进化 v2 详细实施方案](docs/SELF_EVOLUTION_V2_IMPLEMENTATION_PLAN.md)
- [自进化 v2 威胁模型](docs/SELF_EVOLUTION_V2_THREAT_MODEL.md)
- [会话自适应设计](docs/SESSION_ADAPTATION_DESIGN.md)
- [试题备考设计](docs/EXAM_PREPARATION_DESIGN.md)
- [科研论文阅读设计](docs/RESEARCH_READING_DESIGN.md)
- [灵活输入设计](docs/INTAKE_DESIGN.md)
- [RAG 设计](docs/RAG_DESIGN.md)
- [Start 向导设计](docs/START_WIZARD_DESIGN.md)
- [知识脉络设计](docs/KNOWLEDGE_LINEAGE_DESIGN.md)
- [弹性进度设计](docs/FLEXIBLE_PROGRESSION_DESIGN.md)
- [详细展开设计](docs/DETAILED_EXPANSION_DESIGN.md)
- [概念路由设计](docs/CONCEPT_ROUTING_DESIGN.md)

## 开发验证

```powershell
python -m pytest -m fast
python -m pytest -m integration
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/wizard.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py atom-learn/scripts/exam.py atom-learn/scripts/lineage.py atom-learn/scripts/platform_state.py atom-learn/scripts/migrations.py atom-learn/scripts/user_profile.py atom-learn/scripts/effective_policy.py atom-learn/scripts/strategy.py
```

快速测试覆盖 CLI/帮助契约、打包、文档、Schema 和确定性辅助逻辑；集成测试覆盖完整的文件系统与子进程工作流。CI 会在 Ubuntu 与 Windows 上使用 Python 3.10、3.11、3.12 和 3.13 运行两层测试。测试使用 `.test-workspaces/` 中的独立工作区，不会修改示例文件。
