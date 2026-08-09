## 问题二批次表

交付批次表逐行与在位解证书绑定核对，建模框架综述见文献[1]。

| 批次 | 偏移 | 批规模 | 哺乳栏 | 育肥栏 | 空怀栏 | 妊娠栏 | 产羔栏 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 56 | 4 | 8 | 9 | 5 | 8 |
| 2 | 28 | 56 | 4 | 8 | 9 | 5 | 8 |
| 3 | 57 | 40 | 3 | 6 | 7 | 4 | 6 |
| 4 | 86 | 40 | 3 | 6 | 7 | 4 | 6 |
| 5 | 114 | 56 | 4 | 8 | 9 | 5 | 8 |
| 6 | 143 | 48 | 4 | 6 | 8 | 4 | 7 |
| 7 | 172 | 56 | 4 | 8 | 9 | 5 | 8 |
| 8 | 200 | 64 | 5 | 10 | 10 | 6 | 9 |

## 搜索前沿

第一层等批规模穷举的最好结果为 {num:Q2.layer1_best} 只/年，作为交付计划的对照下限。

## 问题三报告

报告集 n={num:Q3.report_n} 的头条产出为 {num:Q3.report_headline} 只/年，选型集头条为 {num:Q3.selection_headline} 只/年，出栏配对差为 {num:Q3.pairing_diff} 只，随机模拟方法见文献[2] {ev:EV.q3_report}。

## 附录：复算与登记

复算脚本 R2 的输出为 {num:Q2.annual_output} 只/年，与交付计划一致。

| 指标 | 登记值 |
| --- | --- |
| Q2.annual_output | {num:Q2.annual_output} 只/年 |
| Q2.ewes | {num:Q2.ewes} 只 |
| Q2.peak_pens | {num:Q2.peak_pens} 栏 |
| Q2.layer1_best | {num:Q2.layer1_best} 只/年 |
| Q2.upper_bound | {num:Q2.upper_bound} 只/年 |
| Q2.knapsack_bound | {num:Q2.knapsack_bound} 只/年 |
| Q1.gap_lower | {num:Q1.gap_lower} 栏 |
| Q1.gap_upper | {num:Q1.gap_upper} 栏 |
| Q1.restricted_lower | {num:Q1.restricted_lower} 栏 |
| Q3.report_n | {num:Q3.report_n} 个 |
| Q3.report_headline | {num:Q3.report_headline} 只/年 |
| Q3.selection_headline | {num:Q3.selection_headline} 只/年 |
| Q3.pairing_diff | {num:Q3.pairing_diff} 只 |

## 参考文献

[1] 王某某. 圈养湖羊生产系统建模. 北京: 农业出版社, 2019.

[2] Brown T. Stochastic Simulation for Livestock Systems. New York: Springer, 2021.
