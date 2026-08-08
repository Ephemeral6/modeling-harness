# 论文结构化源示例

本目录是论文的唯一权威源。Agent 只编辑 `paper/src/` 下的分节文件与
`manifest.json`，然后运行 `modelharness paper compile` 生成 `paper/draft.md`；
永远不要直接编辑 `draft.md`。

正文中的结论性数字一律通过类型化占位符引用，禁止手抄数值：

```text
{num:claim_id}       -> 渲染 config/claim_bindings.json 中该 claim 的锁定显示值
{ev:evidence_id}     -> 渲染为 [[evidence_id]] 证据标记
{decision:var_id}    -> 渲染 results/decision_variable_manifest.json 中的决策口径语句
```

新增一节时：新建 `NN_名称.md`，并把它按出现顺序登记进 `manifest.json`
的 `sections` 数组；节的顺序以 manifest 为准，文件名前缀只是命名习惯。
提交前先运行 `modelharness paper compile --check` 做纯 lint。
