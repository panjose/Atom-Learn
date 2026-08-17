# AtomLearn

[English](README.md) | [简体中文](README.zh-CN.md)

AtomLearn 是一个面向渐进式学习和科研论文阅读的资料驱动 AI Skill。它既可以把教材重组为带先修依赖的 Knowledge Atom 图，也可以把科研领域组织为用于批判性阅读与证据综合的导向论文图。

> 当前知识原子尚未真正理解，就绝不推进。  
> 在当前 Knowledge Atom 真正掌握前，不进入下一个知识原子。

## 已实现功能

- 支持从完整教材或知识库、用户大纲，或仅一个主题名词开始
- 为本地资料建立索引，并用 harness Web Search 补齐覆盖缺口
- 在稳定 base runtime 中融合 BM25 与默认本地多语言哈希投影，接受供应商向量，并且只在开发者/源码安装中提供本地学习型 embedding、HNSW 与重排
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
- 通过受 protocol 约束的论文图发现、筛选、刷新并扩展科研领域引用关系
- 围绕单一 Active Paper 形成 locator 驱动的结构化主张和可复核跨论文综合
- 用已复核联合映射分析往年题，并分开结构、官方与经验难度
- 建立已复核题族和经过容量校验的每日学习、补救、复习与练习计划
- 从隐私安全的 session 信号中适配回答风格、节奏、示例、反馈和科研取向
- 分析学习证据，并生成有边界、需审批的课程进化提案
- 从规范化 YAML 状态生成学习、科研、个性化和进化视图

发布能力的事实来源是机器可读的[能力账本](atom-learn/assets/capabilities.yaml)。“已实现”描述的是仓库代码状态，不等于稳定发行交付状态。账本会分别记录交付等级、runtime、artifact、用户入口、工程验证、harness 行为证据和学习效果证据。签名 `v0.14.2` runtime 只交付 `base` profile；`ocr`、`scale` 和 `semantic` 是开发者/源码 extras，不包含在该稳定 runtime 中。AtomLearn 尚未建立任何学习增益效果结论。工程检查、评分器校准、本地策略实验和 study 记录契约都不得被描述成这种证据。

## 安装

本节命令属于开发者/源码安装，而不是稳定 Manager 的普通用户入口。不要把复制或链接的源码 Skill 与 Manager 所有的 bridge 混用；Manager 会正确拒绝覆盖外部 Skill。统一稳定 bootstrap 与保守源码副本迁移仍属于 v0.15 计划。

AtomLearn 需要 Python 3.10+、PyYAML、pypdf 和 python-docx：

```powershell
python -m pip install -e ".[dev]"
atomlearn --help
```

可编辑安装会提供更短的 `atomlearn` 控制台命令；把 Skill 目录单独复制后，仍支持直接运行 `python atom-learn/scripts/atomlearn.py ...`。

确定性小语料 RAG 路径不需要模型运行时。在开发者/源码环境中，需要自动 OCR adapter 时安装 `.[ocr]`，需要 USearch HNSW generation 时安装 `.[scale]`，需要显式批准的本地 Sentence Transformers 模型时安装 `.[semantic]`。这些 extras 不存在于签名 `v0.14.2` base runtime 中，因此目前还不是稳定发行能力。sidecar OCR 与供应商生成向量的 attachment 仍可通过 base 路径使用。

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

Core `0.14.2` 保留只读兼容性与确定性迁移规划，并加入下文所述的修复门禁。`atomlearn migrate status|plan|validate` 不会应用迁移；仅查看状态也不会创建平台用户数据目录。详见 [Core 版本与迁移](atom-learn/references/MIGRATIONS.md)。

跨课程个性化默认关闭，只有学习者明确运行 `atomlearn profile enable <workspace>` 后才启用。全局画像只包含白名单枚举信号，不自动导入旧 workspace 历史，并可在不删除审计记录的情况下停用、退役、导出或重置。`atomlearn policy effective|explain` 会合并当前轮、workspace、全局、策略和 Core 层，并给出逐值来源。详见[用户画像](atom-learn/references/USER_PROFILE.md)和[Effective Policy](atom-learn/references/EFFECTIVE_POLICY.md)。

