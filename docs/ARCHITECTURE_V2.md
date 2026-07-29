# Modeling Harness 2.0 架构

## 1. 分层

```text
Interfaces
  ├─ Codex conversation contract
  └─ modelharness CLI
Application
  ├─ Intake use case
  ├─ Autopilot use case
  └─ Doctor / status
Workflow
  ├─ durable task ledger
  ├─ lease / heartbeat / reconcile
  └─ stage playbooks
Domain
  ├─ Evidence DAG
  ├─ stage invariants
  └─ path ownership rules
Infrastructure
  ├─ atomic JSON repository
  ├─ SQLite WAL workflow store
  ├─ cooperative file locks
  └─ process execution
```

## 2. 状态与真相

- `evidence.json` 是研究结论的 typed DAG，带 revision 和内容哈希。
- `workflow.sqlite3` 是任务和事件的持久状态，WAL + FULL synchronous。
- `stamps/sN.json` 是可验证投影，不是仅凭存在即可相信的标记。
- `projects/.current.json` 只是 UI 指针，不参与研究正确性。

Autopilot 只接受从 S0 开始连续有效的印章前缀。任何配置、证据、审核或上游印章变化
都会使后续印章链失效。

## 3. Subagent 生命周期

```text
pending → claimed → running → completed
                    └──────→ failed
```

Claim 带 worker 身份、attempt 和 lease。worker 定期 heartbeat。进程消失后，
`reconcile` 将未超过尝试预算的任务恢复为 pending；超预算任务转为 failed。

写入所有权先做规范化和大小写折叠，`src` 与 `src/solver.py` 被判定为冲突。

## 4. 故障处理

| 故障 | 行为 |
|---|---|
| JSON 半写/编码损坏 | 抛出 CorruptStateError，停止推进 |
| 并发 Evidence 更新 | 文件锁串行化，原子替换防止丢更新 |
| 伪造 stamp | schema、配置哈希和链验证失败 |
| Artifact 被修改 | Evidence audit 与 stamp validation 失败 |
| Review 被改写 | 审核哈希不匹配，阶段失效 |
| Worker 崩溃 | lease 到期后 reconcile |
| Intake 重试 | 创建新的唯一 run，不覆盖旧项目 |
| 下游返工 | 印章移入 archive，并记录原因 |

## 5. 兼容策略

v1 实现和旧测试保存在 `legacy_v1/`，不进入安装包和正式测试。原来的模块调用入口仍
由 v2 实现承接。v1 字符串型检查命令暂时兼容，但会标记为 `legacy_shell=true`；
新配置应使用 argv 数组，以 `shell=False` 执行。
