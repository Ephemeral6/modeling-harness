# 对话式开题

本仓库同时支持两个 Agent 底座：**Codex**（读 `AGENTS.md`）与 **Claude Code**
（读 `CLAUDE.md`，其内容引导回 `AGENTS.md`）。契约与流程对两个引擎完全一致。

## 用户操作

1. Clone 仓库并在 Codex Desktop 或 Claude Code 中打开。
2. 在同一条消息上传题面、数据、图片和参考材料。
3. 输入：

   > 使用 Modeling Harness 完整解决该题，允许 Agent 自主选择本地计算工具，直接开始。

不需要手工建目录、复制附件、安装所有可选工具或粘贴启动提示词。

## Agent 自动执行（Codex / Claude Code 通用）

根目录会话契约（`AGENTS.md`；Claude Code 经 `CLAUDE.md` 进入）要求 Agent：

- 执行 Intake，创建隔离项目并锁定原始附件哈希；
- 选择交付 Profile，生成初始 Problem Graph；
- 快照方法包、工具目录、自主策略和计算环境；
- 运行 `modelharness doctor` 与 `modelharness tool doctor`；
- 按关键局部问题前沿持续推进；
- 对每个计算节点自主选择 use 或 skip 并记录理由；
- use 时记录工具版本、命令、种子、输入输出哈希、日志和验证；
- 持续运行 `work next`，直到完整交付。

默认自主权限覆盖项目内本地计算，不覆盖联网、安装、商业许可证和项目外写入。

## 手工调试

```powershell
modelharness intake `
  --title "真实题目测试" `
  --prompt "完整解决，Agent 自主选择本地计算工具" `
  --file "C:\path\题面.pdf" `
  --file "C:\path\附件.xlsx" `
  --engine auto   # 也可显式指定 codex 或 claude-code

modelharness profile use cumcm
modelharness doctor
modelharness tool doctor
modelharness plan show
modelharness work next
```

工具决策示例：

```powershell
modelharness tool recommend s3.solver_validation
modelharness tool decide s3.solver_validation `
  --action auto --reason "需要正式求解、toy 和残差检查"
```
