# Modeling Harness 3.0 执行宪法

本项目以 Problem Graph 中的局部问题为求解单元，以 S0–S6 为整体审计里程碑。
任何智能体都可以提出候选，但只有合同绑定的机械检查、冷启动审核和
`modelharness evidence verify` 能把节点提升为 verified。

## 四层状态

- `.harness/problem_graph.json`：待回答义务和依赖；
- `.harness/evidence.json`：候选、验证、拒绝和撤销的结论；
- `.harness/workflow.sqlite3`：任务、租约、预算和验收；
- `.harness/stamps/`：里程碑闭合投影。

不得用任务完成代替证据验证，也不得用印章存在代替印章校验。

## 不可违反的规则

1. `problem/data_raw/` 只读；所有变换写入 `data/processed/`。
2. 原始题意只写 `problem/statement.md`，不得混入解题思路。
3. 主控负责问题拆解、关键前沿、预算、接口和最终整合。
4. 生成者、实现者和审核者分离；生成者只登记 candidate。
5. 先做量纲、Fermi、反例、toy 和已知解，再跑正式规模。
6. 数值结论必须来自机器产物，关键结果不得凭记忆写入论文。
7. 任务完成必须通过 acceptance；自然语言汇报不是完成依据。
8. 审核绑定 task ID、contract hash、evidence IDs 和 artifact hashes。
9. 上游错误使用 `evidence revise/revoke`，沿图撤销下游。
10. 最优不稳、优势不显著或稳健集为空时，改报集合、区间或条件式建议。

## 工作循环

运行：

```powershell
modelharness work next --project .
```

按返回的 `frontier`、`priority`、`contract_hash` 和 `tasks` 推进。完成任何任务、
审核、验证或 Gate 后再次运行，不能把单个阶段或 Agent 完成当作终点。

## Problem Graph 修订

问题图只在题意、数据语义、模型接口或交付义务真正变化时修订。先产出外部 proposal，
运行 `plan validate`，经冷审后用 `plan apply --reason` 应用。系统会淘汰旧合同任务、
撤销受影响证据，并只失效必要里程碑。

## Delivery Profile

- `cumcm`：逐问数值结果、算法过程、误差和复现；
- `mcm_icm`：摘要、机制解释、敏感性和决策叙事；
- `real_world`：利益函数、数据治理、决策包和监控计划；
- `general`：通用证据报告。

Profile 只能改变交付义务和呈现，不能改变已验证数学事实。
