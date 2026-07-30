# Modeling Harness 3.1 对话入口

当用户在本仓库上传数学建模题目、数据或参考材料并要求开始时，直接 Intake。不要要求
用户手动创建目录、复制附件、运行命令或重复提示词。

## 自动接题

1. 收集本轮附件绝对路径和用户原始要求。
2. 执行 `modelharness intake` 创建隔离项目。
3. 读取项目 `AGENTS.md`、intake manifest、题面和全部附件。
4. 根据任务选择 `cumcm`、`mcm_icm`、`real_world` 或 `general` Profile。
5. 运行 `modelharness doctor` 和对应的 `modelharness tool doctor`。
6. 运行 `modelharness work next`，立即处理最高优先级局部问题。
7. 每次任务、工具运行、证据、审核或 Gate 后再次运行 `work next`，直到完成。

## 五个权威层

- Problem Graph：待回答义务、依赖和合同；
- Toolchain：工具能力、Agent 决策和可复现运行；
- Evidence Graph：候选、验证、拒绝和撤销的结论；
- Workflow：任务、租约、预算和机器验收；
- Milestone Stamps：S0–S6 整体闭合投影。

不得用任务完成替代证据验证，不得用工具成功退出替代数学验证，也不得用印章存在替代
印章校验。

## Agent 工具自主权

每个需要计算的方法包都会生成 tool plan。执行 Agent 有权：

- 采纳推荐工具；
- 根据局部问题增加 capability；
- 改选其他已安装且策略允许的工具；
- 判断解析推导、手算或现有证据足够，选择 skip。

use 和 skip 都必须执行 `modelharness tool decide` 并记录理由。use 后必须通过
`modelharness tool run` 形成绑定当前 node contract、decision hash、版本、种子、输入、
输出、日志和验证器的 verified run。

默认自主权只覆盖项目内本地计算。联网、安装或升级软件、商业许可证、项目外写入和超出
预算的长计算需要用户新授权。不得通过普通 shell 绕开工具策略。

## Problem Graph

新项目自带初始 Problem Graph。S0 必须把它按真实子问和局部可证伪问题细化。若要修订：

1. 生成 `problem/decomposition.json`；
2. 运行 `plan validate`；
3. 冷启动审核问题边界、依赖、输出合同、方法包和失败出口；
4. 使用 `plan apply --reason` 应用。

## 执行原则

1. 整题由主控整合，局部问题按关键前沿调度。
2. 生成者只登记 candidate，不运行自己的终审。
3. 先做量纲、Fermi、反例、toy、已知解和简单基线。
4. 计算从 smoke、小规模、验证规模推进到正式规模。
5. 优化必须检查可行性、残差、界/gap；随机计算必须锁种子并报告误差。
6. 任务声明输入、写入范围、工具计划、产物、验收、预算和失败出口。
7. 审核绑定 task ID、contract hash、evidence IDs、artifact hashes 和相关 tool runs。
8. 发现错误用 `evidence revise/revoke`，不要只改论文。
9. S0–S6 是里程碑，不是固定角色队列。
10. 只有全部交付闭合、需要新授权，或同一阻断连续三轮时停止。

`projects/.current.json` 只指向最近项目。用户说“继续”或“进展”时恢复该项目，先运行
`doctor`、`tool doctor` 和 `work next`。
