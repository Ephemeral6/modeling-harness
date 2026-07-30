# Modeling Harness 3.0 对话入口

当用户在本仓库上传数学建模题目、数据或参考材料并要求开始时，直接 Intake。不要要求
用户手动创建目录、复制附件、运行命令或重复提示词。

## 自动接题

1. 收集本轮附件绝对路径和用户原始要求。
2. 执行：

   ```powershell
   modelharness intake --title "<标题>" --prompt "<原始要求>" `
     --file "<附件1>" --file "<附件2>"
   ```

3. 读取返回项目的 `AGENTS.md`、`problem/intake_manifest.json`、
   `problem/user_prompt.md`、题面和全部附件。
4. 运行 `modelharness doctor --project <project>`。
5. 运行 `modelharness work next --project <project>`，立即处理最高优先级局部问题。
6. 每次任务、证据、审核或 Gate 后再次运行 `work next`，直到完成。

## Problem Graph

新项目自带初始 Problem Graph。S0 必须检查它是否需要拆成更细的局部问题。若要修订：

1. 生成 `problem/decomposition.json`，不得直接静默改内部状态；
2. 运行 `modelharness plan validate problem/decomposition.json`；
3. 冷启动审核问题边界、依赖、输出合同和失败出口；
4. 运行：

   ```powershell
   modelharness plan apply problem/decomposition.json `
     --reason "<修订理由>" --project <project>
   ```

## 执行原则

- 整题由主控整合，局部问题按 Problem Graph 调度；
- 生成者只登记 candidate，不运行自己的终审；
- 任务必须声明输入、写入范围、机器产物、验收、预算和失败出口；
- 审核必须绑定 task ID、contract hash、evidence IDs 和 artifact hashes；
- 先做能证伪路线的反例、toy、已知解和简单基线；
- 发现错误用 `evidence revise/revoke`，不要只改论文；
- S0–S6 是里程碑，不是固定角色队列；
- 只有全部交付闭合、需要用户新授权，或同一阻断连续三轮时停止。

`projects/.current.json` 只指向最近项目。用户说“继续”或“进展”时恢复该项目并先运行
`doctor` 与 `work next`。
