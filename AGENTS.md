# Modeling Harness 4.3 对话入口

本契约对 Codex 与 Claude Code 两个底座同等生效（Claude Code 经 `CLAUDE.md`
进入本文件）。当用户在本仓库上传数学建模题目、数据或参考材料并要求开始时，直接 Intake。不要要求
用户手动创建目录、复制附件、运行命令或重复提示词。

## 冷启动 Bootstrap

本仓库应做到“clone 下来直接丢题目就能跑”。会话开始时先探测环境：

1. 运行 `modelharness --version`；失败则在仓库根目录执行
   `python -m pip install -e .`（核心零依赖，秒级完成）后重试。
2. Profile 对应的科学计算栈是可选 extras（如 `pip install -e ".[cumcm]"`）；
   若当前环境已有等价包则直接用，缺包且需要联网安装时按权限规则请求授权，
   并优先选择不依赖缺失包的方法路线。
3. Bootstrap 结果（安装了什么、跳过了什么）写入项目 `docs/decisions.log`。

## 自动接题

1. 收集本轮附件绝对路径和用户原始要求。若用户把题面直接粘贴在对话里而没有
   附件，把原文一字不改作为 `--prompt` 传入即可：intake 会把它物化为
   `problem/data_raw/001__pasted_statement.md`（哈希锁定、进 manifest，
   `origin=conversation_paste`），并同步生成 `problem/statement.md`。
   粘贴文本与上传附件享受同等的磁盘保留待遇。
2. 执行 modelharness intake 创建隔离项目。
3. 读取项目 AGENTS.md、intake manifest、题面和全部附件。
4. 根据任务选择 cumcm、mcm_icm、real_world 或 general Profile。
5. 运行 doctor、tool doctor、state 和 work next。
6. 立即处理最高优先级局部问题；每次状态变化后再次运行 work next，直到完成。

## 自由边界

整题目标由 Harness 保持，局部研究路线由 Agent 决定。Agent 可自主拆题、选模、调用或
跳过本地计算工具、并行、回退、分支、合并、放弃路线以及声明不可辨识。S0–S6 是全局
里程碑投影，不是固定角色队列。

普通、本地、项目内、可逆动作不走审批。Action Proposal 用于重大 Problem Graph 变更
的审计，或联网、安装、商业许可、外部写入等新权限；不得把 Proposal 扩张为每次
Evidence 写入的强制流水线。

## 权威层

- Problem Graph：问题义务、候选路线、依赖和合同；
- Toolchain：工具能力、Agent 决策、可复现运行和恢复；
- Evidence Graph：候选、验证、拒绝、撤销、依赖失效和鲜度；
- Workflow：任务、租约、预算、正交裁决、事件和恢复；
- Milestone Stamps：S0–S6 整体闭合投影。

State Capsule 与 Task / Progress / Failure / Resource / Opportunity 五本账从以上状态
按需派生，不是新的权威数据库。不得用任务完成替代证据验证，不得用工具成功退出替代
数学验证，也不得用印章存在替代印章校验。

## 九条硬不变量

1. NOT_RUN 永远不等于 PASS；execution_status、verdict、authority、freshness 正交。
2. verified 绑定当前工件、输入、审核和验证策略哈希；变化后标记 stale、missing 或
   tampered。
3. 根错误证据变为 revoked，依赖结论变为 invalidated，并级联到任务、审核、里程碑和
   论文 claim；历史记录不删除。
4. 未知执行结果进入 RECOVERY_PENDING；非幂等动作不得在对账前自动重试。
5. 生成者不能批准自己的结论；登记 producer 后，审核 task 和 worker 均必须独立。
6. mandatory requirement 未闭合时，S6 不能宣告完整交付。
7. 正式数字未绑定权威字段，或推导式与源字段不一致时，不能发布。
8. 存在随机选择过程时，头条性能数字若不来自独立 report set，不能标为无偏终评。
9. 成稿中存在内部证据标记、临时路径或未解析模板时，不能通过交付。

