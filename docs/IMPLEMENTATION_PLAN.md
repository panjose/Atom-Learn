# AtomLearn 详细实施方案

| 项目 | 内容 |
| --- | --- |
| 方案版本 | v0.1 |
| 更新时间 | 2026-08-13 |
| 基线设计 | [PRODUCT_DESIGN.md](PRODUCT_DESIGN.md) |
| 建议策略 | 先状态正确，后扩大自动化；先单课程闭环，后多课程平台化 |

## 1. 交付目标

首个可用版本交付一个可安装的 `atom-learn` Codex Skill。它能在用户指定目录中创建独立学习工作区，从少量本地资料建立知识地图，严格执行单 Active Atom 协议，持久化问题与掌握证据，并跨会话恢复。

### Definition of Done

- Skill 目录通过官方 `quick_validate.py`；
- 新工作区可通过一个命令初始化，且不写入 Skill 安装目录；
- 10–30 Atom 的示例课程能通过完整学习闭环；
- DAG、状态转换、证据要求、单 Active Atom 均有自动测试；
- `LEARNING_MAP.md`、`CURRENT.md`、`PROGRESS.md`、`QUESTIONS.md`、`SOURCES.md` 可重复生成；
- 至少完成数学和计算机科学两个端到端夹具；
- 从空对话开始，Codex 能仅靠 Skill 与工作区恢复正确状态；
- 私有来源和运行时课程数据默认不会被 Git 意外提交。

## 2. 目标仓库结构

```text
Atom-Learn/
├── README.md
├── docs/
│   ├── PRODUCT_DESIGN.md
│   └── IMPLEMENTATION_PLAN.md
├── atom-learn/
│   ├── SKILL.md
│   ├── agents/
│   │   └── openai.yaml
│   ├── scripts/
│   │   └── atomlearn.py
│   ├── references/
│   │   ├── PROTOCOL.md
│   │   ├── SCHEMA.md
│   │   ├── ATOMIZATION.md
│   │   ├── QUESTION_ROUTING.md
│   │   └── MASTERY.md
│   └── assets/
│       └── templates/
│           ├── course.yaml
│           ├── graph.yaml
│           ├── atom.yaml
│           ├── current.yaml
│           ├── questions.yaml
│           └── reviews.yaml
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── examples/
│   ├── calculus-mini/
│   └── operating-systems-mini/
├── pyproject.toml
└── .github/workflows/validate.yml
```

Skill 包内不加入 README、安装指南或变更日志；这些项目级信息保留在仓库根目录和 `docs/`。`SKILL.md` 控制在 500 行以内，详细量规按需加载。

## 3. 技术基线

### 3.1 推荐技术选择

| 层 | 选择 | 原因 |
| --- | --- | --- |
| Skill 指令 | Markdown | Codex 原生、易审阅、易迭代 |
| 规范状态 | YAML + NDJSON 事件日志 | 人类可读、结构明确、适合版本迁移 |
| 状态 CLI | Python 3 标准库，必要时加 PyYAML | 跨平台、适合确定性校验与渲染 |
| Schema | JSON Schema 或代码内版本化校验 | 捕获格式错误并支持迁移 |
| 测试 | pytest | 参数化状态机和端到端夹具方便 |
| 包质量 | Ruff + pytest | 快速、配置简单 |
| CI | GitHub Actions | 对 push/PR 自动校验 Skill 与测试 |

若运行环境没有 PyYAML，优先使用工作区已提供依赖；不要为 MVP 自写不完整 YAML 解析器。脚本的文件写入采用同目录临时文件、校验、原子替换的顺序。

### 3.2 CLI 边界

建议用一个 `atomlearn.py` 提供子命令，减少脚本间重复：

