# Modeling Harness 4.0 评测实验室

评测分三层，不以论文流畅度替代建模正确性。运行时 Harness 不依赖本目录；隐藏真值
只在离线评分端出现。

## L1 局部能力

每条任务对应一个 Problem Graph 节点，并提供隐藏或机械真值：

- 数据语义与清洗；
- 参数估计与可辨识性；
- 预测与留出验证；
- 动力系统和仿真；
- 优化与组合搜索；
- UQ、敏感性和反事实；
- 条件式决策。

主指标：正确闭合率、数值误差、覆盖率、首次验证率、REJECT 后修复率和计算成本。

## L2 组合建模

把 3–8 个局部节点组成有真实依赖的中型问题，检查接口、单位、数据版本、级联撤销、
不确定性传播和局部结论组合。

## L3 端到端

分别建设 CUMCM、MCM/ICM 和 Real World 题集。每类包含正常题、不可辨识题、数据
不自洽题、优势不显著题、无唯一最优题和计算预算不足题。

## 可运行评分

项目通用评分：

~~~powershell
python -m modelharness.evaluation --project <project>
~~~

隐藏 rubric 评分：

~~~powershell
python -m modelharness.benchmarking benchmarks/fixtures/l1_numeric.json --project <completed-project> --json report.json
~~~

Rubric 支持 artifact_exists、evidence_status、json_numeric、integrity 和
task_status_absent。任务可扩展 rubric，但不应把隐藏目标复制进 Agent 项目。

## Episode Package

每次基准运行结束后归档题目、配置、环境、任务、事件、工具计划/决策/运行、证据、
审核、结果、论文和机械评分：

~~~powershell
python -m modelharness.episode --project <project> --out <episodes/run-id> --benchmark-id <id> --model <model> --seed <seed>
~~~

modelharness.benchmarking.pass_all_k 可从重复运行报告计算“k 次全部通过”的经验概率。
固定模型、Profile、题目和环境后比较 Harness 版本，才能区分模型升级与系统改进。

fixtures/ 只提供格式和回归样例，不冒充完整的 40–60 个 L1、15–20 个 L2 和三类 L3
正式题库。正式题库应逐步由真实竞赛题、公开论文任务和经过授权的真实项目构成。
