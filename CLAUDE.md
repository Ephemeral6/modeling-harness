# Modeling Harness 4.4 对话入口（Claude Code）

本仓库同时支持 Codex 与 Claude Code 作为 Agent 底座。会话契约的唯一权威文本是
同目录的 `AGENTS.md`：当用户在本仓库上传数学建模题目、数据或参考材料并要求开始时，
先完整读取 `AGENTS.md` 并按其“自动接题”流程直接执行 Intake，不要要求用户手动建目录、
复制附件或粘贴提示词。

冷启动：若 `modelharness --version` 不可用，先在仓库根目录 `python -m pip install -e .`
（核心零依赖）。用户直接粘贴题面时按 `AGENTS.md`“自动接题”第 1 条落盘后 intake。

Claude Code 特有适配：

- Intake 时可加 `--engine claude-code`（默认 auto 检测）；进入项目后以项目内
  `CLAUDE.md` 为会话入口，它会把你引导回项目 `AGENTS.md` 执行宪法。
- 独立冷启动审核用 Task/Agent 工具 spawn 无状态 subagent；生成者不能批准自己的结论。
- 其余全部规则（权威层、十条硬不变量、工具自主权、工作循环）见 `AGENTS.md`，
  两个引擎完全一致。