教学策略实验需要通过 `atomlearn strategy enable-experiments` 再次独立选择加入。候选在实时使用前必须经过 shadow 和确定性重放；只有与 episode 匹配、预注册且达到 A/B 级的 Evidence 才能成为 outcome。学习、过程、UX 和 guardrail 指标始终分层报告。晋升至少需要每组 10 个可比 outcome、20 个 episode、每组 5 个延迟 outcome、固定种子的 95% 区间、所有主要延迟/迁移学习指标的区间下界超过最小效应，并且所有不良 guardrail 的区间上界不超过容忍度。小样本、宽区间、即时表现、速度或满意度都不能单独触发晋升。详见[策略实验](atom-learn/references/STRATEGY_EXPERIMENTS.md)。

## Evidence v2 与学习测量

Evidence v2 不再把模型给出的数字直接当作掌握证明。每条新记录都携带测量题与 episode 身份、评分器/rubric/校准来源、独立性、本地答案哈希、从 Active Atom 派生出的必需维度分数、质量等级，以及相互独立的 mastery/strategy 资格。Core 内置的确定性精确选择和数值/单位评分器可以生成 A 级 Evidence；注册并经过校准的评分器、真正独立的双评或声明的人工裁决可以取得 B 级资格。未注册、未校准、不独立以及现在才提交的 legacy 分数只能用于反馈，不能独立让 Atom 达到 mastered，也不能进入策略 outcome。

即时掌握、延迟保持、近迁移和远迁移共用一份版本化题库契约。保持与迁移题必须处于 held-out 且上下文隔离。开放题评分器版本需要使用有人工参考分数的版本化校准集，生成可复现的 MAE、偏差、一致率、弃答率、人工复核率、版本漂移、混淆统计和分层报告。独立的 benchmark 协议禁止把工程测试或评分校准宣传成学习增益；学习增益需要经过同意的对照研究以及延迟与迁移测量。详见[Evidence v2 与学习测量](atom-learn/references/MEASUREMENT.md)。

真实学习效果记录需要另一份明确 opt-in。`atomlearn study` 会预注册对照条件、分配方式、缺失数据策略、即时/7 天/30 天/近迁移/远迁移测量和分层；只接受不透明引用与最小化本地观察；禁止原始答案和内容正文；绝不自动导出；撤回后会把所有保留记录排除出分析。仅有记录契约本身绝不会声称存在学习增益。详见[真实学习效果研究](atom-learn/references/LEARNING_EFFECT_STUDY.md)。

```powershell
atomlearn measure registry
atomlearn measure grade --input deterministic-grade.yaml
atomlearn measure validate-bank --input measurement-bank.yaml
atomlearn measure calibrate --input calibration-set.yaml --output calibration-report.json
atomlearn measure validate-protocol
atomlearn migrate-evidence courses/calculus --confirmed --expected-revision 7
atomlearn study enroll study-transfer-pilot --input enrollment.yaml
atomlearn study status study-transfer-pilot
atomlearn study withdraw study-transfer-pilot --confirmed --expected-study-revision 2
```

## 每 Atom 自适应复习

固定的 1/3/7/30 调度仍是默认方案和回退方案。只有通过正常 Active Atom Evidence 流程记录的、延迟发生的、A/B 质量主动回忆，才会更新每个 Atom 的稳定性、可提取性和难度。识别题、被动重读、旧版或不合格评分、满意度、聊天时长，以及单独的回答速度都不能更新记忆状态。回答耗时只保留为审计分桶，不参与 adapter 计算。

`adaptive-shadow` 只计算建议日期，不改变真实队列。`adaptive-active` 必须先通过版本化工程 benchmark，再由学习者明确选择加入，而且只影响之后新建的复习，不会改写已有待复习日期。考试目标会遵守目标日期和最终复习窗口。统一的只读每日队列会在时间与认知负荷容量内组合失败补救、到期复习、阻塞先修、新 Atom 和考试练习；无法容纳的工作会作为可见 backlog 返回。

```powershell
atomlearn review benchmark courses/calculus --expected-revision 7
atomlearn review configure courses/calculus --input atom-learn/assets/templates/review-policy.yaml --expected-revision 8
atomlearn review status courses/calculus
atomlearn review queue courses/calculus --date 2026-08-16 --minutes 60
atomlearn review pilot courses/calculus
```

