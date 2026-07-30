# Problem Graph Autopilot 协议

## 外循环

```text
ORIENT
  → COMPUTE READY FRONTIER
  → SELECT HIGHEST-VALUE LOCAL PROBLEMS
  → LOCAL RESEARCH CELLS
  → VERIFY
  → INTEGRATE
  → MILESTONE GATE
  → NEXT FRONTIER
```

主控每个工作单元后运行：

```powershell
modelharness work next --project .
```

## 局部研究循环

```text
FRAME → COMPETE → DISCRIMINATE → IMPLEMENT → COLD REVIEW → VERIFY
                       ↑                              |
                       └──────── REPAIR ─────────────┘
```

局部问题必须有：

- 精确问题和输入证据；
- 输出 evidence ID、artifact 和 contract hash；
- 方法包与最小测试；
- 唯一写入范围；
- 验收命令与预算；
- 审核路径与失败出口。

## 并行条件

同时满足才并行：

- 问题节点依赖已闭合；
- 输入和验收明确；
- 写入范围不重叠；
- 计算或搜索收益大于沟通成本。

并行结果必须逐个验收，不以自然语言汇报代替机器产物。

## 审核

审核者冷启动，只读取题面、问题合同、输入证据、产物和检查。V3 审核 JSON 必须包含：

```json
{
  "reviewer": "numerics-auditor",
  "task_id": "TASK_ID",
  "contract_hash": "HASH",
  "verdict": "APPROVE",
  "scope": ["..."],
  "findings": [],
  "required_fixes": [],
  "evidence_checked": ["result.example"],
  "artifact_hashes": {"results/example.json": "SHA256"}
}
```

REJECT 后修订同一 evidence ID，保留谱系，再以新产物哈希创建冷审任务。

## 长计算

- 先 smoke、再小规模、最后正式规模；
- 分片范围互不重叠；
- 每片原子写 checkpoint；
- 任务预算来自各自合同；
- lease 过期只恢复未完成任务；
- 正常运行不重复已完成分片。

## 停止条件

只允许：

1. Problem Graph 根义务、S0–S6 和 Delivery Profile 全部闭合；
2. 缺少只有用户能提供的文件、凭据或重大目标选择；
3. 同一阻断连续三轮且没有安全替代；
4. 下一步超出授权或需要高风险外部写入。

