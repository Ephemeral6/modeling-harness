# Problem Graph + Toolchain Autopilot 协议

## 外循环

```text
ORIENT
  → COMPUTE READY FRONTIER
  → SELECT LOCAL PROBLEM
  → RECOMMEND COMPUTATION CAPABILITIES
  → AGENT USE / SKIP DECISION
  → LOCAL RESEARCH CELL
  → VERIFY TOOL RUN AND EVIDENCE
  → INTEGRATE
  → MILESTONE GATE
  → NEXT FRONTIER
```

主控每个工作单元后运行 `modelharness work next --project .`。

## 局部研究循环

```text
FRAME → COMPETE → DISCRIMINATE → CHOOSE TOOL → IMPLEMENT → COLD REVIEW
                       ↑              ↓                          |
                       └──── VALIDATE / REPAIR ──────────────────┘
```

计算节点必须有：

- 能力需求和可用工具候选；
- Agent 的 use/skip 理由；
- 结构化 argv、输入、输出、种子、超时和预算；
- toy、解析解、穷举、残差、交叉后端或统计验证；
- 当前 contract 和 decision hash 绑定的 run manifest。

## 自主与边界

Agent 自主选择是否调用本地计算工具，不因推荐而被迫使用复杂模型。以下情况优先 skip：

- 解析结果已经回答局部问题；
- 低成本反例已经淘汰路线；
- 工具增加的复杂度超过判别收益；
- 输入不足导致计算只会制造伪精确。

以下情况优先 use：

- 手算无法可靠覆盖正式规模；
- 需要数据清洗、估计、搜索、仿真或可视化；
- 需要对拍、压力测试、敏感性或不确定性传播；
- 机器结果是最终数字的唯一来源。

联网、安装、商业许可、外部写入和超预算计算不在默认自主权内。

## 长计算

- 先 smoke、再 toy/小规模、最后正式规模；
- 分片范围互不重叠；
- 固定随机种子并原子写 checkpoint；
- 每片保留输入输出哈希和日志；
- lease 过期只恢复未完成任务；
- 正常运行不重复已验证分片。

## 停止条件

只允许：

1. Problem Graph、tool decisions/runs、S0–S6 和 Delivery Profile 全部闭合；
2. 缺少只有用户能提供的文件、凭据、许可证或重大目标选择；
3. 同一阻断连续三轮且没有安全降级；
4. 下一步超出授权或需要高风险外部写入。
