# 项目 Autopilot 协议

主控在每个工作单元后运行：

```powershell
python -m modelharness.autopilot next --project .
```

返回包定义当前 `stage`、`phase`、缺失 verified 证据、独立审核者以及建议的
`parallel_agent_plan`。主控执行后再次调用，直到 S6 完成。

## 循环

```text
定位当前状态 → 拆分 → 并行执行 → 检查产物 → 独立审核
     → gate → 下一阶段
        ↑
     修复/撤销
```

Subagent 必须拥有互不重叠的写入范围，并返回产物路径与验证结果。主控负责整合，
不能把自然语言汇报视为完成。审核者冷启动，只读取题面、规格、代码、结果和检查。

一次失败、一次 REJECT、一个阶段完成或一个 subagent 完成都不是停止条件。仅在以下
情况停止：S6 完成；需要用户提供不可替代的输入/授权；同一阻断连续三轮无法解决；
下一步属于未授权的高风险外部操作。

