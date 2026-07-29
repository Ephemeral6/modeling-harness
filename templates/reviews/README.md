# 独立审核产物

审核者只输出 JSON：

```json
{
  "reviewer": "numerics-auditor",
  "verdict": "APPROVE",
  "scope": ["..."],
  "findings": [],
  "required_fixes": [],
  "evidence_checked": ["node.id"]
}
```

`APPROVE` 表示在声明的 scope 内未发现阻断问题，不表示模型绝对正确。

