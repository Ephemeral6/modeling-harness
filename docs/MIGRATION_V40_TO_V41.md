# 从 4.0 迁移到 4.1

4.1 是向后兼容的交付闭合升级。旧项目不需要一次性补齐新工件；缺少相关输入时，
`state`、`evaluation` 和 `audit_paper` 仍可运行。新 Gate 在对应工件或 S6 交付义务
出现后才生效。

## 新工件

| 工件 | 用途 |
|---|---|
| `problem/source_segmentation.json` | 对源文本逐段覆盖并锁定片段哈希 |
| `problem/requirements.json` | mandatory requirement、期望类型、claim 与闭合状态 |
| `config/claim_bindings.json` | 正式 claim 到权威 JSON 字段、单位、容差和推导式的绑定 |
| `results/claim_values.json` | 由 claims evaluator 生成的字段级数值与漂移结果 |
| `docs/assumptions.json` / `docs/assumptions.md` | 假设、偏差方向、替代分支与物化状态 |
| `results/research_diagnostics.json` | 搜索域、边界、gap、排名与压力情景诊断 |
| `results/opportunity_outcomes.json` | Opportunity 的五类处置与实际改进 |
| `results/scenario_sets.json` | screen / selection / report / stress 四集及用途 |
| `results/delivery_check.json` | 成稿净化、章节、图表和引用违规 |
| `config/scheduling.json` | low / medium / high 的排序映射与非期望值声明 |

`paper/draft.md` 是保留 `[[evidence.id]]` 标记的内部工作稿；`paper/final.md` 是净化后的
交付物。4.1 不再要求成稿携带内部证据标记。

## 四道新增 Gate

- G6 Requirement：mandatory requirement 必须闭合到 fresh、verified evidence；
- G7 Claim：正式数字必须从绑定字段重新求值，源值、推导值、单位与锁定值一致；
- G8 Holdout：随机选择后的头条值来自独立 report set；若复用 selection set，必须明确
  承认选择偏差、取消 unbiased 标记并在成稿限定；
- G9 Delivery：成稿包含内部标记、临时路径、原始哈希或未解析模板时不能交付。

搜索边界、网格欠分辨、未物化假设分支与 optimality gap 是软 Opportunity，不是 Gate。

## 调度字段

4.1 只在既有 value 乘积中增加 `improvement_value` 与 `coverage_value`：

~~~text
value = decision_change_probability × information_gain × falsification_value
        × improvement_value × coverage_value
~~~

两字段可选、必须为正数，缺省均为 1.0。采用乘法是因为“结论错误损失”与“潜在改进量”
量纲不同，不应强行相加；1.0 的中性值也保证旧节点的调度分数与 4.0.1 逐位一致。
`risk_penalty` 继续留在分母。`config/scheduling.json` 的序数只用于排序和打破平局，
不构成期望值估计。

## 旧项目升级

1. 升级包并运行 `modelharness --version`，确认 4.1.0；
2. 运行 `modelharness doctor --project .`、`modelharness state --project .` 与
   `modelharness work next --project .`；运行时会继续兼容旧图和缺失的新工件；
3. 若有原始题面文件，运行 requirements 提取并人工完成 segment → requirement 映射；
4. 为进入论文的正式数字建立 `config/claim_bindings.json`，生成 claim values 后再锁定
   decision；
5. 若存在随机模型选择，补齐四集划分和 report-set tool run provenance；
6. 将原论文迁到 `paper/draft.md`，通过 render 生成 `paper/final.md`，修复 delivery
   check 后再完成 S6；
7. 查看 evaluation 的 `answer_quality`，处置 open opportunities。无法在预算内完成的
   项目应登记 budget-qualified stop、strength downgrade 或 deferred，而不是静默忽略。

新建项目会自动获得上述模板、S6 Profile 必交项验收和新调度配置。现有项目的
`modeling-project.json` 无需手工改 schema。
