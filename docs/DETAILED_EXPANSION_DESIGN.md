# “详细讲讲”的原子化展开设计

## 1. 问题

用户要求“把这个知识点详细讲讲”时，传统模型往往把回答长度调高，一次输出定义、动机、推导、例子、边界和应用。表面上信息更完整，实际上重新制造了 AtomLearn 要解决的认知过载。

因此，“详细”不能表示同一轮塞入更多概念，而应表示把当前 Atom 的内部结构显式化。

## 2. 设计目标

- 把一个详细请求转换成持久化子 Atom 树；
- 任意时刻仍只有一个 Active Atom；
- 每个子 Atom 有独立目标与 Evidence；
- 子 Atom 按确定顺序逐个推进；
- 全部子 Atom 通过后，父 Atom 还必须完成整合检查；
- 支持对子 Atom 再次展开；
- 与回退、延后、RAG、考试、科研和知识脉络兼容。

## 3. 双图模型

系统同时维护两类关系：

1. `parent_atom_id` 与 `graph.expansions` 表示“这个细节属于哪个父概念”；
2. `prerequisites` 表示“先掌握什么才能激活什么”。

展开后的顺序固定为：

```text
原先修 -> 子 Atom 1 -> 子 Atom 2 -> ... -> 子 Atom N -> 父 Atom 整合
```

这样展示层可以画树，执行层仍由无环先修 DAG 提供严格门禁。

## 4. 状态机

执行 `expand --confirmed` 后：

- 父 Atom 保存原先修并变为 `locked`；
- 创建 2–12 个同模块子 Atom；
- 子 Atom 1 成为唯一 Active Atom；
- `expansion_stack` 记录当前展开路径；
- 每次子 Atom 掌握后，`assess` 自动激活下一个；
- 最后一个子 Atom 掌握后，父 Atom 以 `integrating` 阶段激活；
- 父 Atom 的新整合 Evidence 通过后，写入 `completed_at`。

父 Atom 原有 Evidence 不会被伪造或迁移为子 Atom Evidence。

## 5. 为什么不复用 split

`restructure split` 用于纠正错误的 Atom 边界，会归档旧 Atom并迁移下游依赖；详细展开则保留父 Atom，因为父概念仍然是学习目标和最终整合单元。两种操作的历史语义不同，不能共用同一状态转换。

## 6. 嵌套展开

如果正在学习的子 Atom 仍包含多个独立目标，可以继续调用 `expand`。系统把新 frame 压入展开栈，先完成最内层子树，再回到外层后续节点。回退栈和展开栈相互独立：前者表示外部先修修复，后者表示父概念内部的细化路径。

## 7. 弹性边界

展开子 Atom 可以通过快速诊断直接获得真实 mastery Evidence，也可以 `defer`；但不能使用 provisional skip。否则父 Atom可能在用户没有逐项证明理解时被解锁，违背详细展开的目的。

延后会清除当前展开焦点但保留树。恢复并重新激活子 Atom 时，系统会从 `parent_atom_id` 重建展开上下文。

## 8. 子系统兼容

- `LEARNING_MAP.md` 按父子关系缩进展示；
- `PROGRESS.md` 显示每棵展开树的子项完成数与整合状态；
- Lineage 结构视图单独报告详细展开树；
- 考试和科研映射若仍指向父 Atom，会通过新的先修闭包自然进入所有子 Atom；
- `import-plan` 保留已有展开关系与计算后的先修；
- Session adaptation 只能改变当前子 Atom 的表达形式，不能把多个子项重新合并为一轮长讲解。

## 9. 安全约束

- 仅允许对 `available` 或 `active` Atom 展开；
- 另有 Active Atom、待评估 Evidence 或已有展开时拒绝；
- 子 ID 必须唯一且不存在；
- 子项顺序由 CLI 计算，payload 不得自定义先修；
- 参与展开的 Atom 不能直接进入 split/merge；
- 所有变更使用 revision 守卫、状态验证和单一审计事件。

## 10. 验证范围

自动测试覆盖只读预览、子图插入、逐项自动激活、父级整合门禁、嵌套展开、跳过拒绝、延后与恢复、导入计划保留展开，以及 Lineage 展示。完整测试还会回归既有掌握、回退、弹性进度、考试、科研和 RAG 行为。
