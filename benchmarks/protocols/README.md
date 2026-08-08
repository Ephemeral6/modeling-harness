# 对照实验协议（Comparison Protocol）

本目录存放对照实验清单。清单是**机器可读的实验纪律**：谁跟谁比、比哪几题、
基线代码的哈希是什么、判卷者是谁、判卷参数何时冻结、报告落在哪里。
`modelharness comparison audit` 按清单收口，报告没出来就不给绿灯。

治的病：**跑完没人判卷**。三条臂跑到 s6、图也画了、论文也写了，然后没有任何人
把三条臂放在一起打分——于是"我们比 X 强"这句话既无报告也无判卷者，却已经开始
在文档里流传。协议把这一步从"人的自觉"变成"审计项"。

## 目录内容

| 文件 | 用途 |
| --- | --- |
| `TEMPLATE_comparison.json` | 新协议的骨架，复制后逐字段填写 |
| `2026-blind-codex-vs-harness.json` | 2026-08-02 那次三题盲测的**事后如实登记**（当前 audit 不绿，见下） |
| `baselines/*.json` | 基线代码登记表（`baseline_id` + `files[].path/sha256`），由出题人在开跑前落盘 |

## 协议流程

1. **出题人登记基线哈希。** 对照臂（外部系统）的产物代码逐文件算 sha256，写进
   `baselines/<baseline_id>.json`。这一步必须发生在 harness 臂开跑之前，之后清单里
   的 `baseline_artifacts` 只允许**原样引用**登记表条目，不允许重算——重算等于
   给了事后对齐哈希的机会。
2. **各臂隔离跑。** 每条臂只看题面与数据，不读其他臂的目录（清单 `isolation` 字段
   写死这条纪律）。harness 臂跑完后必须把 lifecycle 状态收口成
   `completed` / `delivered` / `abandoned` 之一，并在 `status_history` 里留下时刻——
   审计正是靠这个时刻判断 judge 是否**先**冻结。
3. **judge 先冻结参数。** 判卷脚本（`judge.command`，`.py`）和它的参数文件在任何一条
   臂完成之前定稿，记下 `frozen_params_sha256` 与 `frozen_at`。judge 的 `worker`
   不得是任何一条臂的 `producer`（AGENTS.md 硬不变量 5：生成者不能批准自己的结论）。
4. **出报告。** 判卷结果写进 `report_path`，至少包含 `arms`（数组或以 `arm_id` 为键的
   对象），每条臂给出 `objective_value` 与 `verdict`。少一条臂、少一个字段都算没判完。
5. **`comparison audit` 收口。**

   ~~~powershell
   python -m modelharness comparison audit --manifest benchmarks/protocols/<id>.json
   ~~~

   非空错误列表 → 退出码 1。

## 五查

| 查 | 判什么 | 典型报错前缀 |
| --- | --- | --- |
| a | 每条 `kind="harness"` 臂的项目存在且状态已终结 | `arm <id>: 项目状态为 active` |
| b | `report_path` 存在，且覆盖全部 `arm_id`、每臂含 `objective_value` 与 `verdict` | `comparison report missing/incomplete` |
| c | 每条 harness 臂逐字节扫 `baseline_artifacts`；命中且该臂 `problem/import_manifest.json` 无对应登记 → 泄漏 | `baseline leak: arm <id>` |
| d | `judge.frozen_at` 早于每条 harness 臂的完成时刻（取 `status_history` 里最早的 completed/delivered） | `judge freeze missing / too late / unverifiable (warning)` |
| e | `judge.worker` 不等于任一臂的 `producer`（臂无 `producer` 字段时跳过） | `judge independence violated` |

d 的保守约定：臂缺 `status_history` 时**不**默认判 judge 冻结及时，而是输出
`judge freeze unverifiable (warning)` 说明先后不可判。审计宁可判"不可判"，也不
替历史补一个乐观假设。

## 路径基准

清单里的相对路径以**协议根**为基准：清单位于 `<repo>/benchmarks/protocols/` 时协议根
就是仓库根；清单在别处（例如回归 fixture 内）时协议根是清单所在目录。绝对路径原样使用
——仓库外的外部盲测产物就该用绝对路径登记，并在 `note` 里写明它在仓库外。

## 纪律条款

> **未走协议的对照结果不得写进任何交付物。**

具体地：只要某次对照没有一份 `comparison audit` 通过的清单，它的相对优劣结论就不得
出现在论文、README、汇报材料、提交说明或对外沟通中——包括"更强""更快""比 X 高 n%"
这类措辞。允许写的只有中性事实陈述（"两套系统都跑过 2023D"），且必须同时注明
"未按对照协议判卷"。已经流传出去的未判卷结论，处置方式是撤回或补判，不是补一句免责声明。

## opt-in 说明

对照协议是**可选加载**的机制，不改变任何单题 run 的默认行为：

- 不建清单就没有协议，`s0`–`s6` 流程与 `modelharness doctor` 一律不受影响；
  本目录不参与运行时，Harness 不依赖它。
- 只有显式执行 `comparison audit --manifest <path>` 才会触发五查；
  它不会被自动挂进某个阶段的 checks。
- 想让某次对照被机器承认，就把清单放进本目录并让 audit 转绿；不想被承认的探索性
  对跑，可以不写清单——代价是它的结论永远不能进交付物（见上条纪律）。

## 当前状态：`2026-blind-codex-vs-harness.json` 不绿

这份清单**故意**保持红色，因为它登记的是既成事实：

- 三条 harness 臂（2023D / 2024C / 2025A，2026-08-02 17:42–17:44 连开）都盖到了 s6 印章，
  但 `status` 至今是 `active`，从未收口 → 触发 a；
- `benchmarks/reports/2026-blind-codex-vs-harness/report.json` 不存在 → 触发 b；
- `judge` 四个字段全为 `null`：没有判卷者，也没有冻结过判卷参数 → 触发 d；
- 三条臂都没有 `status_history`，冻结先后不可判 → 触发 d 的 warning 分支。

c 与 e 目前没有报错：逐字节扫描三条臂未发现与 `baselines/` 登记表同哈希的文件
（即没有 codex 代码被复制进 harness 臂），而各臂 `producer` 当年未登记、judge 也不存在，
独立性检查按约定跳过。**在这份清单转绿之前，那次盲测的任何对比结论都不得作为交付物内容。**
