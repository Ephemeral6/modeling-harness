# Failure Model and Recovery Contract

The harness fails closed. Missing, malformed, stale, or ambiguous state never counts as approval.

| Failure | Detection | Recovery |
|---|---|---|
| Interrupted JSON write | atomic temp file prevents partial replacement | discard orphan temp |
| Existing JSON corrupted | `CorruptStateError` | restore from user backup; never reset silently |
| Concurrent evidence writers | exclusive lock + revision | retry serialized operation |
| Artifact changes after verify | SHA audit | revoke and re-verify |
| Gate signing race | evidence lock + second hash pass | abort gate and rerun |
| Forged/stale stamp | schema/config/chain/evidence/review validation | invalidate from first bad stage |
| Worker process disappears | expired lease | reconcile to pending or failed |
| Duplicate worker plan | idempotency key | return existing task |
| Overlapping write scopes | normalized parent/child conflict | re-plan ownership |
| Intake crashes | staging directory | remove staging; do not update current pointer |
| Duplicate Intake | unique run directory | preserve both runs |

Still intentionally external to the kernel:

- Codex subagent process creation;
- checkpoint semantics of problem-specific numerical programs;
- reviewer identity attestation;
- external data licensing and credentials.

These are represented by contracts and task metadata, but deployment-specific adapters must enforce
them where stronger guarantees are required.
