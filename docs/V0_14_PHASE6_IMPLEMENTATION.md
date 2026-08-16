# AtomLearn v0.14 Phase 6 实施记录：考试与科研自动闭环

## 1. 交付范围

本阶段实现 `V0_14_REMEDIATION_DESIGN.md` 的 Workstream F、G。目标不是让 Core 假装拥有穷尽文献库或可靠自动难度判断，而是让 harness/provider 的自动化结果进入一条可复核、可追踪、失败关闭的状态链。

## 2. 考试闭环

### 2.1 联合映射

`exam process` 现在分别计算题干、答案和评分细则对课程 Atom 的信号，并保存 joint score、候选列表、证据 locator 和理由。自动映射始终为 `pending`；只有 `confirmed` 或 `corrected` 映射进入 Atom coverage 与备考优先级。`reject` 是显式终态，不会以未映射候选伪装成课程覆盖。

### 2.2 复杂度与难度

题目记录同时保存：

- `structural_complexity`：五因素可解释启发式及其置信度；
- `official_difficulty`：用户提供的官方等级；
- `empirical_difficulty`：正确率、区分度、耗时、IRT 参数、样本数和来源 locator；
- `effective_basis`：`qualified empirical -> official -> structural complexity` 的明确优先级。

经验数据不足 30 次作答时仍可记录，但不成为有效难度。没有官方或合格经验数据时，产品明确称其为结构复杂度，而不是可靠自动难度。

### 2.3 题族与记忆风险

`exam propose-families` 使用规范化题干、候选/确认知识映射和解题结构生成跨试卷候选题族。候选不直接写成已确认 `family_id`。`exam review-families` 支持确认、纠正、拒绝，并可接收带 locator 的 seen/held-out 迁移聚合；只有样本量达到门槛时才输出 `low` 或 `high` memorization risk，否则保持 `unknown`。

### 2.4 每日计划

`exam daily-plan` 接受目标日期、可学习星期、每日分钟数、各任务预计时长、desired retention 和 final review window。输出按日安排的 prerequisite/learn/remediate/review/practice 任务及分钟数。无法容纳全部任务时返回 `infeasible`、未排任务、分钟缺口和调整选项，绝不降低 mastery 门槛来制造可行计划。

## 3. 科研闭环

### 3.1 Protocol、Discovery 与 Screening

`research set-protocol` 独立维护 protocol revision，包括研究问题、范围、日期、语言、文献类型、纳入/排除标准、目标结果和检索限制。

`research discover` 支持 Crossref/OpenAlex 直接检索，或输出供 harness Web Search 使用的 typed action。`research submit-discovery` 校验 action ID、保存 query/filter/provider/result IDs、导入 DOI/title 去重后的候选并保留 integrity provenance。Provider 失败被记录为 `failed`，不会产生覆盖成功声明。

候选必须经过 `research screen`。未确认的模型 include/exclude 建议降级为 `needs_review`；确认排除必须引用 protocol 中预声明的 criterion。状态汇总提供 PRISMA 风格计数，但固定声明它只覆盖有界 provider 结果。

### 3.2 Citation snowball 与 refresh

`research snowball` 为 backward/forward citation expansion 生成带 seed、depth、provider 和停止规则的 action；提交结果后 Core 写入引用边。`research refresh` 根据保存查询和 included papers 生成按需刷新 action，并要求 correction/retraction integrity 字段。刷新命中已有论文时不会把阅读状态重置为 candidate；撤稿或 concern 会阻止论文激活，等待重新筛选。

### 3.3 结构化抽取与跨论文综合

critical note 增加 population、setting、dataset、method、baseline、outcome、metric、assumption facet，以及 effect、uncertainty 和 claim-level evidence locator。Block locator 会对当前 Document IR source revision 和 block ID 做校验；没有句子/表格/figure/equation/block locator 的论文不能完成。

综合不再以 token overlap 作为合并条件。只有结构化 outcome/metric + context facet，或显式论文关系，才会提出跨论文 theme。矛盾保留条件差异和每条 claim 的 locator。Theme 默认 `proposed`，需经 `research review-synthesis` 确认、重命名或拒绝。输出固定声明 corpus 有界，并禁止把 open question 直接称为创新空白。

## 4. 数据契约

新增模板和 JSON Schema：

- `exam-empirical-difficulty.yaml` / `exam-empirical-difficulty.schema.json`；
- `exam-family-review.yaml` / `exam-family-review.schema.json`；
- `exam-daily-plan.yaml` / `exam-daily-plan.schema.json`；
- `research-protocol.yaml` / `research-protocol.schema.json`；
- `research-discovery-submission.yaml` / `research-discovery-submission.schema.json`；
- `research-screening.yaml` / `research-screening.schema.json`；
- `research-synthesis-review.yaml` / `research-synthesis-review.schema.json`。

旧的结构化 exam import 保持兼容；旧 research note 会在加载时补齐空 facet/locator 结构，但已完成论文若没有 claim locator 会失败关闭并要求修复。

## 5. Phase 6 退出条件

- 自动 mapping、family、screening 和 synthesis theme 都能区分候选与已验证状态；
- 没有官方/合格经验数据时不宣称可靠难度；
- 没有完整 provider 搜索时不宣称穷尽检索；
- 没有 claim locator 时不能完成论文或确认综合；
- 不把 unresolved question 表述为已证实创新空白；
- 单元、CLI 集成、schema、能力账本与 release gate 同步验证。

## 6. Provider 依据

- Crossref `works` endpoint 支持 bibliographic query、日期 filter、select 和 list result；AtomLearn 只消费元数据候选并保留 query/filter 日志：[Crossref REST API](https://api.crossref.org/)。
- Crossref 的 update metadata 与 Retraction Watch 数据可通过 `update-to` / retraction filter 获取；Core 将它映射为 integrity 状态，而不是自行判断论文真伪：[Crossref Retraction Watch](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/)。
- OpenAlex `works` search/filter、referenced works 和 cited-by 能力作为 discovery/citation adapter；provider coverage 仍受请求边界限制：[OpenAlex API documentation](https://help.openalex.org/)。

## 7. 验证结果

- Fast suite：`57 passed`；
- 完整 integration suite：`139 passed, 1 skipped, 57 deselected`；skip 仅因为默认环境未安装可选 `scale` HNSW 依赖；
- Phase 6 定向文档、CLI、manager contract、exam、research 回归：`47 passed`；
- `release/gate.py validate-skill`：通过，能力账本为 `14 implemented / 1 experimental / 1 planned`；
- 所有新增 JSON Schema 通过 Draft 2020-12 schema 自检，所有新增 YAML 模板可解析。
