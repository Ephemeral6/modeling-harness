# 对话式开题

## 用户操作

1. Clone 仓库并在 Codex Desktop 中打开仓库目录。
2. 在同一条对话消息中上传题面 PDF、Word、Excel、CSV、图片或压缩包。
3. 输入：

   > 使用这个 Harness 完整解决该题。全程不使用 Claude，直接开始。

不需要手动运行 `modelharness new`、创建题目文件夹、复制附件或粘贴长启动提示词。

## Codex 自动执行

仓库根目录的 `AGENTS.md` 会要求 Codex：

- 从对话中取得附件的本地路径和原始提示词；
- 调用 `python -m modelharness.conversation`；
- 在 `projects/<题目名称>/` 创建隔离项目；
- 原样复制附件并计算 SHA-256；
- 生成 `problem/intake_manifest.json`；
- 保存 `problem/user_prompt.md`；
- 自动提取纯文本题面，或登记待解析的 PDF/Word 等附件；
- 写入 `projects/.current.json` 作为当前任务指针；
- 读取项目宪法并立即进入 S0。

项目运行产物位于 `projects/`，该目录默认不进入 Git，因此不会污染 Harness 源码仓库。

## 手工调试命令

通常无需手工执行。需要诊断 Intake 时可运行：

```powershell
python -m modelharness.conversation `
  --title "真实题目测试" `
  --prompt "完整解决，全程不使用 Claude" `
  --file "C:\path\题面.pdf" `
  --file "C:\path\附件.xlsx"
```

返回值包含项目路径、附件数量、manifest 路径和下一步动作。

