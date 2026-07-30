# Modeling Harness 3.1：自主计算工具链

## 目标

工具链层把“Agent 可以临时运行一段代码”升级为：

```text
问题节点
  → 能力需求
  → 环境探测
  → Agent 自主 use / skip 决策
  → 结构化非 shell 运行
  → 输入、输出、版本、种子和日志哈希
  → 机械验证
  → 任务、证据与里程碑审计
```

Harness 本身继续只依赖 Python 标准库。NumPy、CVXPY、OR-Tools、R、MATLAB 等均为
可选计算后端；缺失或不兼容时由 Registry 显式报告并按方法包回退。

## 状态与配置

- `config/tools/catalog.json`：工具、能力、探测、风险、执行器和降级候选；
- `config/tools/catalog.lock.json`：工具目录内容锁；
- `config/tools/environment.lock.json`：创建或显式锁定时的实际环境快照；
- `config/tool_autonomy.json`：Agent 自主边界；
- `config/tool_environments/*.json`：General、CUMCM、MCM/ICM、Real World 环境；
- `.harness/tool_plans/<node>.json`：能力匹配和推荐；
- `.harness/tool_decisions/<node>.json`：Agent 的 use/skip 决策和理由；
- `.harness/tool_runs/<run>.json`：可复现运行与验证 manifest；
- `logs/tool_runs/`：stdout/stderr 哈希日志。

Problem Graph 合同包含方法包、工具目录和自主策略哈希。因此工具定义或权限变化会产生
新合同，不能复用旧运行冒充当前结果。

## Agent 自主权

默认 `mode=agent_choice`。Agent 可以：

- 采纳推荐工具；
- 根据题目额外请求能力；
- 选择其他已安装且允许的工具；
- 判断手算、解析推导或已有证据已足够，登记 `skip`。

无论 use 还是 skip，都必须写出理由。`use` 必须形成当前合同和当前决策哈希绑定的
verified run；只在自然语言里说“运行过”不算完成。

默认可自主执行本地计算。以下行为仍需新授权：

- 联网或调用外部 API；
- 安装、升级或卸载软件；
- 商业求解器和许可证消耗；
- 对项目外系统产生写入；
- 超出 `max_timeout_seconds` 的计算。

用户可以有意识地修改 `config/tool_autonomy.json` 扩大项目授权；修改后必须重新
`tool lock`，旧合同、决策和运行不会被静默沿用。

## 典型命令

```powershell
modelharness tool doctor --profile cumcm --project .
modelharness tool capabilities --project .
modelharness tool recommend s3.solver_validation --project .

modelharness tool decide s3.solver_validation `
  --action auto `
  --reason "该节点需要数值求解和 toy 对拍" `
  --project .
```

长命令使用 JSON 文件，避免 shell 转义和注入：

```json
[
  "python",
  "src/solver.py",
  "--config",
  "config/run.json"
]
```

```powershell
modelharness tool run s3.solver_validation `
  --argv-file config/solver.argv.json `
  --tool numpy --tool scipy `
  --input src/solver.py `
  --input config/run.json `
  --output results/nominal.json `
  --validators-file config/solver.validators.json `
  --seed 2026 --timeout 3600 --project .
```

跳过计算也必须可审计：

```powershell
modelharness tool decide s1.model_formulation `
  --action skip `
  --reason "解析反例已经排除该候选，不需要正式数值计算" `
  --project .
```

## 验证器

运行始终检查退出码、预期输出存在性和输出哈希。额外验证器包括：

- `file_nonempty`；
- `json_finite`；
- `json_fields`；
- `numeric_assertion`；
- `cross_artifact`：解析解、穷举解或独立后端结果对拍；
- `independent_check`：只允许 `checks/` 下的结构化 Python 检查脚本。

推荐的领域验证映射：

| 问题 | 最小机械证据 |
|---|---|
| 连续/凸优化 | primal/dual residual、KKT、解析或小实例 |
| 整数规划 | 穷举 toy、可行性、界、gap、双后端 |
| 微分方程 | 解析极限、网格收敛、守恒误差 |
| 蒙特卡洛 | 已知分布、重复种子、标准误、收敛曲线 |
| 统计/机器学习 | 防泄漏切分、简单基线、留出或滚动验证 |
| 贝叶斯 | 先验预测、真值回收、R-hat、ESS、后验预测 |
| GIS | CRS、单位、几何有效性、空间分块留出 |
| 离散事件仿真 | 逐事件 toy、守恒、预热期和重复种子 |

## 工具选择与降级

工具按能力、任务类型、可用性、风险、优先级和成本排序。方法包可以声明 required 和
preferred capability，但 Agent 保留最终 use/skip 判断。缺失能力不会被伪装为成功：

- 商业 MIP 不可用：优先 OR-Tools、CVXPY/HiGHS，再回退启发式并报告界；
- GIS 后端不可用：保留坐标血缘，使用审计过的简化几何或请求环境；
- PyMC 不可用：使用可验证频率学派区间或自编最小采样器，不伪造后验；
- MATLAB/R/Julia 不可用：优先等价 Python 实现并增加独立对拍；
- 正式规模超时：交付可行解、下界/上界、gap 和计算预算，不宣称最优。

## 环境 Profile

四套环境文件给出 required/optional 工具、版本约束和可复现需求：

- `general`：标准数值、数据、符号和绘图；
- `cumcm`：强化统计、优化、图算法、Excel 和过程复现；
- `mcm_icm`：强化机制、敏感性、决策叙事、Pandoc 和 LaTeX；
- `real_world`：强化治理、监控、可解释基线和外部系统授权边界。

`tool doctor` 的 required 缺失会失败；optional 缺失和兼容性问题会列出但允许通过降级
继续。环境变化后运行 `tool lock` 创建新快照；不要修改旧 run manifest。
