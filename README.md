# Modeling Harness 3.0

面向国赛、美赛和真实数学建模任务的、可恢复、证据门禁驱动的多智能体执行框架。
3.0 保留 2.0 的可靠性内核，并把实际求解单元从固定阶段角色升级为 Problem Graph
中的可独立证伪局部问题。

## 架构

```text
Codex Conversation / CLI
          ↓
Problem Graph ── local obligations / dependencies / contract hashes
          ↓
Critical Frontier Scheduler ── impact × uncertainty / cost
          ↓
Workflow Engine ── SQLite lease / heartbeat / acceptance / retry
          ↓
Evidence Kernel ── typed DAG / checks / cold review / cascade revoke
          ↓
S0–S6 Milestone Service ── chained, self-validating stamps
          ↓
Delivery Profiles ── CUMCM / MCM-ICM / Real World / General
```

四个权威状态源：

- `.harness/problem_graph.json`：还必须解决什么；
- `.harness/evidence.json`：已经验证了什么；
- `.harness/workflow.sqlite3`：谁在做什么；
- `.harness/stamps/sN.json`：哪些里程碑已经闭合。

详细设计见 [ARCHITECTURE_V3.md](docs/ARCHITECTURE_V3.md)。

## 核心原则

- 整题是编排边界，局部问题是求解单元；
- Problem Graph 状态由 Evidence 和 Workflow 推导，不复制真相；
- 生成者只能登记 candidate，不能自我认证；
- 任务完成必须通过机器验收，不相信自然语言汇报；
- 审核绑定合同哈希、证据范围和产物哈希；
- 先做反例、toy、已知解和基线，再做正式规模计算；
- 错误证据沿依赖图撤销，只失效最早受影响的里程碑；
- 最优不稳时必须改报集合、区间或条件式建议；
- 交付 Profile 改变呈现义务，不改变数学事实。

## 快速开始

```powershell
python -m pip install -e .

modelharness intake --title "真实题目" --prompt "完整解决" `
  --file 题面.pdf --file 数据.xlsx

modelharness status
modelharness plan show
modelharness work next
```

主控在每个局部工作单元、审核或 Gate 后继续运行：

```powershell
modelharness autopilot next
```

直到问题图根义务、S0–S6 印章链和交付 Profile 审计全部闭合。

## Problem Graph

```powershell
modelharness plan show
modelharness plan status
modelharness plan validate problem/decomposition.json
modelharness plan apply problem/decomposition.json `
  --reason "S0 完成局部问题拆解"
```

每个问题节点声明问题、输入证据、输出证据、方法包、workstream、验收、审核、
风险和里程碑。系统自动生成 contract hash，并绑定任务、证据和审核。

## 方法包

```powershell
modelharness pack list
modelharness pack audit
modelharness pack lock
```

内置方法包覆盖问题形式化、候选模型竞争、数据与估计、求解器验证、UQ 与稳健性、
决策分析和证据成文。所有方法包在项目初始化时快照并锁定。

## 交付 Profile

```powershell
modelharness profile list
modelharness profile use cumcm
modelharness profile use mcm_icm
modelharness profile use real_world
```

- `cumcm`：按子问锁定数值结果与算法过程；
- `mcm_icm`：强化摘要、机制、叙事和决策含义；
- `real_world`：增加决策包、监控计划和人工授权点；
- `general`：通用证据报告。

## Evidence

```powershell
modelharness evidence add result.nominal --kind result `
  --statement "标称求解结果" --artifact results/nominal.json

modelharness evidence verify result.nominal

modelharness evidence revise result.nominal `
  --reason "审核发现边界条件错误"

modelharness evidence revoke result.nominal --reason "上游数据口径变化"
```

V3 问题图声明过的 evidence ID 会自动绑定当前合同与审核要求。

## Durable Work

```powershell
modelharness task list
modelharness task claim TASK_ID --worker solver-agent
modelharness task heartbeat TASK_ID --worker solver-agent
modelharness task finish TASK_ID --worker solver-agent `
  --result '{"artifact":"results/nominal.json"}'
modelharness task retry TASK_ID
```

`finish` 会执行任务合同中的 artifact、JSON 字段、证据登记和机械检查，失败会持久化为
failed，不能伪装为 completed。

## S0–S6 里程碑

| 里程碑 | 整体审计目标 |
|---|---|
| S0 | 题意、根问题、成功标准、问题图和失效出口 |
| S1 | 模型结构、假设、可辨识性与验证映射 |
| S2 | 数据血缘、清洗、估计和数据审核 |
| S3 | 求解器、toy、已知解和数值审核 |
| S4 | 留出验证、UQ、基线、敏感性和稳健性 |
| S5 | 条件式决策、复跑和结果锁定 |
| S6 | Profile 交付与逐项证据复核 |

里程碑不是固定工作队列。Autopilot 在当前里程碑内根据 Problem Graph 选择关键前沿。

## V2 兼容

没有 Problem Graph 的旧项目继续运行静态 V2 Playbook。迁移说明见
[MIGRATION_V2_TO_V3.md](docs/MIGRATION_V2_TO_V3.md)。

## 测试

```powershell
python -m pytest -q
python -m compileall -q modelharness
```

测试覆盖并发 Evidence、损坏状态、门禁防伪、Intake 回滚、任务租约、机器验收、
Problem Graph 环检测、合同签名、方法包防篡改、Profile 失效和 V2 兼容。

项目采用 [MIT License](LICENSE)。
