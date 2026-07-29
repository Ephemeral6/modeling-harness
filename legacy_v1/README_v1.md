# Modeling Harness

面向真实数学建模问题的、证据门禁式多智能体执行系统。它不是提示词合集，也不是
Codex Skill：它是一个可复制的项目骨架、命令行控制面、证据 DAG、阶段门禁、独立
审核协议和论文叙事流水线。

## 核心闭环

```text
题面 → 候选模型竞争 → 数据/实现 → 分层验证 → UQ/红队
  → 决策锁定 → 证据驱动成文 → 论文逐句复核
                ↑
       verified evidence DAG
```

系统借鉴 Danus 的职责隔离：主控负责计划与调度，工作者探索局部任务，独立验证器
掌握“写入真相层”的唯一入口；但本系统把 theorem fact graph 扩展成建模 evidence
graph，使数据血缘、假设、代码、实验、结果、决策和限制都可追溯。

## 与普通 Agent 的区别

- 计划和草稿不等于事实；只有通过检查的节点才是 `verified`。
- 上游证据撤销会沿依赖图级联撤销下游结论。
- S0–S6 门禁阻止“模型没验证就优化、稳健性没做就写唯一最优”等跳步。
- 论文写作者只能读取 verified 证据包；论文审核者再逐句检查结论强度。
- Harness 不绑定 Claude、Codex CLI 或某个模型。Codex Desktop 是推荐的交互主控。

## 安装

在 PowerShell 中：

```powershell
cd C:\Users\你的用户名\Desktop\数学建模\modeling-harness
python -m pip install -e .
modelharness --help
```

不安装也可使用：

```powershell
$env:PYTHONPATH="C:\...\modeling-harness"
python -m modelharness.cli --help
```

## 创建项目

```powershell
modelharness new C:\work\contest-2026-A --title "2026 国赛 A 题"
cd C:\work\contest-2026-A
```

把题面写入 `problem/statement.md`，原始数据放入 `problem/data_raw/`，然后在 Codex
中打开该目录。

## 证据图

候选结论先登记：

```powershell
modelharness evidence add problem.statement `
  --kind problem `
  --statement "题面和约束已完整登记" `
  --artifact problem/statement.md

modelharness evidence verify problem.statement
```

带依赖和机械检查的节点：

```powershell
modelharness evidence add result.nominal `
  --kind result `
  --statement "标称场景求解结果" `
  --artifact results/nominal.json `
  --depends code.solver,result.parameters `
  --check "python checks/l2_nominal.py"

modelharness evidence verify result.nominal
```

如果上游假设被推翻：

```powershell
modelharness evidence revoke model.assumptions --reason "留出残差显示时序相关"
modelharness invalidate s1
```

## 阶段推进

`config/stages.json` 定义每阶段必须存在的 verified 节点、独立审核 JSON 和机械检查：

```powershell
modelharness status
modelharness gate s0
modelharness gate s1
```

推荐阶段：

| 阶段 | 交付目标 |
|---|---|
| S0 | 题意、决策问题、成功标准、失效出口 |
| S1 | 多候选竞争、正式模型、假设与验证映射 |
| S2 | 数据血缘、清洗、参数估计和数据审计 |
| S3 | 求解器、toy、已知解、标称结果和数值审计 |
| S4 | 留出验证、UQ、敏感性、基线、反事实和红队 |
| S5 | 最终决策、适用条件、预测/结果哈希锁 |
| S6 | 证据驱动成文、逐句论文复核 |

模板里的节点名是契约，可以在 `config/stages.json` 中按题目调整。

## 论文叙事

生成只包含已验证材料的写作包：

```powershell
modelharness narrative build
```

写作者按六个读者问题组织，而不是按工作时间线：

1. 真正要做什么决策？
2. 题目的核心信息困难是什么？
3. 模型的哪一部分化解了这个困难？
4. 哪条验证链说明结果可信？
5. 在收益、风险和基线下应采取什么行动？
6. 在什么条件下结论失效或需要重做？

正文关键句保留 `[[result.uq]]` 这样的节点引用。审核：

```powershell
modelharness narrative audit --paper paper/draft.md
```

## 在 Codex Desktop 中使用

打开新项目目录，给主控第一条消息：

> 读取 AGENTS.md 和 modeling-project.json。你是 Main Orchestrator。接管这个真实
> 数学建模问题，从 S0 开始；先检查现有状态和题面，建立任务图。可并行且写入不
> 冲突时使用 subagent。每个阶段先完成证据登记和独立审核，再运行 gate；不要跨阶段。

后续通常只需说：

- “汇报状态，继续当前阶段。”
- “开 3 个 subagent 独立提出不同候选模型，禁止互相读取草稿。”
- “让无状态 numerics-auditor 只看规格、代码和产物做复核。”
- “发现上游问题就撤销依赖节点并返工，不要修饰最终文字。”
- “S5 通过后生成 narrative brief，再写论文并让 paper-verifier 逐句审。”

Codex 会自动读取项目根目录的 `AGENTS.md`。角色的细化合同位于
`prompts/roles/`；主控应把相应合同和明确的文件写入范围交给 subagent。

## 项目目录

```text
.harness/          证据图与不可伪造的阶段印章
config/            阶段契约、论文叙事配置
problem/           题面和只读原始数据
data/              清洗数据与血缘账本
docs/              规格、假设、决策日志
src/               模型与求解器
checks/            L0–L5 分层机械检查
results/           机器生成结果
reviews/           独立审核 JSON
predictions/       最终决策/预测及哈希锁
paper/             证据包和论文
prompts/roles/     多智能体角色合同
```

## 设计边界

Harness 能强制留下证据链和阻止明显跳步，但不能保证研究问题必然可解，也不能替代
参赛者对创新性、题意取舍和最终表达负责。外部数据、文献和竞赛规则仍需人工核验；
超长计算应使用检查点和互不重叠的 worker 范围。
