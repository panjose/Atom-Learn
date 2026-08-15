# AtomLearn Self-Evolution v2 威胁模型

## 目录

- 资产与信任边界
- 攻击者与入口
- 威胁清单
- 验证策略
- 发布前安全闸门

## 1. 资产与信任边界

需要保护的资产：

- Core Skill 的代码、协议、默认策略和 release manifest；
- 用户全局 Profile 与 Strategy；
- workspace 内的 Atom、Evidence、资料 locator 和演进历史；
- migration transaction、旧版本恢复点和 active version pointer；
- 用户主动构建但尚未导出的 Capsule。

信任边界：

1. 对话 harness 可以提炼枚举信号，但其自由文本输入不可信；
2. course runtime 可写当前 workspace，并在 opt-in 后写受限用户状态；
3. Core 安装目录对 course runtime 只读；
4. Release Manager 是唯一可切换 Core 的组件；
5. 下载的 artifact 在来源、签名/哈希和结构验证前均不可信；
6. Capsule 在用户预览并显式导出前不得离开本机。

## 2. 攻击者与入口

- 恶意或被污染的课程资料尝试进行 prompt injection；
- 模型或 harness 把原始消息、路径或个人信息塞入未知字段；
- 本地并发进程用陈旧 revision 覆盖新状态；
- 损坏、恶意或回滚攻击的 release artifact；
- 压缩包中的路径穿越、绝对路径、重复名称或 reparse/symlink 逃逸；
- 旧 Core 读取或写入不兼容新 schema；
- Capsule 中的唯一组合、精确时间或稳定 ID 造成重识别；
- 更新进程在下载、迁移、安装或 pointer 切换中途被终止。

## 3. 威胁清单

| ID | 威胁 | 影响 | 控制 | 必测断言 |
| --- | --- | --- | --- | --- |
| T01 | 原始消息进入 Profile | 隐私泄露 | `additionalProperties: false`、枚举值、无自由文本字段 | `message`/`summary`/`quote` 字段被拒绝 |
| T02 | 推断敏感特征 | 隐私与歧视 | `infer_sensitive_traits: false` 常量、维度 allowlist | 未知/敏感维度被拒绝 |
| T03 | 历史偏好覆盖当前请求 | 行为失配 | current-turn 优先、ignored provenance | 冲突时 current-turn 生效 |
| T04 | 个性化弱化 mastery/RAG | 学习正确性下降 | protected invariants 独立于 preference | override 尝试返回 `protected_invariant` |
| T05 | 未 opt-in 跨课程写入 | 授权越界 | global flag + workspace binding 双重守卫 | 未启用时用户目录不创建 |
| T06 | 重复 Session 双计数 | 错误画像 | opaque session id 幂等键 | 重复 ID 被拒绝 |
| T07 | stale revision 覆盖 | 数据丢失 | expected revision + namespace lock | 第二写者失败且状态未变 |
| T08 | 迁移部分成功 | 状态不一致 | 副本迁移、全量验证、原子切换 | 任一步失败仍读取旧状态 |
| T09 | 模型自由迁移历史 | 不可复现 | 纯函数 registry | 无注册路径时拒绝而非猜测 |
| T10 | 降级旧 Core 写新 schema | 语义损坏 | `min_reader_core_version` 和 write compatibility | 旧 Core 只读拒绝 |
| T11 | 实验跨不可比 Atom | 伪因果 | 分层、不可变 exposure、insufficient | 不可比样本不晋升 |
| T12 | 仅速度提升触发晋升 | 掌握质量下降 | quality primary + guardrail | 无质量改善时保持 monitoring |
| T13 | Exposure 重试换组 | 污染实验 | 确定性 assignment + ledger | 同一 episode 始终同组 |
| T14 | Artifact hash/signature 错误 | Core 被篡改 | 下载后验证、稳定 channel 强制信任策略 | 验证失败前不执行 artifact 代码 |
| T15 | Zip Slip/链接逃逸 | 任意文件覆盖 | 逐 entry 规范化与链接拒绝 | `../`、绝对路径、链接均失败 |
| T16 | 半更新 | Core 不可用 | side-by-side、transaction journal、health check | 重启后恢复旧 active |
| T17 | `main` 成为稳定更新源 | 不可重复版本 | immutable semver release | stable 拒绝分支 artifact |
| T18 | Capsule 含路径/locator | 内容泄露 | 字段 allowlist + recursive privacy lint | 路径、URL、DOI、Atom ID 被拒绝 |
| T19 | Capsule 唯一组合重识别 | 身份泄露 | 分桶、粗时间窗、一次性 ID | 小样本组合失败或再分桶 |
| T20 | Capsule 自动上传 | 未授权外发 | build/export/submit 分离，首版无 submit | export 测试中无网络调用 |
| T21 | Course runtime 写 Core | 供应链越权 | 目录权限 + tree fingerprint 测试 | 完整 session 后 Core tree 不变 |
| T22 | 删除 reset/rollback 历史 | 审计丢失 | tombstone、归档、可恢复操作 | reset 不永久删除 ledger |

## 4. 验证策略

### 4.1 Schema 与输入

- 对所有规范 payload 使用 Draft 2020-12 Schema；
- 未知字段默认拒绝；
- 枚举字段不提供“other free text”；
- ID 使用局部 opaque ID，不使用路径、标题或消息摘要；
- 对 NDJSON 每一行独立校验并报告行号。

### 4.2 状态事务

- 每个 namespace 独立 revision；
- mutation 在锁内重读 revision；
- 临时副本与目标位于同一 volume；
- 写入完成并 fsync 后再切换；
- transaction journal 能区分 planned、copied、validated、activated、committed、failed；
- 恢复只切换到已验证的完整状态。

### 4.3 Release 供应链

- manifest 本身必须来自配置的 trust root；
- stable artifact 同时校验语义版本、哈希、签名策略和 Skill 结构；
- 解压前检查全部 entry；
- 验证完成前不得 import 或执行 artifact 中的 Python；
- 新 Core 只在迁移副本上运行 smoke test；
- active pointer 最后切换，且旧版本保留。

### 4.4 隐私攻击夹具

至少覆盖：Windows/UNC/POSIX 路径、URL、DOI、邮箱、用户名、精确时间、课程标题、Atom ID、长自由文本、零宽字符、Unicode 混淆和嵌套未知字段。

## 5. 发布前安全闸门

- T01–T22 均有自动化测试或明确的人工 release check；
- 所有 P0 威胁测试在 Windows 与 Linux 通过；
- migration/update fault injection 覆盖每个持久化阶段；
- Core tree fingerprint 在全量学习测试前后相同；
- Capsule 构建和导出测试使用禁止网络的测试环境；
- stable release 不含未签名或不可验证 artifact；
- 任一安全闸门失败时 feature flag 保持关闭。