该 benchmark 验证的是确定性 adapter 不变量，而不是学习增益。workspace pilot 只是观察性回放，始终禁止自动晋升；任何因果学习效果声明都必须进入单独、明确同意的 study 工作流。详见[每 Atom 自适应复习](atom-learn/references/ADAPTIVE_REVIEW.md)和 [Phase 7 实施记录](docs/V0_14_PHASE7_IMPLEMENTATION.md)。

## 灵活课程输入

AtomLearn 支持三种主要输入模式：`sources` 用于完整教材或知识库，`outline` 用于课程大纲或用户自建结构，`topic` 用于用户只提供领域关键词、概念、技能或名词的情况。三种模式最终都会生成同一套来源可追踪的 Knowledge Atom DAG，但采用不同的资料发现和原子化策略。

首次使用通常应走可恢复的 `start` 向导。只提供主题的用户输入一个短语即可；对于资料、大纲和混合输入请求，由 harness 把学习者的一次请求转换为公开 start schema。Core 会派生带 revision 的 Goal Contract 和显式 Corpus Policy，然后为澄清、本地候选覆盖判断、策略允许的 Web Search、规划、阶段确认和首个 Atom 激活返回绑定 revision 的 typed action。学习者不需要编辑中间 YAML，中断后会原样重放当前 action，过期 submission 不能修改更新后的状态。

```powershell
python atom-learn/scripts/atomlearn.py start courses/causal --topic "causal inference"
python atom-learn/scripts/atomlearn.py start courses/calculus --input atom-learn/assets/templates/start-sources.yaml
python atom-learn/scripts/atomlearn.py start courses/calculus --json
python atom-learn/scripts/atomlearn.py start courses/calculus --submission workflow-submission.json --json
python atom-learn/scripts/atomlearn.py start courses/calculus --print-schema
python atom-learn/scripts/atomlearn.py intake init courses/calculus --input intake.yaml
python atom-learn/scripts/atomlearn.py intake guidance courses/calculus
python atom-learn/scripts/atomlearn.py intake update courses/calculus --input discovery-update.yaml --expected-intake-revision 0
python atom-learn/scripts/atomlearn.py import-plan courses/calculus --input course-plan.yaml --expected-revision 0
python atom-learn/scripts/atomlearn.py intake complete courses/calculus --expected-intake-revision 1
```

完整资料模式会清点并协调多份材料；大纲模式把大纲条目作为覆盖锚点，而不是最终 Atom 边界；关键词模式会主动进行术语消歧、记录假设并发现权威来源，不要求学习者自己编写教学大纲。混合输入中的来源、大纲、主题词和显式锚点都会保留在同一个 Goal Contract 中。所有模式在规划前都必须通过候选绑定的 coverage；`closed_corpus` 只报告不受支持的目标而不会 Web Search，`correct_gaps` 和 `discover` 也只有在判断本地候选后才会检索。候选计划在阶段确认前先经过校验，首个可学 Atom 会在激活前单独展示。旧版 sources workspace 会在内存中升级，不能保留旧的 coverage 绕过。统一起始 payload 位于 `atom-learn/assets/templates/start-*.yaml`，机器可读契约包括 [start.schema.json](atom-learn/assets/schemas/start.schema.json)和 typed action/submission schema。完整方法见[统一 Start 向导](atom-learn/references/START_WIZARD.md)、[Typed Workflow Actions](atom-learn/references/WORKFLOW_ACTIONS.md)和[课程输入工作流](atom-learn/references/COURSE_INTAKE.md)。

## RAG 与纠错式 Web Search

AtomLearn 会在每个学习工作区中持久化一个不绑定供应商的 RAG 索引。每个新的 source revision 都会先转换为供检索、考试处理和科研关联共用的版本化、保留布局的 Document IR。除 TXT、Markdown、RST、JSON、YAML 和 CSV 外，稳定 base 还会保留 HTML 与 DOCX 结构、PDF 表格与公式，以及带 locator 的 sidecar OCR 输出。检索会返回精确支持证据的 IR block ID 与有界 parent context，融合 SQLite FTS5 BM25 与默认本地多语言哈希向量，并可 attachment 供应商生成的向量。自动 OCR、显式批准的本地学习型 embedding、USearch HNSW 和 cross-encoder 重排是已实现的开发者/源码路径，不属于签名 `v0.14.2` base runtime 能力。小语料继续使用轻依赖路径；没有已安装且通过验证的 HNSW generation 时，大语料 dense 检索会以零扫描分块的方式跳过该分量。最终的直接支持判定由 harness 完成；排序分数绝不会被当成可信度。

