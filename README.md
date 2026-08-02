# Modeling Harness: Evidence-Gated Mathematical Modeling Agents

<p align="center">
  <img src="https://img.shields.io/badge/version-4.1.0-1f6feb" alt="Modeling Harness 4.1.0">
  <img src="https://img.shields.io/badge/Python-%3E%3D3.10-3776ab?logo=python&logoColor=white" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-4c1" alt="MIT License"></a>
</p>

Modeling Harness 是面向国赛、美赛与真实项目的数学建模 Agent 运行框架，开箱即用地
支持 **Codex** 与 **Claude Code** 双底座：clone 下来打开任一引擎、把题目丢进对话即可
跑到完整交付，且所有中间产物落盘可回溯。它让模型自由
拆题、选模、分支、回退并自主调用计算工具，同时用 Problem Graph、可复现执行和
Evidence Graph 保证：最终交付中的每个重要结论，都能追溯到当前版本的工件和验证。

> 让模型决定“下一步值得做什么以及怎么做”，让 Harness 只决定“动作是否已有权限、
> 结果是否可信、状态是否可以提交”。

它不是把整道开放题塞进一个超长提示词，也不是固定的多角色流水线。整题是持续维护的
研究目标，可独立证伪的局部问题才是求解单元；S0–S6 只是全局完成度投影，不规定 Agent
必须按顺序工作。

完整设计见 [4.x 架构](docs/ARCHITECTURE_V4.md)。升级说明见
[4.0 → 4.1](docs/MIGRATION_V40_TO_V41.md) 与
[3.1 → 4.0](docs/MIGRATION_V31_TO_V40.md)。

## How it works

~~~mermaid
flowchart TD
    U["题目、附件与交付 Profile"] --> P["Problem Graph<br/>目标、依赖、验收与失败出口"]
    P --> S["State Capsule<br/>按需派生当前研究状态"]
    S --> A["Agent 自由选择下一步<br/>拆分、实验、工具、分支或回退"]
    A --> L["项目内可逆研究<br/>默认直接执行"]
    A --> Q["新权限或外部副作用<br/>Action Proposal"]
    Q --> K{"Policy Kernel"}
    K -->|"允许或附加义务"| L
    K -->|"需授权"| H["用户"]
    L --> R["Artifacts + Trace<br/>资源、日志、输入输出哈希"]
    R --> V["Verifier Portfolio<br/>机械检查、独立后端、冷启动审核"]
    V -->|"PASS"| E["Evidence Graph<br/>当前可相信的结论"]
    V -->|"FAIL / INCONCLUSIVE"| S
    E --> P
    E --> M["S0–S6 Milestone Projection"]
    M --> D["论文、代码、结果与审计包"]
~~~

Agent 可以自由改变研究路线，但不能直接把自己的候选结论写成“已验证”。只有满足当前
合同、工件鲜度和验证要求的结果，才能进入权威状态。

每条结论通常经历：

~~~text
局部问题 → 候选方法 → 计算/推导 → 工件 → candidate evidence
         → 独立验证 → verified evidence → 下游结论与交付
~~~

若上游数据、程序、参数、策略或证据发生变化，相关结论会变为 stale、revoked 或
invalidated，并只从最小受影响节点重新计算。

## 自由边界

| Agent 自主决定 | Harness 保持的少量硬边界 |
|---|---|
| 如何拆题，是否拆分、合并或放弃路线 | 用户目标和交付义务不能被悄悄改写 |
| 使用何种模型、算法、近似与基线 | NOT_RUN 永远不能显示为 PASS |
| 是否调用工具、调用哪个工具、何时跳过 | verified 必须绑定当前输入、工件、审核与策略哈希 |
| 串行、并行、回溯、竞争路线与停止时机 | 错误证据必须沿依赖图级联失效 |
| 声明不可辨识、无唯一解或优势不显著 | 未知执行结果必须进入 RECOVERY_PENDING |
| 在已有权限内使用项目资源 | 已登记的 producer 不能批准自己的结论 |
| 在预算内探索更优路线 | mandatory requirement 未闭合时，S6 不能宣告完整交付 |
| 自主选择推导与数值路线 | 正式数字必须绑定权威字段，且推导式与源字段一致 |
| 自主设计随机实验 | 随机选择后的头条数字必须来自独立 report set，或明确降级 |
| 自主组织内部工作稿 | 成稿含内部标记、临时路径或未解析模板时不得交付 |

普通、本地、项目内、可逆的研究不需要逐动作审批。只有联网、安装、商业许可证、外部
写入、不可恢复操作或超出既有预算时，Policy Kernel 才要求额外授权或附加义务。

