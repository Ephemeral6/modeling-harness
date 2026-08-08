交付计划共 {num:Q2.ewes} 只基础母羊，分八批滚动配种，日峰值恰为 {num:Q2.peak_pens} 栏，年化出栏 {num:Q2.annual_output} 只/年 {ev:EV.plan_replay}。

解析上界为 {num:Q2.upper_bound} 只/年，整数背包上界为 {num:Q2.knapsack_bound} 只/年。整数背包上界只对固定周期与整批构成的计划成立；解析上界则覆盖全部滚动计划。

上界经四条互不复用的路径复核，两两取值一致；其中只有符号积分一路是独立的建模推导。

该计划为手工构造的在位解，没有可核对的搜索记录。本计划是受限搜索下的最好可行解，其最优性作用域仅限已探索的构造类。

栏位缺口区间为 {num:Q1.gap_lower} 至 {num:Q1.gap_upper} 栏，受限类缺口下界为 {num:Q1.restricted_lower} 栏 {ev:EV.gap_witness}。

| 编号 | 假设 | 披露 |
| --- | --- | --- |
| A12 | 育肥期自断奶日起算 | 若自出生起算，上界另行推导 |