```text
atomlearn init <workspace> --course-id <id>
atomlearn validate <workspace>
atomlearn render <workspace>
atomlearn activate <workspace> <atom-id>
atomlearn pause <workspace> --reason <text>
atomlearn record-question <workspace> --input <json-file>
atomlearn record-evidence <workspace> --input <json-file>
atomlearn assess <workspace> <atom-id> --result <result> --evidence-id <id>
atomlearn schedule-review <workspace> <atom-id>
atomlearn restructure <workspace> --proposal <yaml-file> --confirmed
atomlearn status <workspace> --json
```

CLI 只执行确定性操作。它不负责理解教材、不自行判断问题类型，也不自行给答案评分；这些判断由 Codex 形成结构化输入，CLI 验证输入后落盘。

## 4. 分阶段实施

工作量按一名熟悉 Python 与 Codex Skill 的开发者估算，用于排优先级而不是承诺日历日期。

### Phase 0：仓库与工程骨架（0.5–1 天）

#### 任务

- 使用 `skill-creator/scripts/init_skill.py` 初始化 `atom-learn`；
- 生成匹配 Skill 的 `agents/openai.yaml`；
- 建立 `tests/`、`examples/`、`docs/` 与 CI 骨架；
- 配置 Python、pytest、Ruff 和跨平台换行/编码；
- 建立 `.gitignore`，排除本地教材、临时课程和测试缓存；
- 写一个只检查骨架的 CI 作业。

#### 验收

- `quick_validate.py atom-learn` 通过；
- 空测试套件与 lint 在 Windows、本地及 GitHub Actions 上可运行；
- `agents/openai.yaml` 的名称、简介和默认提示与 `SKILL.md` 一致；
- 仓库不存在真实用户教材或学习状态。

### Phase 1：状态契约、Schema 与模板（2–3 天）

#### 任务

1. 定义 `schema_version: 1`；
2. 完成 Course、Source、Atom、Graph、Session、Question、Evidence、Review 和 Event schema；
3. 固化 Atom 生命周期、Session 阶段和允许转换表；
4. 定义稳定 ID、别名、时间戳与 source locator 格式；
5. 实现 `init`、`validate`、`status`、`render`；
6. 创建最小模板与一个手写 5 Atom 夹具；
7. 实现工作区级校验：
   - DAG 无环；
   - 依赖目标存在；
   - 最多一个 Active Atom；
   - Active Atom 先修满足；
   - mastered 必须引用 Evidence；
   - 问题、复习和 alias 不得悬空；
   - schema 版本受支持。

#### 验收

- 初始化两次不会覆盖已有课程；
- 对同一状态连续 render 两次没有语义 diff；
- 每一种非法状态都有失败测试和清晰错误信息；
- 手动中断写入不会留下部分更新；
- 五个 Markdown 视图均由同一规范状态生成。

### Phase 2：Orientation 与知识建图（3–5 天）

#### 任务

1. 在 `PROTOCOL.md` 定义资料接收与课程访谈流程；
2. 在 `ATOMIZATION.md` 定义概念抽取、去重、粒度判断与 DAG 规则；
3. 设计来源清单：本地路径、文件类型、版本、hash、定位方法、可引用范围；
4. 让 Codex 分批生成候选模块、Atom 和依赖，不一次处理超大课程；
5. 添加术语 alias 与来源冲突模型；
6. 对无来源的 AI 补充内容设置显式 `source_type: synthesized`；
7. 生成并校验小型知识图；
8. 设计已有知识诊断，允许用户确认或以 Evidence 标记跳过；
9. 只在图合法后激活入口 Atom。

#### 验收

- 同一概念被多本教材覆盖时只生成共享 Atom，并保留多个 locator；
- 环依赖会被发现并要求修正，不能进入教学阶段；
- 每个 Atom 都有单一目标、先修、来源状态和掌握量规；
- Orientation 输出地图摘要而不是立即长篇授课；
- 30 Atom 夹具可在合理上下文内分批构建和恢复。

### Phase 3：单 Atom 教学引擎（3–4 天）

#### 任务