复审是 append-only 谱系：Problem Graph 中的 review.path 是逻辑审核名，实际输出路径以
任务 owns 为准。若旧审核已存在，新审核写 `_v2.json`、`_v3.json` 等不可变同名版本；
不得覆盖、删除或归档旧 REJECT，也不得为此向用户请求授权。Harness 自动选择与当前
合同和全部当前工件哈希匹配的最新审核。

除这九条、项目路径安全和用户权限外，不增加限制。G6–G9 只约束交付闭合；
搜索边界、假设分支和潜在改进进入 Opportunity Ledger，不直接触发 FAIL。

## 竞赛论文内容不变量

当 Delivery Profile 定义 `paper_content_contract` 时，S6 还必须通过 requirement → paper
的反向内容审计。每个 mandatory answer 均需直接答案、模型定义、公式推导、算法/伪代码、
验证过程和管理解释；题目特有义务按 `paper_obligations` 检查表示形式、verified evidence、
附件行数/字段/哈希与正文引用。18–25 页只作软目标，机器字段锁定表只进入技术附件。

## Agent 工具自主权

每个计算节点都会生成 tool plan。执行 Agent 有权采纳推荐、增加 capability、改选其他
已安装且策略允许的工具，或判断解析推导/现有证据足够而 skip。

use 和 skip 都通过 tool decide 记录理由。use 通过 tool run 绑定当前 node contract、
decision hash、版本、种子、输入、输出、日志和 validator。超时或外部结果不明时使用
tool recover；不得把 RECOVERY_PENDING 当作普通失败直接重复。

默认自主权覆盖项目内本地计算。联网、安装或升级软件、商业许可证、项目外写入和超预算
长计算需要用户新授权。不得通过普通 shell 绕开工具策略。

## Problem Graph

新项目自带初始图。S0 应按真实子问和局部可证伪问题细化。重大修订：

1. 生成 problem/decomposition.json；
2. 运行 plan validate；
3. 冷启动审核边界、依赖、输出合同、方法包和失败出口；
4. 使用 plan apply --reason 应用，并保留 Proposal/事件记录。

## 执行原则

1. 整题由主控整合，局部问题按关键前沿调度。
2. 生成者登记 candidate 时传 producer_task_id，不运行自己的终审。
3. 先做量纲、Fermi、反例、toy、已知解和简单基线。
4. 计算从 smoke、小规模、验证规模推进到正式规模。
5. 优化检查可行性、残差、界/gap；随机计算锁种子并报告误差。
6. 任务声明输入、写入范围、工具计划、产物、验收、预算和失败出口；仅有外部或
   非幂等副作用时声明 idempotent=false。
7. 审核绑定 task、contract、evidence、artifact 和相关 tool run；严格写入审核任务
   owns 给出的版本化路径，不自行维护 canonical 文件。
8. 发现错误用 evidence revise/revoke，只返工最小受影响子图；修复走 repair
   begin/verify 合同：动手前声明 finding 锚点与写入范围，收尾由机器核对域外
   改动、新增护栏违规与回归语料条目。
9. 最优不稳或优势不显著时输出集合、区间或条件式建议。
10. 只有全部交付闭合、需要新授权，或同一阻断连续三轮时停止。

## 会话可弃性（全落盘纪律）

会话上下文是一次性的：用户随时可能关闭引擎，重开后必须能只凭磁盘完全重建现场。
因此：

1. 任何影响后续决策的中间产物——判断、数字、失败原因、被放弃的路线——在产生的
   同一个工作单元内落盘：数值进 `results/*.json` 或 `data/processed/`，判断与死因
   追加进 `docs/notebook.md` 与 `docs/decisions.log`，结论登记进 Evidence Graph。
2. 禁止让任何数据只存在于对话上下文里再“稍后一起写盘”；先落盘，再继续。
3. 进入论文或答案的每个数字必须能指出其磁盘出处文件；说不出出处的数字不得使用。
4. 用户说“继续”或“进展”时恢复 projects/.current.json 指向的项目：先运行
   doctor、tool doctor、state 和 work next，从磁盘状态而非记忆恢复工作，
   不重做已有验证证据的工作。
