# V2 → V3 迁移

## 原地继续旧项目

旧项目不必迁移。若不存在 `.harness/problem_graph.json`，Autopilot 自动使用
`legacy_v2` 调度器，原印章和证据仍然有效。

## 启用 Problem Graph

```powershell
modelharness plan init --project <project>
modelharness plan show --project <project>
modelharness pack audit --project <project>
modelharness profile show --project <project>
```

初始问题图映射原 S0–S6 要求。建议在 S0 审核后把整题拆成更细的局部节点，再通过：

```powershell
modelharness plan validate problem/decomposition.json
modelharness plan apply problem/decomposition.json `
  --reason "S0 完成局部问题拆解" --project <project>
```

## Evidence 合同

V3 项目中，若 evidence ID 已被 Problem Graph 声明，`evidence add` 会自动绑定：

- 当前节点 contract hash；
- 节点要求的审核文件。

修订同一结论使用：

```powershell
modelharness evidence revise result.example `
  --reason "审核发现边界处理错误" --project <project>
```

不再通过删除节点并重新使用新 ID 隐藏返工历史。

## 审核格式

V3 审核必须提供 `reviewer`、`task_id`、`contract_hash`、
`evidence_checked` 和 `artifact_hashes`。旧项目的最小 APPROVE/REJECT JSON
只在未绑定问题合同的 V2 证据上兼容。