## Quickstart

### 对话式零配置

~~~powershell
git clone https://github.com/Ephemeral6/modeling-harness.git
~~~

然后用 **Codex** 或 **Claude Code** 打开仓库目录，把题面和附件直接丢进对话：

> 使用 Modeling Harness 完整解决该题，允许 Agent 自主选择本地计算工具，直接开始。

不需要手动安装、建目录、复制附件或粘贴提示词。会话契约会驱动 Agent 自动完成：

1. **自举**：探测 `modelharness` CLI，缺失则 `pip install -e .`（核心零依赖，秒级）；
2. **接题**：`intake` 建立隔离项目——上传附件复制进 `problem/data_raw/` 并锁定
   SHA-256；对话里粘贴的题面同样物化为
   `problem/data_raw/001__pasted_statement.md` 进 manifest，与附件同等保留；
3. **推进**：选 Profile → `doctor` → 持续 `work next`，直到论文、代码、结果、
   审计包全部交付。

两个引擎共用同一份契约，行为一致：

| 引擎 | 会话入口 | 说明 |
|---|---|---|
| Codex（Desktop / CLI） | `AGENTS.md` | 历史默认底座 |
| Claude Code（CLI / Desktop） | `CLAUDE.md` | 薄 shim，引导回同一份 `AGENTS.md` 契约 |

引擎选择写入 `modeling-project.json` 的 `engine` 字段（`intake` / `new` 支持
`--engine {codex,claude-code,auto}`，默认按已安装 CLI 自动检测）；
`modelharness doctor` 会报告两个引擎的本机可用性。详见
[CONVERSATION_START.md](CONVERSATION_START.md)。

### 交付 Profile

核心运行时只依赖 Python 标准库；计算库按 Profile 作为可选 extras 安装
（如 `pip install -e ".[cumcm]"`），由 Agent 在接题后根据环境探测自主处理。
可选 Profile：

| Profile | 重点 |
|---|---|
| `cumcm` | 过程复现、数据处理、优化、图算法、Excel 与结果核查 |
| `mcm_icm` | 机制解释、敏感性、决策叙事与英文论文交付 |
| `real_world` | 数据治理、外部约束、监控、权限与可部署建议 |
| `general` | 不预设竞赛风格的通用建模 |

Modeling Harness 是 Agent 的研究运行层，不绑定特定模型供应商，也不会仅靠
`modelharness intake` 在后台凭空生成答案。负责求解的 Codex、Claude Code 或其他
Agent 需要在项目根目录持续读取 `work next`、生成工件并提交验证。

## 全落盘与断点回溯

会话上下文被视为一次性缓存，磁盘才是记忆。你可以在任何时刻关闭 Codex 或
Claude Code，重开后 Agent 只凭磁盘状态完全重建现场，不依赖对话历史：

- **题面与附件**：`problem/data_raw/` 只读保留原件（含对话粘贴文本），
  `intake_manifest.json` 锁定哈希与来源；
- **数值与结果**：全部来自 `results/*.json`、`data/processed/` 的机器产物文件，
  附生成脚本；进入论文的每个数字必须能指出磁盘出处，说不出即不得使用；
- **判断与死因**：假设取舍、失败原因、被放弃的路线在产生的同一个工作单元内
  追加进 `docs/notebook.md` 与 `docs/decisions.log`（append-only，禁止改写历史）；
- **权威状态**：Problem Graph、Evidence Graph、任务与租约（SQLite）、tool run
  的输入输出哈希、S0–S6 印章全部落盘于 `.harness/`。

恢复方式：重开引擎后说“继续”，Agent 会读取 `projects/.current.json` 指向的项目，
运行 `doctor` + `state` + `work next`，从关键前沿接着推进；已验证的证据不会被重做。

## Agent loop

`work next` 返回当前关键前沿、合同、可用输入和 State Capsule。Agent 每次只需选择一个
最值得做的局部动作：

~~~powershell
# 查看全局状态与关键前沿
modelharness state
modelharness work next

# 检查某节点需要什么计算能力，并自主选择 use 或 skip
modelharness tool recommend s3.solver_validation
modelharness tool decide s3.solver_validation `
  --action auto `
  --reason "需要数值求解、toy case 与残差检查"

