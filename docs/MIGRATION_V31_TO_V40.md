# 从 3.1 迁移到 4.0

4.0 对现有项目采用就地、向后兼容迁移。

## 自动迁移

- workflow.sqlite3 首次打开时增加 execution_status、verdict、authority、freshness、
  side_effect_class、idempotent 和 recovery_note 列；
- Evidence schema 2、3、4 均可读取；
- 旧 verified 节点继续有效，下一次 verify 会写入完整 binding；
- 旧 Tool Run 可重新 verify，新运行写 schema 2；
- 原有 Problem Graph、Method Pack、Profile 和 Tool Catalog 不需要重建。

## 新行为

- 空 acceptance 返回 NOT_RUN/UNASSESSED，不再利用 all([]) 变成 PASS；
- 非幂等任务租约失联后进入 RECOVERY_PENDING；
- revoke 的根节点为 revoked，下游为 invalidated；
- work next 返回 state_hash 和 state_capsule；
- 新命令：state、proposal、task recovery-pending、task recover、tool recover。

## 建议

旧项目不必把所有 Evidence 立即补 producer_task_id。新生成的关键结论应登记生产任务；
一旦登记，Harness 会强制 reviewer task 和 worker 均与 producer 不同。
