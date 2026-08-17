# AtomLearn v0.15 Phase 1 实施记录

## 结果

Phase 1 完成了通用 Goal Contract、Corpus Policy 和所有 intake 模式的统一 coverage 门禁。`sources`、`outline`、`topic` 现在只表示主要输入与原子化策略，不再决定是否执行覆盖检查。

## 核心契约

- `input_inventory` 独立记录 sources、outline、topic 是否实际存在；混合输入不会因 primary mode 而丢失。
- `goal_contract` 合并学习目标、用途、深度、大纲条目、主题词和显式 mandatory anchors，并由 `goal_contract_revision` 独立版本化。
- `corpus_policy.role` 支持 `full`、`partial`、`supplemental`、`outline_like`、`unknown`。
- `corpus_policy.expansion` 支持 `closed_corpus`、`correct_gaps`、`discover`。
- planning readiness 同时绑定 intake revision、Goal Contract revision 和 RAG revision；任一变化都会让旧 coverage 失效。

## 向导闭环

统一 `start` 流程先检索本地候选并返回 `judge_coverage` action。只有 harness 判断候选为 weak/missing 且 Corpus Policy 允许扩展时，Core 才返回 `web_search` action。

`closed_corpus` 有缺口时返回 `corpus_gap_reported`：

- 不生成 Web Search task；
- 拒绝直接提交 Web evidence；
- 向学习者显示缺失锚点；
- 要求明确选择缩小目标、增加获准材料或改变 Corpus Policy。

Course plan task 现在包含 Goal Contract 和 Corpus Policy。只要输入中存在 outline，包含 mixed input，向导都会注册并索引稳定的 outline source。

## 迁移与兼容

旧 intake state 在读取时进行内存升级，read-only status/guidance 不改写磁盘。旧 sources workspace 默认迁移语义为：

```yaml
corpus_policy:
  role: unknown
  expansion: correct_gaps
  user_confirmed: false
```

旧 `ready_to_plan` 不被信任；没有匹配当前三类 revision 的 coverage report 时，派生状态为 `discovering`。发生下一次显式 intake mutation 时，新字段会随正常事务持久化。

## 验证

本阶段增加或更新了以下回归路径：

- sources intake 在 coverage 前不能进入 planning；
- sources start 先返回本地 candidate judgment；
- closed corpus 缺口不会触发 Web Search；
- sources + outline + topic + explicit anchors 的 mixed input 全部进入同一 Goal Contract；
- legacy sources state 可只读升级且不会被 status 命令改写；
- intake coverage 必须匹配 Goal Contract revision 和 canonical query；
- 科研 coverage 保留可细化 query 的原有契约，不被 intake 限制误伤。

对应设计基线见 [V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md](V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md) 的 Workstream A 与 Phase 1。
