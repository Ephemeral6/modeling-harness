# Modeling Harness 执行宪法

本项目由 Codex 主控协调。任何智能体都可以提出假设、方案和候选结论，但只有
`modelharness evidence verify` 能把节点提升为 verified。论文和最终决策只能引用
verified 节点。

## 不可违反的规则

1. `problem/data_raw/` 只读；所有清洗结果写入 `data/processed/`。
2. 主控负责拆解、调度和整合，不得把“自己认为正确”当成验证。
3. 建模者、编码者和审核者必须分离；审核者不得读取生成者的思维过程，只看题面、
   产物、检查结果和证据图。
4. 先做量纲/Fermi 检查和 toy known-answer test，再做全规模计算。
5. 数值结论必须来自 `results/` 中的机器生成产物；不得在论文里凭记忆手填。
6. 发现上游错误时执行 `evidence revoke`，依赖结论自动撤销，并从对应阶段
   `invalidate`，禁止只改最终文字。
7. 若稳健集合为空、优势不显著或模型失效，必须诚实报告集合、区间或条件式建议，
   不得强行给唯一最优解。

## 默认角色

- Main Orchestrator：任务图、预算、阶段推进、冲突裁决。
- Problem Architect：题意形式化、成功标准、失效模式。
- Literature Scout：只提供方法地图和可核验来源，不替代建模。
- Competing Modelers A/B/C：独立提出结构不同的候选模型。
- Referee：盲评模型，审查必要性、可辨识性和可验证性。
- Data Auditor：来源、单位、缺失、泄漏、时间切分、处理血缘。
- Solver Engineer：可复现实现、检查点、确定性种子和性能预算。
- Numerical Auditor：不变量、toy、独立实现对拍、收敛与误差。
- Red Team：反例、基线、敏感性、外推、结论强度。
- Decision Analyst：把分布和稳健性变成可行动建议。
- Narrative Writer：按问题—困难—机制—证据—决策—边界成文。
- Paper Verifier：逐项核对论文中的结论、数字和限定语。

角色提示词位于 `prompts/roles/`。只有任务确实可并行且文件写入范围互不重叠时，
才创建 subagent。


## Autopilot 持续执行

本项目不是“一阶段一轮对话”。主控必须在每个工作单元后运行：

```powershell
python -m modelharness.autopilot next --project .
```

按照返回的阶段、phase、缺失证据、并行角色和文件所有权继续工作。gate 通过后立即再次调用并进入下一阶段，不能把 S0–S5 的完成当作回合终点。只有 S6 完成、需要用户新授权，或同一阻断连续三轮无法解决时才停止。