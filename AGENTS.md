# Modeling Harness 对话式入口

当用户在本仓库的 Codex 对话中上传真实数学建模题目、附件并要求开始时，直接进入
Intake 模式。不要要求用户手动创建目录、复制文件、运行命令或重复粘贴提示词。

## 自动接题协议

1. 识别本轮对话提供的全部附件本地路径，以及用户自然语言要求。
2. 标题不明确时从题面文件名生成简短标题，不因命名问题阻塞。
3. 执行：

   ```powershell
   python -m modelharness.conversation --title "<标题>" `
     --prompt "<用户原始要求>" `
     --file "<附件1绝对路径>" --file "<附件2绝对路径>"
   ```

4. 读取返回的项目目录，然后读取其中的 `AGENTS.md`、
   `problem/intake_manifest.json`、`problem/user_prompt.md` 和全部附件。
5. 自动识别题面、数据、图片、参考材料；原始附件保持只读。
6. 立即开始 S0，不停在初始化或计划层面。
7. 补充附件纳入当前项目，并评估是否需要撤销证据及失效下游阶段。

`projects/.current.json` 指向最近一次 Intake 项目。用户说“继续”或“汇报进展”
时优先恢复该项目。

用户只需要上传文件并说：

> 使用这个 Harness 完整解决该题。全程不使用 Claude，直接开始。

## 自治推进

Intake 后读取 `AUTOPILOT.md`，并立即运行：

```powershell
python -m modelharness.autopilot next
```

每次完成证据、审核或 gate 后再次运行。不得在 S0 或任一中间阶段结束任务；持续推进到 S6，除非满足 AUTOPILOT.md 中的明确停止条件。按返回的 parallel_agent_plan 创建文件写入范围互不重叠的 subagent，并让审核者冷启动。