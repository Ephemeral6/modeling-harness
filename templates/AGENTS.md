# Modeling Harness 4.0 执行宪法

本项目以 Problem Graph 的局部问题为求解单元，以 S0–S6 为全局里程碑投影。
Harness 负责状态、权限、证据和恢复；Agent 负责研究判断。

## Freedom Envelope

Agent 可自主决定：

- 如何拆题、增删或切换候选研究路线；
- 采用何种数学模型、算法、计算工具和验证后端；
- 串行、并行、回溯、分支、合并或放弃路线；
- 是否调用工具，何时停止低价值实验；
- 在不可辨识、无唯一解或优势不显著时改报集合、区间或条件式结论。

普通、本地、项目内、可逆的研究动作不需要额外审批。proposal 用于重大图变更的
审计，或联网、安装、商业许可、外部写入等需要新权限的动作；它不是固定角色流水线。

## 权威状态

- .harness/problem_graph.json：问题义务与依赖；
- .harness/evidence.json：候选、验证、拒绝、撤销和依赖失效的结论；
- .harness/workflow.sqlite3：任务、租约、验收、恢复和事件；
- .harness/tool_*：计算选择、运行、输入输出哈希和验证；
- .harness/stamps/：S0–S6 里程碑投影；
- .harness/proposals/：需要审计的动作提案，不是第二套状态。

modelharness state 按需生成 State Capsule 和 Task / Progress / Failure /
Resource / Opportunity 五本派生账；不得把它们另存为新的权威数据库。

## 五条硬不变量

1. NOT_RUN 永远不等于 PASS。执行状态、裁决、裁决来源和鲜度正交记录：
   execution_status × verdict × authority × freshness。
2. verified 必须绑定当前工件、输入证据、审核和验证策略的哈希；变化后只能是
   STALE、MISSING 或 TAMPERED，不能继续显示为有效通过。
3. 错误证据撤销时，根结论变为 revoked，依赖结论变为 invalidated，并级联使
   相关任务、审核、里程碑和论文 claim 过期；历史记录不得删除。
4. 执行结果不明时进入 RECOVERY_PENDING。非幂等动作禁止自动重复，必须先对账：
   recovered_success | confirmed_failed | safe_to_retry | human_required。
5. 生成者不能批准自己的结论。生产任务与审核任务必须不同；登记身份后，producer
   与 reviewer 的 worker 也必须不同。

除上述不变量、项目路径边界和用户明确权限外，不新增限制。

## 建模工作规则

1. problem/data_raw/ 只读；变换写入 data/processed/。
2. 原始题意只写 problem/statement.md。
3. 先做量纲、Fermi、反例、toy 和已知解，再决定是否跑正式规模。
4. 数值结论来自可复现机器产物，不得凭记忆写入论文。
5. 每个计算节点显式登记 use 或 skip；Agent 自主选择本地工具。
6. use 通过 tool run 记录版本、种子、输入输出哈希、日志和 validator。
7. 上游错误使用 evidence revise/revoke，只返工最小受影响子图。
8. recommendation 不能因为计算成功就自动冒充已验证事实。

## 工作循环

~~~powershell
modelharness doctor --project .
modelharness state --project .
modelharness work next --project .
~~~

领取局部任务后，Agent 可按需执行：

~~~powershell
modelharness tool recommend NODE_ID --project .
modelharness tool decide NODE_ID --action auto --reason "<理由>" --project .
~~~

若工具或任务结果不明：

~~~powershell
modelharness tool recover RUN_ID --outcome safe_to_retry --note "<对账证据>" --project .
modelharness task recover TASK_ID --outcome safe_to_retry --note "<对账证据>" --project .
~~~

完成任务、运行、审核、证据验证或 Gate 后再次运行 work next。

## Delivery Profile

- cumcm：逐问数值结果、算法过程、误差和复现；
- mcm_icm：摘要、机制解释、敏感性和决策叙事；
- real_world：利益函数、数据治理、决策包和监控计划；
- general：通用证据报告。

Profile 只改变交付义务和计算环境基线，不能改写已验证数学事实。
