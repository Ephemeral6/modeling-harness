# Modeling Harness 3.1

面向国赛、美赛和真实数学建模任务的、问题图驱动、工具自主、证据门禁的 Agent 执行
框架。整题是编排边界，可独立证伪的局部问题是求解单元。

3.1 在 3.0 的 Problem Graph 和可靠性内核上增加正式 Toolchain Layer：Agent 可以根据
局部问题自主选择调用或不调用计算工具，但选择、版本、输入、输出、种子、日志和验证
必须可审计。

## 架构

```text
Conversation / CLI
        ↓
Problem Graph ── obligations / dependencies / contract hashes
        ↓
Critical Frontier Scheduler
        ↓
Toolchain ── capability probe → Agent use/skip → reproducible run
        ↓
Workflow SQLite ── lease / heartbeat / acceptance / retry
        ↓
Evidence Graph ── typed DAG / checks / cold review / revoke
        ↓
S0–S6 Milestones
        ↓
Delivery Profiles ── CUMCM / MCM-ICM / Real World / General
```

权威状态：

- `.harness/problem_graph.json`：还必须解决什么；
- `.harness/tool_*`：为何调用或跳过工具、实际运行了什么；
- `.harness/evidence.json`：已经验证了什么；
- `.harness/workflow.sqlite3`：谁在做什么；
- `.harness/stamps/sN.json`：哪些整体里程碑闭合。

详细设计见 [ARCHITECTURE_V3.md](docs/ARCHITECTURE_V3.md) 和
[TOOLCHAIN_V31.md](docs/TOOLCHAIN_V31.md)。

## 快速开始

```powershell
python -m pip install -e .

modelharness intake --title "真实题目" --prompt "完整解决" `
  --file 题面.pdf --file 数据.xlsx

modelharness profile use cumcm
modelharness doctor
modelharness tool doctor
modelharness work next
```

主控在每个局部任务、工具运行、审核、证据验证或 Gate 后继续运行 `work next`，直到
Problem Graph、工具决策、证据、S0–S6 和交付 Profile 全部闭合。

## Agent 自主工具调用

```powershell
modelharness tool capabilities
modelharness tool recommend s3.solver_validation

modelharness tool decide s3.solver_validation `
  --action auto `
  --reason "需要正式数值求解、toy 和残差检查"
```

Agent 可以改选其他允许的工具，也可以 skip：

```powershell
modelharness tool decide s1.model_formulation `
  --action skip `
  --reason "解析反例已淘汰候选，无需数值计算"
```

use 后必须结构化运行。建议把 argv 和 validators 放入 JSON 文件：

```powershell
modelharness tool run s3.solver_validation `
  --argv-file config/solver.argv.json `
  --tool numpy --tool scipy `
  --input src/solver.py `
  --output results/nominal.json `
  --validators-file config/solver.validators.json `
  --seed 2026 --timeout 3600

modelharness tool audit-node s3.solver_validation
```

默认自主权限只覆盖项目内本地计算。联网、安装软件、商业许可证、外部写入或超预算运行
需要新授权。

## 内置计算工具目录

目录覆盖以下能力，实际可用性由 `tool doctor` 探测：

- 数值与科学计算：Python、NumPy、SciPy、Julia、Octave、MATLAB；
- 数据与统计：Pandas、statsmodels、scikit-learn、R；
- 符号与约束：SymPy、Z3；
- 优化：CVXPY、OR-Tools、Gurobi、SCIP、CBC、GLPK；
- 图与网络：NetworkX；
- 仿真与不确定性：NumPy、SciPy、SimPy、PyMC、ArviZ；
- 机器学习：scikit-learn、PyTorch、XGBoost、LightGBM；
- GIS：GeoPandas、Shapely、Rasterio、Folium；
- 可视化与交付：Matplotlib、Seaborn、Plotly、Pandoc、LaTeX；
- Excel：openpyxl。

Harness 核心仍为零第三方依赖。缺失工具会触发降级或请求授权，不会导致框架本身无法
启动。

## 可复现与验证

每个 tool run 记录：

- Problem Graph contract hash 和 Agent decision hash；
- 工具版本、执行器、Python 版本；
- 完整非 shell argv、cwd、timeout、随机种子；
- 输入和输出的路径、大小、SHA-256；
- stdout/stderr 路径及 SHA-256；
- 退出码、耗时和验证结果。

内置验证器支持非空文件、JSON 字段、有限数、数值断言、跨产物对拍和 `checks/` 下的
独立 Python 检查。正式计算应至少包含 toy、解析极限、穷举、约束残差、独立后端、
留出验证或统计收敛中的一种。

## 方法包

```powershell
modelharness pack list
modelharness pack audit
modelharness pack lock
```

除问题形式化、模型竞争、数据估计、求解验证、稳健性、决策和证据写作外，3.1 还提供：

- 时间序列；
- 数学优化；
- 微分方程；
- 蒙特卡洛；
- 空间分析；
- 网络分析；
- 机器学习；
- 贝叶斯建模；
- 离散事件仿真。

方法包声明 required/preferred capability、验证协议和降级出口。

## 计算环境 Profile

```powershell
modelharness tool env list
modelharness tool env show cumcm
modelharness tool doctor --profile cumcm
modelharness tool lock
modelharness tool audit --environment
```

- `general`：标准数值、数据、符号与绘图；
- `cumcm`：强化统计、优化、图算法、Excel 和过程复现；
- `mcm_icm`：强化机制、敏感性、论文渲染和决策叙事；
- `real_world`：强化治理、监控和外部授权边界。

环境文件包含 required/optional 工具、版本约束和 pip/external requirements。工具目录
锁与环境快照分离：目录篡改会阻断 Gate，环境漂移由 doctor 报告；已使用工具的版本还会
写入每个 run。

## Problem Graph

```powershell
modelharness plan show
modelharness plan status
modelharness plan validate problem/decomposition.json
modelharness plan apply problem/decomposition.json `
  --reason "S0 完成局部问题拆解"
```

每个节点声明问题、输入证据、输出证据、方法包、workstream、验收、审核、风险和里程碑。
合同包含节点语义、方法包、工具目录和自主策略哈希。

## Evidence 与 Durable Work

```powershell
modelharness evidence add result.nominal --kind result `
  --statement "标称求解结果" --artifact results/nominal.json
modelharness evidence verify result.nominal

modelharness task claim TASK_ID --worker solver-agent
modelharness task heartbeat TASK_ID --worker solver-agent
modelharness task finish TASK_ID --worker solver-agent `
  --result '{"artifact":"results/nominal.json"}'
```

计算型任务的 acceptance 会检查 Agent 工具决策；use 时还要求当前合同下存在 verified
run。自然语言汇报不能把任务变成 completed。

## S0–S6

| 里程碑 | 审计目标 |
|---|---|
| S0 | 题意、成功标准、问题图和失效出口 |
| S1 | 模型结构、假设、可辨识性和工具判断 |
| S2 | 数据血缘、清洗、估计和防泄漏 |
| S3 | 求解器、toy、已知解、残差和数值审核 |
| S4 | 留出、UQ、基线、敏感性和稳健性 |
| S5 | 条件式决策、复跑和结果锁定 |
| S6 | Profile 交付、重渲染和逐项证据复核 |

## 迁移与测试

- V2 → V3：[MIGRATION_V2_TO_V3.md](docs/MIGRATION_V2_TO_V3.md)
- V3.0 → V3.1：[MIGRATION_V30_TO_V31.md](docs/MIGRATION_V30_TO_V31.md)

```powershell
python -m pytest -q
python -m compileall -q modelharness
```

项目采用 [MIT License](LICENSE)。
