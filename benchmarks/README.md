# Modeling Harness 4.2 评测实验室

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

Rubric 支持 artifact_exists、evidence_status、json_numeric、evaluation_value、integrity 和
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

## 4.1 消融设计（登记，暂不执行）

固定模型、题包、工具权限、计算预算与截止条件，每个配置至少运行 5 个独立种子：

1. V4.0.1 baseline；
2. + M1 sanitizer；
3. + M2 源覆盖 + M5 requirement；
4. + M4 claim binding；
5. + M3 boundary + M6 improvement；
6. 完整 4.1。

各维度单独记录，不做乘积：Soundness（错误断言、不可行方案、数值漂移）；
Completeness（mandatory coverage、直接回答率）；Objective quality（统一模拟器下相对
best-known bound 的 regret）；Search quality（边界触发修复、跨 regime 发现）；
Statistical validity（holdout optimism、区间覆盖、pass^k）；Delivery quality（内部
标记、格式、图表与引用缺陷）；Cost（token、时间、工具调用、用户介入）。

回归 fixture 提供低成本信号，端到端链只在完整配置验证。不同方案的目标值比较必须使用
先冻结参数的中立复核器，避免用第三套假设直接裁判两套不可通约的模拟器。

## 变异注入战（Mutation Battery）

`fixtures/regression/mutation_battery/` 把 2023D 项目第 11 轮红队的 21 条变异注入
测试常驻化（并补充 2 条引用/单位类条目，共 23 条）。`project/` 是最小完整项目骨架：
claim bindings + predictions 锁定值、Paper IR 结构化源（头条数字一律走 `{num:}`
占位符）、编译出的 `paper/draft.md` 与 `paper/final.md`、数值工件、情景四集、
证据图与升级后的回归语料 `docs/regression_corpus.json`。`mutations.json` 逐条登记
变异：`id`、`class`、`target`、`operator`（numeric_swap / sign_flip / caliber_swap /
claim_rollback / disclosure_inversion / unit_swap / reference_unmark）、`params` 与
`expected_detectors`。

`tests/regression/test_mutation_battery.py` 对每条变异执行 copytree →
`modelharness.mutation_core.apply_mutation` → `modelharness.mutation_core.detect`，
逐条断言 `expected_detectors` 至少命中其一，并汇总断言总检出率 ≥ 90%、
`class == "headline_tamper"`（篡改交付数字类，对应 v11 的 M06/M09/M10/M11）
检出率 == 100%。基线（未注入）必须 0 发现，否则检出率没有意义。

目录冻结规则（防 Goodhart）：

1. 检出率只对 `mutations.json` 已收录条目计算；不得为拉高比率删除或弱化已收录
   条目，也不得移除某条变异仍然有效的 `expected_detectors`。
2. 新发现的逃逸案例必须先作为新条目入目录（含变异算子与期望检测器），再重新
   计数；未入目录前的任何检出率数字一律无效。
3. 某条变异在现有检测器组合下确实测不出时，允许给该条加 `known_gap: "<原因>"`
   并从分母剔除，但 known_gap 条数 ≤ 2、必须逐条写明原因，测试会强制该上限；
   不允许静默降目标。