# 用结构化 argv 执行，避免 shell 拼接；记录输入、输出、种子和 validators
modelharness tool run s3.solver_validation `
  --argv-file config/solver.argv.json `
  --tool numpy --tool scipy `
  --input src/solver.py `
  --output results/nominal.json `
  --validators-file config/solver.validators.json `
  --seed 2026 `
  --timeout 3600

modelharness tool audit-node s3.solver_validation
modelharness work next
~~~

如果解析推导、反例或已有证据已经足够，Agent 可以明确跳过工具：

~~~powershell
modelharness tool decide s1.model_formulation `
  --action skip `
  --reason "解析反例已淘汰候选模型，无需数值计算"
~~~

## 计算工具自治

Harness 提供能力目录、环境探测、选择记录、结构化执行、资源限制、日志和验证器，而不是
强迫 Agent 使用某一套软件。

~~~powershell
modelharness tool capabilities
modelharness tool env show cumcm
modelharness tool doctor --profile cumcm
modelharness tool lock
modelharness tool audit --environment
~~~

目录可以识别或适配：

- 数值与科学计算：Python、NumPy、SciPy、Julia、Octave、MATLAB；
- 数据与统计：Pandas、statsmodels、scikit-learn、R；
- 符号、约束与优化：SymPy、Z3、CVXPY、OR-Tools、Gurobi、SCIP、CBC、GLPK；
- 图、仿真与不确定性：NetworkX、SimPy、PyMC、ArviZ；
- 机器学习：PyTorch、XGBoost、LightGBM；
- GIS：GeoPandas、Shapely、Rasterio、Folium；
- 可视化与交付：Matplotlib、Seaborn、Plotly、Pandoc、LaTeX、openpyxl。

这些工具不是全部随核心包安装。`tool doctor` 先探测真实环境；缺失能力可降级、换路线或
请求授权，不能被伪装成已经运行。

每次 tool run 记录完整非 shell argv、cwd、timeout、随机种子、工具版本、输入输出
SHA-256、stdout/stderr、退出码、耗时和 validator 结果。正式结论应按问题性质加入
toy case、解析极限、穷举对拍、约束残差、独立后端、留出验证或收敛诊断。

## Trust model

### 正交状态

执行是否完成、结论是否通过、谁作出裁决、工件是否仍然新鲜是四件不同的事：

~~~text
execution_status = not_run | queued | running | completed | error |
                   recovery_pending | cancelled
verdict          = unassessed | pass | fail | inconclusive | not_applicable
authority        = machine | human | hybrid
freshness        = valid | stale | missing | tampered
~~~

因此“检查没运行”“程序报错”“数学结论失败”“人工接受风险”不会被压成同一个模糊状态。
人工可以判定检查不适用或接受风险，但不能把机械 FAIL 偷换成机械 PASS。

### Soundness 与 completeness

4.1 在“已有结论是否可信”的 soundness 轴之外，增加“题面要求是否全部回答”的
completeness 轴。`source_segmentation.json` 保证原文片段不被静默丢弃，
`requirements.json` 建立 requirement → claim → verified evidence 的闭合链。
未闭合 mandatory requirement 是交付 Gate；搜索边界、保守假设分支和潜在改进则进入
Opportunity Ledger，作为预算内优化信号，不会被误写成机械 FAIL。

### 工件鲜度与级联撤销

verified evidence 绑定 Problem Graph 合同、输入证据、工件、工具运行、审核和验证策略的
哈希。绑定对象变化后，旧验证会自动过期。撤销传播但不删除历史：

~~~text
root evidence       verified → revoked
dependent evidence  candidate/verified → invalidated
dependent task      completed → invalidated
review binding      current → stale
milestone stamp     valid → archived
paper claim         current → stale
~~~

### 独立审核

生成者可以自检、运行测试、登记 candidate 并根据反馈修复；生成者不能把自己产生的语义
结论提升为 verified。Schema、哈希、残差等确定性义务可以由独立程序批准，模型适用性、
假设合理性和结论边界则需要隔离上下文的审核。

审核文件采用 append-only 谱系。Problem Graph 保留稳定的逻辑审核名，复审实际写入
`_v2.json`、`_v3.json` 等不可变版本；Harness 自动选择与当前合同及工件哈希匹配的
最新记录。旧 REJECT 无需覆盖、归档或删除，因此跨 Agent 的 Windows ACL 不再成为
研究阻断。

### 不明执行恢复

超时或断线不等于“没有执行”。若外部动作、后台进程或部分输出的最终状态未知，运行进入
`RECOVERY_PENDING`，系统冻结下游并禁止盲目重试非幂等动作：

~~~powershell
modelharness tool recover RUN_ID `
  --outcome safe_to_retry `
  --authority human `
  --note "已核对进程、事务号和输出目录，原执行未生效"
