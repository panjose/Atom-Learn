# AtomLearn 开源发布检查清单

这份清单把“仓库代码可以强制验证的控制项”和“必须由 GitHub 仓库管理员配置的外部状态”分开。请在修改仓库可见性前完成所有未勾选项；任何可见性、所有权、发行或安全设置变更后，都应重新执行本清单。

## 使用这份清单

- `[x]` 表示该控制项已经进入当前源码树，并受仓库验证保护。
- `[ ]` 表示维护者必须核验或配置 GitHub 状态；源码 commit 无法证明外部状态。
- 任何隐私、凭据、来源、许可或发行签名问题未解决时，都不能把仓库设为公开。

## 仓库基线

- [x] Core、Manager、包元数据、`LICENSE`、`NOTICE` 和 `THIRD_PARTY_NOTICES.md` 对 Apache-2.0 的声明一致。
- [x] 可选 OCR adapter 使用 pypdfium2/PDFium 而不是 PyMuPDF；构建后会检查 wheel 内容和许可元数据。
- [x] `CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`SECURITY.md`、`SUPPORT.md`、`GOVERNANCE.md` 和 `CITATION.cff` 已定义社区契约。
- [x] Issue forms、Pull Request 模板、CODEOWNERS、Dependabot、CodeQL 和 Open Source Readiness workflow 已纳入版本控制。
- [x] 中英文 README 都将 `main` 标注为尚未发布的 `v0.15`，说明签名 `v0.14.2` 只交付 base profile，并明确 AtomLearn 尚未建立学习增益效果结论。
- [x] `python release/open_source_gate.py` 会检查必要文件、受跟踪的隐私路径、凭据模式、用户专属绝对路径、Git 历史和可选构建 wheel，同时不会输出匹配到的 secret 原文。
- [ ] 决定是否在公开发布前交付签名 `v0.15.0`。在此之前，README 和能力账本中的相关声明必须继续明确标记为尚未发布的实现工作。

## 隐私与历史审计

- [ ] 检查所有将公开或仍有关联的 branch、tag、release、issue、Pull Request、discussion、wiki、Actions 日志、artifact 和 cache。
- [ ] 在已抓取全部 refs 的完整 clone 中运行 `python release/open_source_gate.py --json`，再独立复核结果和 GitHub secret-scanning alerts。模式匹配只是后备防线，不能证明绝对不存在 secret。
- [ ] 确认历史上提交过的所有凭据均已吊销并轮换，即使后来已从文件中移除；绝不能只依赖删除。
- [ ] 确认任何公开 ref 或托管 artifact 中都不存在学习者状态、受版权保护的教材、论文语料、考试答案、未发表结果、模型凭据、cookie、签名私钥或发布密钥备份。
- [ ] 决定是否接受公开 commit author 邮箱 `242panjose@gmail.com`。当前 Git 历史包含该邮箱。不要轻率重写已签名或已打 tag 的历史：历史重写会改变 commit identity，必须另做迁移与发行完整性方案。
- [ ] 检查仓库 collaborator、deploy key、webhook、GitHub App、Actions secret/variable、environment、Pages、Codespaces 和 package 权限。修改可见性不等于获准公开任何 secret。

## GitHub 安全与治理

- [ ] 启用 GitHub 私密漏洞报告，并让维护者订阅 security alert 通知，然后再让用户遵循 `SECURITY.md`。
- [ ] 核验 dependency graph、Dependabot alerts 和 Dependabot security updates；公开前先分流处理初始 alerts。
- [ ] 核验公开仓库 secret scanning，复核每一条 alert，并在账号或套餐允许时启用仓库 push protection。
- [ ] 仓库公开后，确认已提交的 CodeQL workflow 成功运行。对于没有 GitHub Code Security 权限的私有仓库，它会有意跳过。
- [ ] 为 `main` 建立 ruleset：要求 Pull Request 以及 `Validate AtomLearn`、`Open Source Readiness`、`CodeQL` 成功；阻止 branch 删除和 force push；只设置范围很窄的紧急 bypass 角色。
- [ ] 保护 `v*` tag，禁止更新和删除；所有已发布 tag、manifest、runtime bundle、trust bundle 和签名都必须视为不可变。
- [ ] 将 Actions 默认权限限制为只读；要求首次贡献者运行审批；复核允许使用的第三方 Actions。
- [ ] 以最小权限配置 release environment、`ATOMLEARN_RELEASE_PRIVATE_KEY` 和 `ATOMLEARN_RELEASE_KEY_ID`，并按需设置 required reviewer。私钥绝不能进入仓库文件或日志。

## 发布与社区边界

- [ ] 核验仓库 description、website、topics、social preview、default branch、Releases、是否启用 Discussions，以及 issue/discussion 的管理负责人。
- [ ] 使用 clean-room clone，按计划公开的说明安装 Core 和 Manager，并在 Windows 与 Linux 上执行快速验证。
- [ ] 构建 Core 和 Manager wheel，然后运行 `python release/open_source_gate.py --skip-history --wheel-dir <directory>`。
- [ ] 确认所有 fixture 和媒体均为合成内容、为本仓库原创、属于公有领域，或采用已记录的兼容许可。本次发布不加入漫画或宣传图片。
- [ ] 发布说明必须区分仓库实现、签名稳定交付、harness/model 行为证据与人体学习效果证据；绝不能从测试、benchmark 或本地 telemetry 推导最后一项。
- [ ] 为安全报告、bug、支持请求与贡献审查定义初始维护者响应预期；保持真实可持续，不承诺无法履行的 SLA。

## 最终可见性变更

- [ ] 在最终审计窗口冻结 merge 和 release。
- [ ] 记录接受审计的准确 commit，以及仓库、历史、wheel、CI、CodeQL 与人工隐私复核结果。
- [ ] 只有在确认前述所有项目并理解 GitHub 可见性变更影响后，才由仓库 owner 执行可见性变更。
- [ ] 变更后立即在未登录状态核验匿名 clone、README/license 渲染、issue forms、私密漏洞报告、Actions、branch/tag ruleset、Dependabot、CodeQL、Releases 和 package links。
- [ ] 只有事后核验通过才能对外宣布。如果发现 secret 或隐私 artifact，应先控制访问并吊销/轮换，再按安全事件流程处理，不能继续发布。