1. 编写精简 `SKILL.md`，规定每轮必读状态和工具顺序；
2. 在 `PROTOCOL.md` 实现 Why → What → How → Example → Intuition 教学框架；
3. 定义响应长度、一次只引入必要概念和显式下一动作规则；
4. 实现 `activate`、`pause`、`resume` 与回溯栈状态动作；
5. 每轮结束写入 learner understands/confusions 和 next action；
6. 对“不存在工作区”“状态损坏”“来源不可访问”提供恢复路径；
7. 添加跨会话恢复场景。

#### 验收

- 连续五轮追问后 Active Atom 不变；
- 新会话能仅凭 `status` 输出准确恢复当前学习位置；
- 尝试激活锁定 Atom 被拒绝；
- 教学不主动覆盖未解锁的后续 Atom；
- 临时回溯完成后能返回原 Atom 与原问题。

### Phase 4：问题路由与 Mastery Check（3–4 天）

#### 任务

1. 在 `QUESTION_ROUTING.md` 为五种问题类型编写判定量规与边界示例；
2. 记录问题原文、归属、分类理由、优先级和解决状态；
3. 对 blocking prerequisite 实现暂停、回溯、解决、返回闭环；
4. 对 non-blocking/future 问题实现 Parking Lot 和关联 Atom；
5. 在 `MASTERY.md` 定义 explain/apply/discriminate/transfer/teach-back 量规；
6. 实现 Evidence 持久化与 `assess` 状态守卫；
7. 对 partial/not_mastered 生成针对性补救动作；
8. 禁止无 Evidence 的 mastered 更新。

#### 验收

- 覆盖五类问题的至少 20 个路由测试案例；
- 阻塞问题能补救并返回，非阻塞问题不更改 Active Atom；
- “我懂了”不会单独触发 mastered；
- Evidence 能说明问了什么、用户表现如何、哪一维度不足；
- 检查失败不会意外解锁后继 Atom。

### Phase 5：进度、复习与动态重构（2–4 天）

#### 任务

1. 从图和 Evidence 计算总进度、章节进度和可用节点；
2. 实现 1/3/7/30 天默认复习策略和课程级覆盖；
3. 实现 review_due、复习 Evidence 和置信度调整；
4. 定义置信度为可解释的派生指标，而非无来源主观小数；
5. 实现拆分/合并 proposal schema；
6. 以 `--confirmed` 守卫结构变更；
7. 迁移旧 ID、来源、问题、Evidence 和依赖，写入 alias 与 Event；
8. 重构后重新生成所有 Markdown 视图。

#### 验收

- 时间冻结测试能稳定验证复习到期日；
- 复习失败会产生补救任务但保留历史 Evidence；
- 拆分或合并不会产生悬空引用或丢失历史；
- 未确认的重构只能生成提案，不能改变课程；
- 进度数值可从规范状态重新计算，不依赖缓存。

### Phase 6：测试、前向验证与加固（3–5 天）

#### 自动测试矩阵

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | ID、schema、转换表、DAG、解锁、复习日期、渲染 |
| 性质测试 | 任意合法操作序列后仍只有一个 Active Atom、无环、无悬空引用 |
| 集成测试 | 初始化 → 建图 → 教学状态 → 提问 → 检查 → 推进 → 恢复 |
| 快照测试 | 五个 Markdown 投影稳定且可读 |
| 故障测试 | 中断写入、损坏 YAML、未知 schema、来源缺失 |
| Skill 验证 | `quick_validate.py`、UI metadata 一致性、SKILL.md 行数 |

#### 前向验证场景

在独立、最少上下文的会话中实际调用 Skill：

1. 用微积分资料建立课程并学习“导数的形式化定义”；
2. 用操作系统资料学习“程序与进程的区别”，中途询问线程；
3. 在当前 Atom 中制造阻塞性先修缺口并测试回溯；
4. 让学习者错误宣称“懂了”，观察是否仍执行验证；
5. 模拟会话中断后恢复；
6. 制造过大的 Atom，测试拆分提案和确认守卫。