```powershell
python atom-learn/scripts/atomlearn.py rag init courses/calculus
python atom-learn/scripts/atomlearn.py rag ingest courses/calculus --input sources.yaml
python atom-learn/scripts/atomlearn.py rag document-ir courses/calculus calculus-text
python atom-learn/scripts/atomlearn.py rag embed-local courses/calculus --input local-embedding.yaml
python atom-learn/scripts/atomlearn.py rag index-build courses/calculus --kind all
python atom-learn/scripts/atomlearn.py rag search courses/calculus --input query.yaml
python atom-learn/scripts/atomlearn.py rag requirements courses/calculus
python atom-learn/scripts/atomlearn.py rag coverage courses/calculus --input coverage.yaml
python atom-learn/scripts/atomlearn.py rag correct courses/calculus --input rag-correction.yaml
python atom-learn/scripts/atomlearn.py rag evaluate courses/calculus --input rag-evaluation.yaml
python atom-learn/scripts/atomlearn.py rag benchmark courses/rag-benchmark --profile core-multidomain-v1
```

`rag correct` 只有在 Corpus Policy 允许扩展时，才会把薄弱、缺失或未经验证的要求转换成结构化 harness Web Search 任务，写入返回的有限证据，刷新检索，并重复运行，直到门禁通过或仍无法建立支持；`closed_corpus` 会返回显式缺口并拒绝 Web evidence。`supported` 判定只能引用为该要求实际检索到的候选分块。`rag evaluate` 会根据标注集测量 recall@k、MRR、nDCG@k、引用正确率和无支持主张率；如果既没有完整提供五项阈值，也没有指定命名 profile，它会返回 `quality_gate: report_only`，绝不会用宽松默认值推断通过。内置的多领域、多语言、多结构 profile 是非空发布门禁。本地模型绝不会被静默下载；pickle-capable 权重和自定义代码会被拒绝；cross-encoder 只有在当前可移植 benchmark report 通过后才能激活。只有当前 intake、Goal Contract 和 RAG revision 的全部强制锚点都得到显式支持，任一 intake 模式才能进入可规划状态。详见[共享 Document IR](atom-learn/references/DOCUMENT_IR.md)、[检索与纠错式 Web Search](atom-learn/references/RAG.md)、[学习型语义与规模 RAG](atom-learn/references/SEMANTIC_RAG.md)和 [RAG 设计](docs/RAG_DESIGN.md)。

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

AtomLearn 可以围绕带 revision 的科研 protocol 组织阅读，而不是把论文处理成彼此孤立的摘要。Crossref、OpenAlex 或 harness Web Search 发现的候选会先经过 DOI/标题去重与显式 screening；有界的前后向引文扩展和按需 integrity refresh 会保留 provider provenance。完成阅读的论文以 claim-level locator 以及 population、dataset、method、outcome、metric、assumption 等结构化 facet 进入可复核的跨论文主题。

```powershell
python atom-learn/scripts/atomlearn.py init courses/agent-research --course-id agent.research --title "Agent Research" --goal "Map reliable research agents"
python atom-learn/scripts/atomlearn.py research init courses/agent-research --field "Reliable autonomous research agents" --question "Which design choices improve reliability?"
python atom-learn/scripts/atomlearn.py research set-protocol courses/agent-research --input research-protocol.yaml --expected-research-revision 0
python atom-learn/scripts/atomlearn.py research discover courses/agent-research --provider harness --query "reliable autonomous research agents" --expected-research-revision 1
python atom-learn/scripts/atomlearn.py research submit-discovery courses/agent-research --input research-discovery-submission.yaml --expected-research-revision 2
python atom-learn/scripts/atomlearn.py research screen courses/agent-research --input research-screening.yaml --expected-research-revision 3
python atom-learn/scripts/atomlearn.py research snowball courses/agent-research paper.field.survey --direction backward --stopping-rule "one depth or 50 candidates"
python atom-learn/scripts/atomlearn.py research refresh courses/agent-research --provider harness
python atom-learn/scripts/atomlearn.py research next courses/agent-research
python atom-learn/scripts/atomlearn.py research status courses/agent-research
```

