# ADR 0001：Self-Evolution v2 权限与状态边界

| 项目 | 内容 |
| --- | --- |
| 状态 | Accepted |
| 日期 | 2026-08-15 |
| 关联设计 | [Self-Evolution v2](../SELF_EVOLUTION_V2_DESIGN.md) |

## 背景

AtomLearn v1 已有 workspace-local adaptation 和 proposal-only course evolution，但跨课程个性化、策略实验、状态迁移与产品发布尚无统一边界。如果让运行中的课程直接改写 Skill 或把所有信号混入一份画像，会同时破坏更新安全、隐私、可解释性和学习不变量。

## 决策

### 1. Core runtime 只读

学习、适配、实验和课程进化不得写入 Core Skill 安装目录。Core 只能由独立 Release Manager 从已验证的正式 artifact 安装或切换。

### 2. Profile 与 Strategy 分离

`profile` 表达用户喜欢怎样交互；显式偏好可立即生效。`strategy` 表达某种呈现可能改善学习结果；它必须经过低风险实验、结果监测和保守晋升。

### 3. Workspace-local 默认

现有 workspace adaptation 保持默认。跨课程 User Profile 必须显式 opt-in；启用后不自动吸收已有 workspace 历史。每个 workspace 可单独绑定或暂停全局 profile。

### 4. Effective Policy 字段级合并

所有教学消费端使用同一个纯合并器。合并器按字段应用优先级，返回生效值、来源、revision、被忽略候选和 reason code。当前轮明确要求优先于历史偏好，但不能覆盖 protected invariants。

### 5. Migration 是确定性纯函数

每个 namespace 维护独立 schema version。迁移只由注册表中的受测函数完成，先在状态副本上运行，再验证并切换；禁止模型自由改写历史规范状态。

### 6. Manager 与 Course Runtime 隔离

Release Manager 拥有 Core 安装权限，course runtime 没有。Manager 使用版本目录、可信 manifest、artifact 验证、迁移副本、健康检查和恢复指针；稳定 channel 不接受 `main`。

### 7. Capsule 不是遥测

Evolution Capsule 默认只在本地构建。它必须经过严格 schema、privacy lint 和用户预览，显式导出后才成为文件；导出与网络提交是两个不同动作。单个 Capsule 只能触发维护者复现，不能自动改变 Core。

## 权限矩阵

| 写入者 | Core | User Profile | User Strategy | Workspace | Capsule export |
| --- | --- | --- | --- | --- | --- |
| Course runtime | 禁止 | opt-in 后受限 | opt-in 后受限 | 允许 | 仅构建本地候选 |
| Release Manager | 安装/切换 | 仅迁移副本 | 仅迁移副本 | 仅显式选择的迁移副本 | 禁止 |
| 用户命令 | 禁止 | 查看/启停/纠正 | 查看/启停 | 正常学习 mutation | 预览/显式导出 |
| 维护者仓库流程 | 通过 release | 禁止读取 | 禁止读取 | 禁止读取 | 只读取用户主动提供的文件 |

## 后果

正面影响：

- Core 更新不再与个性化数据产生 Git 合并；
- 画像、效果策略和课程变化使用不同证据标准；
- 每轮行为可解释、可复现、可暂停；
- 迁移和更新失败可恢复；
- 产品改进不越过用户授权边界。

代价：

- 需要额外的 user data store、migration engine 和 manager；
- 相同偏好可能在 workspace/global 两层存在，必须有 provenance；
- 策略实验样本积累慢，系统必须接受长期 `monitoring`；
- Release Manager 的供应链测试成本显著高于普通 `git pull`。

## 被拒绝的方案

- 课程运行时直接修改 `SKILL.md` 或 Python：破坏更新、审计和信任边界；
- 在安装目录存用户画像：远程更新会覆盖或冲突；
- 自动把全部 workspace adaptation 汇总为全局画像：没有有效 opt-in；
- 只按完成速度晋升策略：不能证明掌握、迁移和保持；
- 用模型迁移 schema：不可复现，容易改写历史语义；
- 对 Skill 目录执行普通 `git pull`：本地改动和远程版本不可控；
- 默认后台上传 Capsule：与本地优先和显式授权冲突。
