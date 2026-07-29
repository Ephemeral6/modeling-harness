# Modeling Harness 2.0

面向真实数学建模任务的、可恢复的多智能体执行框架。用户在 Codex 中上传题目与附件
并说“直接开始”，系统自动创建隔离项目，持续推进 S0–S6，协调 subagent，验证证据，
处理返工，并从可信证据生成论文。

## 2.0 架构

```text
Codex Conversation / CLI
          ↓
Unified Command Interface
          ↓
Autonomous Workflow Engine ── SQLite task lease / heartbeat / retry
          ↓
Stage Service ── chained, self-validating S0–S6 stamps
          ↓
Evidence Kernel ── typed DAG / atomic transaction / cascade revoke
          ↓
Filesystem + Process Adapters ── atomic JSON / locks / checkpoints
```

核心不变量：

- 所有 JSON 状态使用临时文件、`fsync` 和原子替换；
- Evidence 读改写有跨进程锁和 revision；
- Subagent 任务保存在 SQLite，支持 claim、lease、heartbeat、失败恢复；
- 父目录和子目录被视为冲突写入范围；
- Gate 印章覆盖配置、上游印章、证据和审核哈希；
- 伪造、陈旧或上游失效的印章不能推动 Autopilot；
- 状态损坏时 fail closed，不把缺失/损坏解释成空状态；
- 撤销证据时保守失效下游阶段，旧印章归档而非直接删除。

详细设计见 [ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md)。

## 对话式使用

```powershell
git clone https://github.com/Ephemeral6/modeling-harness.git
cd modeling-harness
python -m pip install -e .
```

在 Codex Desktop 中打开仓库，上传题面 PDF、Word、Excel、CSV 等文件，然后说：

> 使用这个 Harness 完整解决该题。全程不使用 Claude，直接开始。

根目录 `AGENTS.md` 会要求 Codex 自动 Intake，并持续调用：

```powershell
modelharness autopilot next
```

直到 S6 完成或遇到真正需要用户输入/授权的阻断。

## 统一 CLI

```powershell
modelharness intake --title "真实题目" --prompt "完整解决" --file 题面.pdf --file 数据.xlsx
modelharness status
modelharness doctor
modelharness autopilot next

modelharness evidence add result.nominal --kind result `
  --statement "标称求解结果" --artifact results/nominal.json
modelharness evidence verify result.nominal

modelharness task list
modelharness task claim TASK_ID --worker solver-agent
modelharness task heartbeat TASK_ID --worker solver-agent
modelharness task finish TASK_ID --worker solver-agent --result '{"artifact":"results/x.json"}'

modelharness gate s0
modelharness invalidate s2 --reason "数据口径变化"
modelharness narrative build
modelharness narrative audit
```

旧的模块入口 `python -m modelharness.conversation` 与
`python -m modelharness.autopilot next` 仍可使用，但推荐统一 CLI。

## S0–S6

| 阶段 | 目标 |
|---|---|
| S0 | 题意、决策问题、数据清单、成功标准和失效出口 |
| S1 | 多候选竞争、正式模型、假设与验证映射 |
| S2 | 数据血缘、清洗、估计与数据审核 |
| S3 | 求解器、toy、已知解与数值审核 |
| S4 | 留出验证、UQ、基线、敏感性和稳健性 |
| S5 | 条件式决策、复跑和结果锁定 |
| S6 | 证据驱动成文与逐句论文复核 |

## 开发与测试

```powershell
python -m pytest -q
python -m compileall -q modelharness
```

测试覆盖并发 Evidence 更新、半写 JSON、伪造/陈旧印章、附件重试、写入范围冲突、
worker lease 过期恢复、Autopilot 幂等任务生成和验证失败持久化。

项目采用 [MIT License](LICENSE)。