只向验证会话提供 Skill、原始资料和用户任务，不泄露预期答案。保存原始输出和状态 diff，按失败类型修订 Skill。若前向验证会改动真实系统或耗时显著，先取得用户同意。

#### 验收

- 所有自动测试通过；
- 两个学科夹具均完成完整闭环；
- 无上下文泄漏的前向验证能维持核心不变量；
- 失败日志能够定位到语义判断、状态动作或渲染中的具体层；
- Windows 路径、UTF-8 中文和 CRLF/LF 场景均验证。

### Phase 7：打包与 MVP 发布（1–2 天）

#### 任务

- 校准 description，使“教材建图、渐进学习、学习状态恢复”等触发语义明确；
- 重新生成并校验 `agents/openai.yaml`；
- 收紧 SKILL.md，只留下执行必需内容；
- 清理夹具中的私有路径、教材内容和用户数据；
- 为根 README 补充安装、使用示例、版本限制与数据位置；
- 标记 `v0.1.0`，记录已知限制和 schema 兼容范围。

#### 验收

- 从全新环境安装后可初始化并完成示例 Atom；
- 更新 Skill 不会改变已有课程工作区；
- 包中没有缓存、临时文件或真实学习数据；
- 用户能明确知道数据存在哪里、如何备份和如何验证。

## 5. 关键接口与数据约束

### 5.1 状态动作请求

Codex 通过文件或标准输入向 CLI 传递结构化请求，避免复杂文本直接拼接进命令行。请求至少包含：

```yaml
action: record_question
course_id: calculus
expected_revision: 17
payload:
  text: 为什么 delta_x 不能直接等于 0？
  classification: in_atom
  related_atom_id: calculus.derivative.definition
  rationale: 该问题直接针对当前定义中的极限机制
```

`expected_revision` 用于发现并发或陈旧上下文。成功变更递增 revision；不匹配时拒绝写入并要求重新读取状态。

### 5.2 Event 约束

每次成功变更追加一条 NDJSON Event：

```json
{"event_id":"evt-000018","revision":18,"type":"question.recorded","at":"2026-08-13T10:10:00+08:00","actor":"codex","atom_id":"calculus.derivative.definition","reason":"learner question"}
```

Event 日志用于审计和调试，不作为每次恢复时重放全部状态的唯一机制。当前快照是启动路径，事件是解释路径。

### 5.3 写入事务

单次状态动作应按以下顺序执行：

1. 读取所有相关文件并校验 revision；
2. 在内存中应用动作；
3. 对完整候选状态执行 schema、图和业务规则校验；
4. 将变更写到工作区内的临时文件；
5. 原子替换规范状态文件；
6. 追加 Event；
7. 重新渲染 Markdown 投影；
8. 再次执行只读 validate 并返回摘要。

若第 4 步之后失败，下一次运行应检测临时文件并提供恢复，而不是静默使用混合 revision。

## 6. 测试用例清单

### 6.1 必测状态守卫

- 空课程不能进入 teaching；
- 两个 active Atom 被拒绝；
- 锁定 Atom 不能激活；
- 没有 Evidence 不能 mastered；
- mastery 检查失败不能推进；
- 删除或改名被引用的 Atom 必须通过 alias/migration；
- DAG 环和自依赖被拒绝；
- 未知问题类型、状态和 schema 版本被拒绝；
- stale revision 不得覆盖新状态；
- render 不得反向改变规范状态。

### 6.2 必测教学行为

- 一次只处理 Active Atom；
- 连续追问不丢主线；
- future Atom 问题被记录而非提前讲授；
- blocking prerequisite 能建立回溯栈；
- 回溯完成后恢复原问题；
- 学习者自报掌握仍需检查；
- 错误答案获得针对性反馈，不直接重复整段讲解；
- 来源冲突被明确指出；
- 无来源补充被标记为 synthesized；
- 跨会话能恢复 next action 和 learner confusions。

