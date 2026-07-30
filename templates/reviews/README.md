# 独立审核产物

审核者只输出 JSON，并写入当前审核任务 `owns` 给出的路径。`review.path` 是逻辑审核名，
不是要求反复覆盖的 canonical 文件。首次审核通常为 `reviews/name.json`；复审自动使用
`reviews/name_v2.json`、`reviews/name_v3.json` 等不可变版本。

旧 APPROVE/REJECT 永远保留。不要覆盖、删除、移动或归档它们，也不要为写入复审请求
额外权限。Harness 会根据 contract_hash 与当前 artifact_hashes 选择最新有效版本。

```json
{
  "reviewer": "numerics-auditor",
  "task_id": "durable-task-id",
  "contract_hash": "problem-node-contract-hash",
  "verdict": "APPROVE",
  "scope": ["solver", "toy", "nominal result"],
  "findings": [],
  "required_fixes": [],
  "evidence_checked": ["code.solver", "experiment.toy", "result.nominal"],
  "artifact_hashes": {
    "src/solver.py": "SHA256",
    "results/toy.json": "SHA256",
    "results/nominal.json": "SHA256"
  }
}
```

`APPROVE` 只表示在声明 scope、当前合同和当前产物哈希下未发现阻断问题，不表示模型
绝对正确。产物变化后旧审核自动陈旧；审核者不得继承生成者的讨论或思维过程。
同一审核版本写出后视为不可变；需要修正审核本身时也创建下一个版本。

