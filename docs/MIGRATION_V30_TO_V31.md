# V3.0 → V3.1 工具链迁移

已有 3.0 项目第一次进入 `StageService`、`ToolRegistry` 或 `work next` 时，如果整个
`config/tools/` 目录不存在，会一次性复制 3.1 工具目录、自主策略和环境 Profile，并
建立目录锁。

这一迁移只在整个目录不存在时发生。如果目录已经存在但 `catalog.json`、锁文件或
自主策略缺失，系统会 fail closed，避免把人为删除或损坏误判成旧版本。

迁移后运行：

```powershell
modelharness tool doctor --project .
modelharness tool lock --project .
modelharness pack lock --project .
modelharness doctor --project .
```

工具目录和自主策略现在属于 Problem Graph 合同。迁移前创建的活动任务可能因合同哈希
变化而陈旧；再次运行 `modelharness work next`，让调度器淘汰旧合同任务并创建带
tool plan 的新任务。

旧证据不会被删除。只有其 obligation hash 不再匹配当前问题合同时，才不能通过当前
里程碑；应按实际情况重跑、验证或登记有理由的 skip，而不是手工改哈希。
