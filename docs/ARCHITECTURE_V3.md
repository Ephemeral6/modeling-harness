# Modeling Harness 3.0：双层问题图架构

## 目标

Harness 3.0 将“整题”作为编排和交付边界，将“可独立证伪的局部问题”作为实际求解
单元。S0–S6 继续提供里程碑审计，但不再决定固定角色顺序。

## 四个权威状态源

```text
Problem Graph        还必须回答什么，以及局部问题如何依赖
Evidence Graph       已经建立、验证、拒绝或撤销了什么
Workflow SQLite      谁在执行什么、租约、预算、验收和恢复状态
Milestone Stamps     哪些整体研究里程碑已经闭合
```

四层不得重复保存同一状态。Problem Graph 不持久化 `ready/running/completed`；
调度器根据证据和任务动态推导。

## 目标数据流

```text
Intake
  → Problem Graph
  → Critical Frontier Scheduler
  → Local Research Cell
  → Candidate Evidence
  → Mechanical Checks + Cold Review
  → Verified Evidence
  → Global Integration
  → S0–S6 Milestone Gate
  → Delivery Profile Renderer
```

## Problem Graph

`.harness/problem_graph.json` 是待解决义务 DAG。每个节点声明：

- 精确问题和任务类型；
- 问题节点依赖和输入证据；
- 预期输出证据、类型、陈述和机器产物；
- 方法包；
- 互不冲突的 workstream 写入范围；
- 机械验收和独立审核；
- 下游影响、不确定性和预计成本；
- 所属里程碑。

节点的语义字段产生 `contract_hash`。任务、证据和审核都绑定该哈希。风险权重变化只
影响调度，不会使数学证据失效；问题、输入、输出、检查或审核政策变化会产生新合同。

## Critical Frontier Scheduler

调度器只选择依赖闭合且尚未验证的节点。默认优先级：

```text
priority = downstream_impact × uncertainty / estimated_cost
```

硬优先规则：

1. 能证伪整条路线的低成本检查；
2. 能解除多个下游阻塞的节点；
3. toy、极限、反例和简单基线；
4. 正式规模计算；
5. 叙事和交付。

## Local Research Cell

局部单元遵循：

```text
Frame → Compete → Discriminate → Implement → Verify → Integrate
```

生成者只能登记 candidate。独立审核必须绑定 work item、合同哈希、证据范围和当前产物
哈希。`evidence verify` 同时检查依赖、产物、机械检查和审核，之后才能写入 verified。

## 方法包

`config/method_packs/` 保存版本化研究协议。方法包提供：

- 适用任务类型；
- 局部研究步骤；
- 必须执行的最小测试；
- 失败出口。

方法包锁保存在 `config/method_pack_lock.json`。方法包改变会使引用它的后续里程碑
印章失效。

## Delivery Profile

`config/delivery_profile.json` 决定根交付义务和呈现方式，不改变已验证数学事实。

- `cumcm`：逐问结果、算法过程、数值误差和复现入口；
- `mcm_icm`：摘要、问题重构、机制解释、敏感性和政策含义；
- `real_world`：利益函数、数据治理、决策包、监控和重做触发器；
- `general`：通用证据报告。

切换 Profile 会修改 S6 的 profile-managed 问题义务，并使旧交付合同陈旧。

## 撤销与返工

- 证据错误：沿 Evidence DAG 撤销下游节点；
- 问题合同改变：淘汰旧合同的活动任务，撤销受影响输出；
- 审核拒绝：对同一证据 ID 执行 revise，保留谱系；
- 印章失效：从第一个引用受影响证据的里程碑开始归档；
- 与问题无关的已验证证据不重算。

## V2 兼容

没有 `.harness/problem_graph.json` 的项目继续使用 V2 静态 Playbook 调度。可以运行：

```powershell
modelharness plan init --project <旧项目>
```

初始化问题图后进入 V3 调度。V2 evidence schema、字符串检查和 schema 2 印章仍可读取；
新项目只生成 schema 3 合同。