~~~

可选 outcome 为 `recovered_success`、`confirmed_failed`、`safe_to_retry` 或
`human_required`。

## Problem Graph, Evidence Graph and milestones

Problem Graph 描述“还必须解决什么”，Evidence Graph 描述“当前已经验证了什么”。
Workflow 只负责耐久任务、租约、心跳、重试与恢复；它们不会互相冒充。

~~~powershell
modelharness plan show
modelharness plan status
modelharness plan validate problem/decomposition.json
modelharness plan apply problem/decomposition.json `
  --reason "根据数据审计拆分两条竞争路线"

modelharness evidence add result.nominal `
  --kind result `
  --statement "标称条件下的可复现求解结果" `
  --artifact results/nominal.json
modelharness evidence audit
~~~

S0–S6 是跨路线的完成度投影：

| Milestone | 审计目标 |
|---|---|
| S0 | 题意、成功标准、问题图与失败出口 |
| S1 | 模型结构、假设、可辨识性与工具判断 |
| S2 | 数据血缘、清洗、估计与防泄漏 |
| S3 | 求解器、toy、已知解、残差与数值审核 |
| S4 | 留出、UQ、基线、敏感性与稳健性 |
| S5 | 条件式决策、复跑与结果锁定 |
| S6 | Profile 交付、重渲染与逐项证据复核 |

Agent 可以在 S4 发现问题后回到模型层，也可以在数据不足时交付“不可辨识”，不必为了
流程完整而制造伪精确答案。

## Benchmark and episode packages

Benchmark Lab 分为局部能力 L1、组合建模 L2 和端到端 L3，并单独覆盖安全与退化问题。
评分不以论文流畅度替代数值正确性、证据闭合率和重复运行可靠性。

~~~powershell
# 项目通用评估
python -m modelharness.evaluation --project <project>

# 隐藏或机械 rubric
python -m modelharness.benchmarking `
  benchmarks/fixtures/l1_numeric.json `
  --project <project> `
  --json report.json

# 归档可重放 Episode
python -m modelharness.episode `
  --project <project> `
  --out episodes/run-001 `
  --benchmark-id l1-numeric `
  --model <model> `
  --seed 2026
~~~

Episode Package 包含题目、配置、环境、动作轨迹、失败归因、工具日志、Evidence、审核、
结果、论文与评分，用于固定模型后的 Harness 消融和 `pass^k` 统计。详情见
[Benchmark Lab](benchmarks/README.md)。

## Layout

~~~text
modelharness/   核心引擎：图、调度、工具、工作流、证据、策略、恢复与 CLI
templates/      新建数学建模项目时复制的 Agent 合同、Profile 与方法包
benchmarks/     L1/L2/L3 评测规范、可执行 rubric 与回归 fixtures
docs/           架构、失败模型、工具链和版本迁移
tests/          单元、集成、退化、恢复与 4.1 不变量测试
legacy_v1/      只读历史实现
~~~

4.1 延续 4.0 的 Problem Graph 和自主 Toolchain，不引入必须遵守的固定角色编排，也不把
Proposal 变成普通研究的审批流水线。新增 Requirement、Claim、Holdout 与 Delivery
四道 Gate，并把“未取得价值”登记为 Opportunity；这些视图仍不构成平行权威数据库。

## Development

~~~powershell
python -m compileall -q modelharness
python -m pytest -q
git diff --check
~~~

- 3.0 架构：[ARCHITECTURE_V3.md](docs/ARCHITECTURE_V3.md)
- 3.1 工具链：[TOOLCHAIN_V31.md](docs/TOOLCHAIN_V31.md)
- 4.0 架构：[ARCHITECTURE_V4.md](docs/ARCHITECTURE_V4.md)
- 4.1 迁移：[MIGRATION_V40_TO_V41.md](docs/MIGRATION_V40_TO_V41.md)
- 失败模型：[FAILURE_MODEL.md](docs/FAILURE_MODEL.md)
- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略：[SECURITY.md](SECURITY.md)

## Related work

README 的叙事结构参考了
[Danus](https://github.com/frenzymath/Danus) 对数学推理系统“如何工作、权威边界、目录、
快速启动、设计不变量”的组织方式。Danus 聚焦带 Fact-Graph Memory 的数学证明；
Modeling Harness 面向包含数据、估计、优化、仿真、不确定性与决策的开放数学建模，并
采用更薄的治理层，让 Agent 在项目内保持更大的研究自由。

项目采用 [MIT License](LICENSE)。
