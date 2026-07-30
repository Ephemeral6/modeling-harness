# 项目内 Toolchain Autopilot

每个工作单元后运行：

```powershell
modelharness work next --project .
```

主控按关键前沿推进：

```text
问题合同 → 工具推荐 → Agent use/skip → 候选 → 最小区分实验
         → 可复现运行 → 冷审 → verified → 整合
```

计算任务先运行 `tool recommend`，再由 Agent 根据局部价值执行 `tool decide`。use 后通过
`tool run` 生成 verified manifest；skip 也必须说明解析替代、证据充分性或预算理由。

当 `phase=work` 时领取局部任务；`phase=review` 时冷启动审核 artifact 和 tool run；
`phase=repair` 时修订同一 evidence ID 并重新运行失效计算；`phase=gate` 时签发里程碑并
立即继续；`phase=blocked` 时检查输入、能力缺失、版本冲突、写入冲突或连续失败。

不得在 S0–S5、单个任务完成、一次 REJECT 或一次工具失败时停止。优先使用安全降级，
但不得把降级结果描述成原工具能提供的更强结论。
