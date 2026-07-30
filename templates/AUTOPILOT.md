# 项目内 Autopilot

每个工作单元后运行：

```powershell
modelharness work next --project .
```

主控按关键前沿推进：

```text
问题合同 → 候选 → 最小区分实验 → 实现 → 冷审 → verified → 整合
```

当 `phase=work` 时领取局部研究任务；`phase=review` 时创建冷启动审核；
`phase=repair` 时按 findings 修订同一 evidence ID；`phase=gate` 时签发当前里程碑并
立即继续；`phase=blocked` 时检查缺失输入、依赖环、写入冲突或连续失败。

不得在 S0–S5、单个任务完成、一次 REJECT 或一次测试失败时停止。

