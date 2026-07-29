PLAYBOOKS = {
    "s0": {
        "objective": "解析题面、数据、决策问题、成功标准和失效出口",
        "workers": [
            ("problem-architect", "形式化目标、约束和交付物",
             ["docs/success_criteria.md"]),
            ("data-scout", "只读审计附件、字段、单位和风险",
             ["docs/data_inventory.md"]),
        ],
        "reviewer": "red-team",
    },
    "s1": {
        "objective": "让结构不同的候选模型竞争并形成正式规格",
        "workers": [
            ("modeler-a", "提出机制/解析候选", ["docs/candidates/cand_a.md"]),
            ("modeler-b", "提出统计/数据驱动候选", ["docs/candidates/cand_b.md"]),
            ("modeler-c", "提出优化/仿真候选", ["docs/candidates/cand_c.md"]),
        ],
        "reviewer": "referee",
    },
    "s2": {
        "objective": "完成数据血缘、可复现清洗与参数估计",
        "workers": [
            ("data-engineer", "实现数据管线", ["src/data_prep.py", "data/processed"]),
            ("estimation-engineer", "实现估计与诊断",
             ["src/estimate.py", "results/parameters.json"]),
        ],
        "reviewer": "data-auditor",
    },
    "s3": {
        "objective": "实现求解器并完成 toy-first 数值验证",
        "workers": [
            ("solver-engineer", "实现正式求解器",
             ["src/solver.py", "results/nominal.json"]),
            ("toy-designer", "构造已知解和独立对拍",
             ["checks/l2_toy.py", "results/toy.json"]),
        ],
        "reviewer": "numerics-auditor",
    },
    "s4": {
        "objective": "完成留出验证、UQ、稳健性、基线和反事实",
        "workers": [
            ("uq-worker", "传播不确定性", ["src/uq.py", "results/uq.json"]),
            ("baseline-worker", "实现强基线", ["src/baselines.py", "results/baselines.json"]),
            ("sensitivity-worker", "搜索翻转边界和失效域",
             ["src/sensitivity.py", "results/sensitivity.json"]),
        ],
        "reviewer": "red-team",
    },
    "s5": {
        "objective": "形成带条件决策并锁定可复现结果",
        "workers": [
            ("decision-analyst", "形成决策建议", ["docs/decision_answer.md"]),
            ("replication-worker", "独立种子复跑", ["results/replication.json"]),
        ],
        "reviewer": "red-team",
    },
    "s6": {
        "objective": "从 verified 证据成文并逐句复核",
        "workers": [
            ("narrative-writer", "完成证据驱动论文", ["paper/draft.md"]),
            ("figure-editor", "从机器结果生成图表", ["paper/figures"]),
        ],
        "reviewer": "paper-verifier",
    },
}
