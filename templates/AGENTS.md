# Modeling Harness 3.1 执行宪法

本项目以 Problem Graph 中的局部问题为求解单元，以 S0–S6 为整体审计里程碑，并使用
Toolchain Layer 管理 Agent 的自主计算选择。

## 五层状态

- `.harness/problem_graph.json`：待回答义务和依赖；
- `.harness/tool_plans/`、`tool_decisions/`、`tool_runs/`：计算计划、选择和运行；
- `.harness/evidence.json`：候选、验证、拒绝和撤销的结论；
- `.harness/workflow.sqlite3`：任务、租约、预算和验收；
- `.harness/stamps/`：里程碑闭合投影。

## 不可违反的规则

1. `problem/data_raw/` 只读；变换写入 `data/processed/`。
2. 原始题意只写 `problem/statement.md`。
3. 主控负责问题拆解、关键前沿、预算、接口和最终整合。
4. 生成者、实现者和审核者分离；生成者只登记 candidate。
5. 先做量纲、Fermi、反例、toy 和已知解，再跑正式规模。
6. 数值结论来自机器产物，不得凭记忆写入论文。
7. 每个计算节点必须显式登记 use 或 skip 及理由。
8. use 必须通过 `tool run` 记录工具版本、种子、输入输出哈希、日志和验证。
9. Agent 可自主选择本地工具；联网、安装、商业许可和外部写入需新授权。
10. 任务完成必须通过 acceptance；自然语言汇报不是完成依据。
11. 审核绑定 task、contract、evidence、artifact 和 tool run。
12. 上游错误使用 `evidence revise/revoke`，沿图撤销下游。
13. 最优不稳或优势不显著时，改报集合、区间或条件式建议。

## 工作循环

```powershell
modelharness doctor --project .
modelharness tool doctor --project .
modelharness work next --project .
```

按返回的 frontier、contract、tool plan 和 tasks 推进。Agent 在领取计算任务后先执行：

```powershell
modelharness tool recommend NODE_ID --project .
modelharness tool decide NODE_ID --action auto --reason "<理由>" --project .
```

若选择 use，使用结构化 argv 和 validators 执行 `tool run`。完成任何任务、工具运行、
审核、验证或 Gate 后再次运行 `work next`。

## Delivery Profile

- `cumcm`：逐问数值结果、算法过程、误差和复现；
- `mcm_icm`：摘要、机制解释、敏感性和决策叙事；
- `real_world`：利益函数、数据治理、决策包和监控计划；
- `general`：通用证据报告。

Profile 决定交付和计算环境基线，不能改变已验证数学事实。