研究模式最多保留一个 Active Paper，要求确认纳入，并阻止 integrity 警报或未完成论文先修的激活；它会生成 `RESEARCH_MAP.md`、`CURRENT_PAPER.md`、`LITERATURE_MATRIX.md` 和 `RESEARCH_GAPS.md`。已建立索引的论文可以关联到共享 Document IR 而不复制全文，claim block locator 会对 source revision 验证。模型 screening 和 synthesis 输出在复核前都只是 proposal。PRISMA 风格计数只描述有界结果，open question 也不会在缺少当前文献验证时变成创新性声明。详见[科研论文阅读工作流](atom-learn/references/RESEARCH_READING.md)和[Phase 6 实施记录](docs/V0_14_PHASE6_IMPLEMENTATION.md)。

## 试题分析与针对性备考

AtomLearn 可以把用户提供的往年题、样题、模拟题或题库整理为来源可追踪的考试语料。它会联合题干、答案和 rubric 证据提出可复核 Atom 映射，pending 映射不会计入覆盖率；它会分开五因素结构复杂度、官方难度和带来源的经验难度，提出可复核的跨试卷题族、测量 held-out 迁移风险，并生成经过容量校验的每日计划。

```powershell
python atom-learn/scripts/atomlearn.py exam init courses/calculus --title "Calculus Final" --target-date 2027-01-10
python atom-learn/scripts/atomlearn.py exam process-source courses/calculus --source-id past-paper --paper-id paper-2026 --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam process courses/calculus --input exam-process.yaml --expected-exam-revision 0
python atom-learn/scripts/atomlearn.py exam review-mappings courses/calculus --input exam-mapping-review.yaml --expected-exam-revision 1
python atom-learn/scripts/atomlearn.py exam calibrate courses/calculus --expected-exam-revision 2
python atom-learn/scripts/atomlearn.py exam record-empirical courses/calculus --input exam-empirical-difficulty.yaml --expected-exam-revision 3
python atom-learn/scripts/atomlearn.py exam propose-families courses/calculus --expected-exam-revision 4
python atom-learn/scripts/atomlearn.py exam review-families courses/calculus --input exam-family-review.yaml --expected-exam-revision 5
python atom-learn/scripts/atomlearn.py exam analyze courses/calculus
python atom-learn/scripts/atomlearn.py exam plan courses/calculus --mode mixed --limit 10
python atom-learn/scripts/atomlearn.py exam daily-plan courses/calculus --input exam-daily-plan.yaml
# For structured data, use `exam import ... --expected-exam-revision 0` instead of `exam process`.
```

`exam process-source` 会消费 RAG 共用的同一份 Document IR，并在规范状态只保存简短摘要、关联和 locator 的同时保留准确 block provenance。经验难度只有在至少 30 次且带来源 locator 的作答聚合下才会生效；否则产品只表述官方难度或结构复杂度。题族必须复核，`memorization_risk` 描述 seen/held-out 迁移而不判断用户动机。无法容纳全部任务的日计划会返回 `infeasible` 和工作量缺口，不会降低 mastery。频率只描述用户提供的样本，绝不是未来命题预测。详见[试题分析与备考工作流](atom-learn/references/EXAM_PREPARATION.md)和[Phase 6 实施记录](docs/V0_14_PHASE6_IMPLEMENTATION.md)。

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

如果学习者明确选择分享产品级发现，`evolve capsule` 可以构建仅含枚举与分桶数据的本地 Capsule，执行隐私 lint，展示完整预览，并进行一次性、经确认的文件导出。导出绝不等于上传；系统没有 submit 或 telemetry 命令；维护者转换后也必须先建立独立复现测试，才能按常规评审流程修改 Core。详见 [Evolution Capsule](atom-learn/references/EVOLUTION_CAPSULE.md)。

### 签名 Release Manager

