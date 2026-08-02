# Modeling Harness 4.0：薄 Harness，自由研究

> 本文保留 4.0 基线设计；“4.1 增量”小节记录向后兼容的新增约束与机会发现层。

## 一句话原则

让模型决定“下一步值得做什么以及怎么做”，让 Harness 只决定“动作是否已有权限、
结果是否可信、状态是否可以提交”。

## 设计边界

4.0 不引入固定角色流水线，也不要求普通局部研究先走审批。Agent 可以自由拆题、选模、
调用或跳过工具、并行、回退、分支、放弃路线和声明不可辨识。

Harness 只强制：

1. NOT_RUN 不得显示为 PASS；
2. verified 与当前工件、输入、审核和策略哈希绑定；
3. 错误证据沿依赖图级联失效；
4. 未知执行结果进入 RECOVERY_PENDING；
5. 已登记的 producer 不能成为自己的 reviewer。

## 4.1 增量：可信之外还要完整

4.1 不改变“Agent 自由研究、Harness 只守边界”的分工，而是在既有 soundness 轴旁增加
completeness 轴，并把 answer quality 作为预算内优化目标：

1. Requirement Gate：源文本逐段映射到 mandatory requirement，再映射到 claim 与
   verified evidence；未闭合时 S6 不通过；
2. Claim Gate：正式数字绑定 JSON 字段、单位、容差和安全推导式，数值漂移阻断发布；
3. Holdout Gate：screen / selection / report / stress 四集分离，随机选择后的头条数字
   只能来自独立 report set，否则必须承认选择偏差并降级；
4. Delivery Gate：工作稿与成稿分离，内部证据标记、路径、哈希和模板占位不得泄漏；
5. Opportunity Layer：搜索边界、欠分辨网格、optimality gap、排名翻转、不可行压力情景
   与未物化假设分支只生成软机会，由 expanded_search、dominance_proof、
   budget_qualified_stop、strength_downgrade 或 deferred 显式处置。

Requirement Ledger、Claim Binding、Scenario Sets 与 Opportunity Ledger 都由原始工件和
权威图派生；它们不会取代 Problem Graph、Evidence Graph 或 Workflow。

## 状态分层

~~~text
Problem Graph        研究义务与候选路线
Action Proposal      重大变更或新权限的审计记录（非必经流水线）
Workflow             任务、租约、正交结果状态与恢复
Tool Runs            可复现计算、输入输出哈希、日志与 validator
Evidence Graph       typed claim、验证绑定、鲜度与级联失效
Milestone Projection S0–S6 全局闭合投影
State Capsule        从以上状态按需派生的长任务上下文
~~~

State Capsule 中的 Task、Progress、Failure、Resource、Opportunity 五本账只是视图，不是
新的权威数据库。

## 正交结果模型

~~~text
execution_status = not_run | queued | running | completed | error |
                   recovery_pending | cancelled
verdict          = unassessed | pass | fail | inconclusive | not_applicable
authority        = machine | human | hybrid
freshness        = valid | stale | missing | tampered
~~~

HUMAN 是裁决来源，不是 PASS/FAIL 的同级状态。人工可以接受风险或要求继续调查，但不能
把一个机械 FAIL 偷换成机械 PASS。

## 撤销传播

~~~text
root evidence: verified → revoked
dependent evidence: verified/candidate → invalidated
dependent task: completed → invalidated
review binding: current → stale
milestone: valid → archived
paper claim: current → stale（由 narrative audit 拦截）
~~~

历史记录不删除。重算从最小受影响节点开始。

## Append-only Review Lineage

Problem Graph 的 `review.path` 是逻辑审核槽位，不是跨 Agent 共享覆盖的物理文件。首次审核
可写逻辑路径；之后写同目录 `_v2.json`、`_v3.json` 等不可变版本。解析器按版本倒序，
选择同时匹配当前 `contract_hash` 和当前全部工件哈希的审核。

这避免两个问题：

- Windows 跨 Agent ACL 不再要求后一位 reviewer 替换前一位创建的文件；
- REJECT、APPROVE 和修订历史都保留，可被 Episode 与哈希绑定完整回放。

旧活动任务若仍 owns canonical 路径，但 reviewer 已生成带相同 task_id 的合法 `_vN`
文件，Scheduler 会自动重绑定任务输出；该操作不需要用户授权。

## Proposal Policy

裁决只有 ALLOW、ALLOW_WITH_OBLIGATIONS、REVISE、ESCALATE_TO_USER、DENY。

- 本地、项目内、可逆动作默认 ALLOW；
- 联网、安装、商业工具和外部写入只在缺少现有授权时升级；
- 未授权破坏性动作 DENY；
- Proposal 是 Agent 可调用的审计接口，不拦截每个 Evidence 写入。

## 调度

默认评分保持 3.1 行为：

~~~text
downstream_impact × uncertainty / estimated_cost
~~~

节点可选增加 decision_change_probability、information_gain、
falsification_value、improvement_value、coverage_value、risk_penalty、
latency_penalty 和 repeat_penalty。两个 4.1 价值字段以 1.0 为中性缺省值，
risk_penalty 仍在分母；所有字段缺省时分数与 4.0.1 逐位一致。