## 7. 里程碑与发布闸门

| 里程碑 | 完成标志 | 不满足时不得进入 |
| --- | --- | --- |
| M1：状态内核 | schema、DAG、转换与 render 全通过 | 自动建图 |
| M2：小型建图 | 30 Atom、多来源、无环且可追踪 | 自主教学 |
| M3：教学闭环 | 追问、检查、Evidence、推进、恢复完成 | 间隔复习 |
| M4：自适应闭环 | 复习和确认式拆并完成 | MVP 发布 |
| M5：发布候选 | 双学科前向验证与全新安装通过 | v0.1.0 |

建议每个里程碑都保留一个可运行、可回滚的版本，不在状态内核未稳定时同时开发 UI。

## 8. 风险优先级

### P0：发布前必须解决

- 状态写坏或升级覆盖用户数据；
- 多 Active Atom、无 Evidence 掌握或违反先修守卫；
- 来源定位伪造；
- Windows/中文路径导致工作区不可恢复。

### P1：MVP 应解决

- Atom 粒度明显失衡；
- 问题路由经常打断主线；
- Markdown 投影与规范状态不一致；
- 大资料一次加载导致上下文耗尽。

### P2：可在 v0.2 以后优化

- 高级复习算法；
- 图形化知识地图；
- 多设备同步与多人课程；
- 学习分析仪表盘；
- 自动化大规模课程市场。

## 9. 建议 Issue 拆分

实施时建议按可独立验收的 Issue 推进：

1. Scaffold the `atom-learn` skill package
2. Define schema v1 and workspace fixtures
3. Implement DAG and workspace validation
4. Implement atomic state transactions and revisions
5. Render the five Markdown views
6. Define orientation and atomization protocols
7. Implement activation, pause, backtrack and resume
8. Define and persist question routing
9. Define mastery rubrics and Evidence
10. Implement unlock and next-Atom selection
11. Implement spaced review scheduling
12. Implement confirmed split/merge migrations
13. Add calculus end-to-end fixture
14. Add operating-systems end-to-end fixture
15. Add forward-test harness and CI
16. Package and validate v0.1.0

每个 Issue 都应包含测试或可观察验收结果，避免只以“Prompt 已写完”作为完成标准。

## 10. MVP 之后的演进

### v0.2：稳健性与效率

- schema 自动迁移、工作区备份与恢复；
- 分批 Orientation 和增量知识图扩展；
- 自适应复习间隔；
- 更细的来源冲突与置信度模型；
- 大课程的上下文检索和 Atom 懒加载。

### v0.3：体验层

- 知识 DAG 可视化；
- 当前 Atom、Parking Lot、复习队列仪表盘；
- 用户可视化编辑依赖与 Atom 边界；
- 学习数据导出和跨设备同步。

### v1.0：生态与兼容性

- 稳定 schema 与迁移承诺；
- 可共享课程包，但不包含用户 Evidence；
- 学科专用 atomization/mastery profiles；
- 插件化来源解析器和外部知识库连接器；
- 完整隐私、版权与数据生命周期说明。

## 11. 第一轮建议执行顺序

下一次进入实现阶段时，严格按以下顺序开始：

1. 用 `init_skill.py` 创建合规 Skill 骨架；
2. 先写 schema、合法转换表和五个失败测试；
3. 实现 `init`、`validate`、`render`，建立最小闭环；
4. 再编写教学协议和语义量规；
5. 用 5 Atom 手写课程验证单 Active Atom；
6. 扩展到两个真实学科的 10–30 Atom 夹具；
7. 最后加入复习、动态重构和发布打包。

这样可以最早验证 AtomLearn 最关键的价值：不是内容讲得多，而是在长时间、长对话和大量分支中仍然保持学习状态正确、主线清晰、推进有据。

