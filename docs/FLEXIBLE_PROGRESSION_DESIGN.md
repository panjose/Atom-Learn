# AtomLearn 弹性进度与跳过功能设计

## 1. 设计目标

学习系统不能默认所有用户都从同一起点开始。用户可能已经学过某部分，认为内容过于简单，只想暂时延后，或为了当前目标主动缩小范围。功能需要尊重这些选择，同时避免把一句“我会了”伪造成掌握证据。

因此实现将“教学是否展开”“路径是否继续”“知识是否掌握”拆成三个独立问题。

## 2. 三种模式

### 2.1 Diagnostic

默认模式为只读快速诊断。它返回 Atom 的目标、必测维度、阈值和核心误区，由 Harness 生成覆盖全部必测维度的最短检查。通过后沿用普通 Evidence 和 assess 流程，因此结果是真正的 mastered。

### 2.2 Defer

延后表示“现在不学”。Atom 状态变为 `deferred`，不再进入当前推荐，也不会满足任何先修条件。恢复后根据其先修状态重新计算为 `available` 或 `locked`。

### 2.3 Provisional skip

暂定跳过表示用户明确愿意承担这一知识假设。Atom 状态变为 `skipped`，可以满足后续路径的先修检查，但不会产生 Evidence、置信度或复习计划。执行前必须说明边界并取得 `--confirmed`。

## 3. 为什么不用 mastered 表示跳过

`mastered` 是一个证据结论，必须能追溯到具体 prompt、response summary、维度分数、反馈和评分理由。跳过是用户的路径选择。合并二者会污染学习分析、考试薄弱点、自进化指标和科研阅读的知识缺口判断。

因此系统分别报告 mastered、skipped 和 deferred。全部路径被走完但存在跳过时，课程状态为 `completed_with_skips`，而不是 `completed`。

## 4. 状态与审计

每个 Atom 可以保存一个当前或最近的 `flexibility` 记录，包括模式、规范化原因、简短 note、是否已提供诊断、是否确认、创建时间和撤销时间。

核心课程 revision 覆盖每次状态变更；事件分别记录 `atom.provisionally_skipped`、`atom.deferred` 和 `atom.flexibility_revoked`。重新导入课程计划会保留已有学习状态和弹性决定。

## 5. 先修与恢复

先修满足集合由 mastered、review_due 和 skipped 构成。Deferred 不在其中。`unskip` 会撤销记录并重新计算所有普通可用状态。

若恢复某 Atom 会立即破坏当前 Active Atom 的直接先修约束，命令会失败，要求先离开下游 Active Atom。这样不会生成“正在学习一个先修条件突然失效的 Atom”的不一致状态。

## 6. 后续缺口回退

暂定跳过必须能够接受现实检验。后续答题或论文阅读一旦出现阻塞问题，系统记录 blocking question，并 backtrack 到跳过 Atom。Backtrack 会自动撤销该 skip，然后按普通教学和 Evidence 流程修复；完成后恢复原父 Atom。

如果被跳过 Atom 自身还有未满足先修，系统先提示修复更深层依赖，避免在错误层级直接激活。

## 7. 策略配置

`course.settings.skip_policy` 提供三档策略：

- `diagnostic_first`：默认推荐快速诊断，暂定跳过需明确确认；
- `learner_choice`：用户可以立即选择路线，但披露和确认规则不变；
- `strict_mastery`：禁止暂定绕过，只允许诊断或延后。

认证培训、医疗操作、安全关键流程等应使用 `strict_mastery`。

## 8. 子系统接入

考试模式为已跳过且映射到试题的 Atom 提供 `verify_skip`，以便在重要样本点上快速验证；被延后的 Atom 不进入直接队列，但仍可作为其他目标的未满足先修。

科研阅读把 skipped 概念列入 `provisional_knowledge_atom_ids`，把 deferred 和其他未满足概念保留在 `knowledge_gap_atom_ids`。这样论文可以继续阅读，同时明确知识假设。

知识脉络学习叠层分别输出 skipped 和 deferred 节点。Session 自适应最多调整“何时建议诊断”，不能根据推断自动执行跳过。

## 9. 安全不变量

- 跳过不创建或修改 Evidence；
- skipped 不进入 mastered 统计；
- deferred 不满足先修；
- provisional 必须有确认和规范化原因；
- 所有决定可撤销且经过 revision 冲突保护；
- 后续缺口可以触发正常回退；
- 严格课程可以完全禁止暂定绕过。

## 10. 验证范围

自动化测试覆盖默认诊断不变更状态、缺少确认时 fail-closed、跳过解锁、延后不解锁、恢复重算、strict policy、下游缺口回退、`completed_with_skips` 披露，以及既有课程状态机的回归行为。
