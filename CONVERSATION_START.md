# 对话式开题

## 用户操作

1. Clone 仓库并在 Codex Desktop 中打开。
2. 在同一条消息上传题面、数据、图片和参考材料。
3. 输入：

   > 使用这个 Harness 完整解决该题。全程不使用 Claude，直接开始。

不需要手工建目录、复制附件或粘贴启动提示词。

## Codex 自动执行

根目录 `AGENTS.md` 要求 Codex：

- 取得附件本地路径和用户原始要求；
- 执行 `modelharness intake`；
- 创建隔离项目并锁定原始附件哈希；
- 生成 Intake manifest、用户要求和初始 Problem Graph；
- 快照方法包和交付 Profile；
- 运行 `modelharness doctor`；
- 运行 `modelharness work next`；
- 按关键局部问题前沿持续推进到完整交付。

项目位于 `projects/`，默认不进入 Git。

## 手工调试

```powershell
modelharness intake `
  --title "真实题目测试" `
  --prompt "完整解决，全程不使用 Claude" `
  --file "C:\path\题面.pdf" `
  --file "C:\path\附件.xlsx"

modelharness plan show
modelharness work next
```

默认 Profile 为 `general`。需要时可在开始研究前选择：

```powershell
modelharness profile use cumcm
modelharness profile use mcm_icm
modelharness profile use real_world
```
