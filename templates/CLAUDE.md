# Claude Code 会话入口

本项目由 Modeling Harness 4.1 管理，可运行在 Codex 或 Claude Code 底座上。
执行宪法的唯一权威文本是同目录的 `AGENTS.md`：开始任何工作前先完整读取并遵守它。
本文件只补充 Claude Code 特有的操作方式，不新增或放宽任何约束。

## Claude Code 适配

- 会话开始即读取 `AGENTS.md`，其中的 Freedom Envelope、权威状态、五条硬不变量、
  建模工作规则和工作循环对两个引擎完全一致。
- 需要独立冷启动审核时，用 Task/Agent 工具 spawn 无状态 subagent，只提供正式输入
  与产物路径；生成者与审核者必须是不同 task 与不同 worker（不变量 5）。
- 长计算写成可断点续跑的 `.py` 脚本后台执行并轮询，不在前台等待。
- 所有脚本必须是 `.py`；路径用 `pathlib.Path`；`subprocess.run` 必须带
  `encoding="utf-8"` 和 `capture_output=True`；不使用 Linux 特有命令。

## 工作循环（与 AGENTS.md 相同）

~~~powershell
modelharness doctor --project .
modelharness state --project .
modelharness work next --project .
~~~
