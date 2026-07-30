# 独立审核产物

V3 审核者只输出 JSON：

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