Core 更新由独立的 `atomlearn-manager` 发行包负责，学习 session 永远不能执行更新。Manifest v2 把签名 Core、固定 Codex bridge 协议、能力 smoke 契约、完整离线 wheelhouse 配方，以及每个受支持 OS/Python 目标的隔离 runtime 绑定为同一发行身份。launcher 始终使用 active release 自己的 runtime；只有状态副本迁移和能力感知 smoke 测试全部通过后才会激活。更新失败或中断时旧 Core 与其 runtime 都会保留；回滚仍只允许配套的上一版 release 与对应状态快照。

```powershell
python -m pip install -e ./manager
atomlearn-manager init --trust-bundle release/atomlearn-trust-bundle.json --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222
atomlearn-manager codex install
atomlearn-manager codex status
atomlearn-manager update status
atomlearn-manager update recover
atomlearn-core version
```

公开 release 无需 credential。私有 GitHub Release 会先尝试公开 URL，再使用 `ATOMLEARN_GITHUB_TOKEN`、`GH_TOKEN` 或 GitHub CLI credential helper；token 不会写入 manifest、workspace 或 URL。指纹核验、密钥轮换、bridge 修复、更新计划、runtime 构建、恢复、回滚和传输边界详见[签名 Release Manager](atom-learn/references/RELEASE_MANAGER.md)。

所有自进化 v2 能力仍然默认关闭，并且可以分别安全退出。加固后的 tag-only 发布流水线要求 Windows/Linux Python 3.10–3.13、属性测试、replay 与 v1 兼容性、迁移夹具、覆盖更新全部阶段的故障注入、独立 Capsule 隐私攻击语料、包含自适应复习的能力 smoke 以及签名 gate report 全部通过，才允许发布 stable assets。详见[操作与恢复手册](docs/SELF_EVOLUTION_V2_OPERATIONS.md)、[0.14.2 Release Notes](docs/releases/v0.14.2.md)和[Changelog](CHANGELOG.md)。

## 设计文档

- [产品与技术设计](docs/PRODUCT_DESIGN.md)
- [v0.15 产品就绪修复设计](docs/V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md)
- [详细实施方案](docs/IMPLEMENTATION_PLAN.md)
- [v0.14 Phase 6 考试与科研实施记录](docs/V0_14_PHASE6_IMPLEMENTATION.md)
- [v0.14 Phase 7 自适应复习实施记录](docs/V0_14_PHASE7_IMPLEMENTATION.md)
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
- [签名 Release Manager 操作说明](atom-learn/references/RELEASE_MANAGER.md)
- [自进化 v2 操作与恢复](docs/SELF_EVOLUTION_V2_OPERATIONS.md)
- [0.13.0 Release Notes](docs/releases/v0.13.0.md)
- [0.14.2 Release Notes](docs/releases/v0.14.2.md)

## 开发验证

```powershell
python -m pytest -m fast
python -m pytest -m integration
python -m pytest
python -m py_compile atom-learn/scripts/atomlearn.py atom-learn/scripts/wizard.py atom-learn/scripts/workflow.py atom-learn/scripts/document_ir.py atom-learn/scripts/evolution.py atom-learn/scripts/research.py atom-learn/scripts/intake.py atom-learn/scripts/rag.py atom-learn/scripts/adaptation.py atom-learn/scripts/exam.py atom-learn/scripts/lineage.py atom-learn/scripts/platform_state.py atom-learn/scripts/migrations.py atom-learn/scripts/user_profile.py atom-learn/scripts/effective_policy.py atom-learn/scripts/strategy.py atom-learn/scripts/strategy_analysis.py atom-learn/scripts/learning_study.py atom-learn/scripts/capsule.py atom-learn/scripts/measurement.py manager/atomlearn_manager/cli.py manager/atomlearn_manager/manager.py manager/atomlearn_manager/builder.py manager/atomlearn_manager/verify.py manager/atomlearn_manager/statecopy.py manager/atomlearn_manager/launcher.py release/gate.py
```

快速测试覆盖 CLI/帮助契约、打包、文档、Schema 和确定性辅助逻辑；集成测试覆盖完整的文件系统与子进程工作流。CI 会在 Ubuntu 与 Windows 上使用 Python 3.10、3.11、3.12 和 3.13 运行两层测试。测试使用 `.test-workspaces/` 中的独立工作区，不会修改示例文件。
