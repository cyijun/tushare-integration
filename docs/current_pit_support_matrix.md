# 当前表 PIT 支持情况说明

## 1. 文档目的

本文档单独回答一个问题：

> 当前 `tushare-integration -> ClickHouse` 这套实现下，哪些表支持 PIT，原因是什么，哪些表不支持。

这里的“支持 PIT”需要先区分口径，否则结论会失真。

---

## 2. 判定标准

## 2.1 严格 PIT

严格 PIT 指：

- 能按业务时间访问
- 能按可用时间访问
- 能回放历史版本
- 能区分“当时可见版本”和“后续修订版本”

典型要求：

- 有 `available_trade_date`
- 有 `sys_from / sys_to` 或等价版本区间
- 有 `batch_id / ingest_time`
- 不会因为 upsert 丢失旧版本

### 结论

> **当前项目里，没有任何一张表支持“严格 PIT”。**

原因很明确：

- 现有落库以 upsert/latest 为主
- 没有系统版本历史
- 没有统一 `available_trade_date`
- 公告/财务/事件类数据的修订历史没有被保留

---

## 2.2 交易日级弱 PIT

交易日级弱 PIT 指：

- 表有明确业务日期，如 `trade_date` / `week` / `month`
- 数据基本按日期快照组织
- 对天级训练、回测、T+1 策略，可以通过“下一交易日可用”规则使用
- 但**不能**回放历史修订版本

这类表可以在当前阶段用于：

- 天级模型训练
- 天级回测
- 第二天策略生成

但前提是：

- 在 DWD 层补统一 `available_trade_date`
- 不把它误当成“严格 PIT”

---

## 2.3 部分支持 / 需要 DWD 再加工

这类表通常具备以下特点之一：

- 有 `ann_date` / `in_date` / `out_date` / `list_date` / `delist_date`
- 可以推导业务生效边界
- 但当前表本身不保存历史版本
- 或者同一事件后续可能被补发、修订、重述

这类表不能直接说“当前已支持 PIT”，但也不应该简单归为“完全不能做 PIT”。

更准确的说法是：

> **它们具备做交易日级 PIT 的原料，但必须经过 DWD 层规则化后才能稳定使用。**

---

## 3. 总体结论

| 结论层级 | 当前状态 |
|---|---|
| 严格 PIT | 无任何表支持 |
| 交易日级弱 PIT，可直接作为 DWD 输入 | 有一批日频/周频快照表支持 |
| 具备 PIT 原料，但必须在 DWD 再加工 | 有一批财务/事件/成分/名单类表 |
| 当前不支持 PIT | 仍有一批“最新状态/静态字典/缺少生效边界”的表 |

---

## 4. 当前可视为“支持交易日级弱 PIT”的表

这些表的共性是：

- 以 `trade_date`、`week`、`month` 为核心业务时间
- 数据天然是某日/某周/某月的快照
- 对天级系统，可以统一按“`available_trade_date = next_trade_date(business_date)`”使用

## 4.1 股票行情与交易快照类

这些表**支持交易日级弱 PIT**。

- `daily`
- `weekly`
- `monthly`
- `stk_weekly_monthly`
- `adj_factor`
- `daily_basic`
- `stk_limit`
- `suspend_d`
- `hsgt_top10`
- `ggt_top10`
- `ggt_daily`
- `bak_daily`
- `stk_premarket`
- `bak_basic`
- `stk_mins`

原因：

- 都有明确的业务日期
- 主要表达“某个交易日/周期的观测结果”
- 上层只要按业务日期向后推一日可用即可

注意：

- `stk_mins` 不是本系统一期核心表，但它仍属于“按业务时间组织的快照事实表”
- 这些表只支持**交易日级弱 PIT**，不支持历史版本回放

## 4.2 股票资金流向与日频市场行为类

这些表**支持交易日级弱 PIT**。

- `moneyflow`
- `moneyflow_hsgt`
- `moneyflow_dc`
- `moneyflow_ind_dc`
- `moneyflow_ind_ths`
- `moneyflow_mkt_dc`
- `moneyflow_ths`
- `margin`
- `margin_detail`
- `slb_len_mm`
- `slb_len`
- `slb_sec_detail`
- `slb_sec`
- `margin_secs`
- `top_list`
- `top_inst`
- `block_trade`

原因：

- 本质上都是“某日市场行为快照”
- 数据主键或采集方式围绕交易日组织
- 历史日期通常不会反复重述成不同版本

## 4.3 涨停/热榜/筹码/特色日频专题类

这些表**支持交易日级弱 PIT**。

- `dc_hot`
- `hm_detail`
- `kpl_concept_cons`
- `kpl_concept`
- `kpl_list`
- `limit_cpt_list`
- `limit_list_d`
- `limit_list_ths`
- `limit_step`
- `ths_hot`
- `cyq_perf`
- `cyq_chips`
- `stk_factor`
- `stk_factor_pro`
- `ccass_hold`
- `ccass_hold_detail`
- `hk_hold`
- `broker_recommend`

原因：

- 要么是按 `trade_date` 采集
- 要么是按统计周期（如 `month`）形成自然快照
- 对天级训练和策略输入，能够按当期快照使用

说明：

- `broker_recommend` 是按月度快照组织，适合做“月度可见信息”
- `cyq_chips` 虽然采集方式特殊，但它依然是按 `ts_code + trade_date` 落地的历史分布快照

## 4.4 指数日频/周频快照类

这些表**支持交易日级弱 PIT**。

- `index_daily`
- `index_weekly`
- `index_monthly`
- `index_weight`
- `daily_info`
- `index_dailybasic`
- `index_global`
- `sz_daily_info`
- `sw_daily`
- `ths_daily`
- `ci_daily`

原因：

- 都是时间点快照或时间段聚合快照
- 查询语义天然是“某日/某周/某月看到什么”

## 4.5 期货日频/周频快照类

这些表**支持交易日级弱 PIT**。

- `fut_daily`
- `fut_holding`
- `fut_settle`
- `fut_mapping`
- `fut_wsr`
- `fut_weekly_detail`

原因：

- 都有明确业务日期或业务周期
- 是某日/某周的事实快照
- 天级系统使用时只需补可用日规则

---

## 5. 当前“具备 PIT 原料，但必须在 DWD 再加工”的表

这些表的共性是：

- 有业务生效字段或公告字段
- 但当前实现没有保存历史版本
- 或数据后续可能被修订、补发、状态变化

因此：

> **它们不能直接宣称“当前支持 PIT”，但在 DWD 层补 `available_trade_date` 和版本控制后，可以成为 PIT 表。**

## 5.1 证券主数据与状态变更类

- `stock_basic`
- `namechange`
- `hs_const`
- `fut_basic`
- `index_basic`
- `ths_index`

原因：

- 这些表通常具备 `list_date` / `delist_date` / `in_date` / `out_date` / `exp_date`
- 理论上可以判断某日是否有效
- 但当前没有系统版本历史，也没有统一的“某天可见版本”规则

## 5.2 成分股 / 成员关系类

- `index_member`
- `index_member_all`
- `ths_member`
- `concept_detail`

原因：

- 这类表往往包含 `in_date` / `out_date`
- 从业务上看，具备按日期回看成分的基础
- 但当前仍缺统一可用日口径，也缺历史采集版本控制

## 5.3 财务、财务指标、披露计划类

- `balancesheet`
- `cashflow`
- `income`
- `forecast`
- `express`
- `dividend`（已补充 `dwd_stock_dividend` 标准层，按公告可用日和版本窗口固化）
- `fina_audit`
- `fina_indicator`
- `fina_mainbz`
- `disclosure_date`

原因：

- 这些表通常有 `ann_date`、`f_ann_date`、`end_date`
- 理论上可以推导 `available_trade_date = next_trade_date(ann_date)`
- 但财报类、预告类、快报类、分红类存在补发、修订、更新的问题
- 当前只保留最新状态，不保留历史版本

所以：

- **用于严谨回测时，当前不应直接当 PIT 使用**
- **应在 DWD 层做公告可用日规则和版本固化**

## 5.4 公司治理 / 股东 / 事件类

- `stk_managers`
- `stk_rewards`
- `top10_holders`
- `top10_floatholders`
- `pledge_stat`
- `pledge_detail`
- `repurchase`
- `share_float`
- `stk_holdernumber`
- `stk_holdertrade`
- `report_rc`
- `stk_surv`

原因：

- 这类表多数有 `ann_date`、`begin_date`、`end_date`、`float_date` 等事件边界
- 从业务上可以定义“什么时候开始可见”
- 但当前没有历史版本留存，事件状态变化也无法回放

因此：

- 它们属于“可 PIT 化”的候选表
- 但当前不能直接作为 PIT 服务表

---

## 6. 当前“不支持 PIT”的表

这些表的共性是：

- 当前更像静态字典、最新名单或弱结构化参考信息
- 没有稳定的业务生效边界
- 或当前表设计只保存“当前状态”

## 6.1 静态字典 / 分类定义类

- `trade_cal`
- `concept`
- `index_classify`
- `hm_list`

原因：

- `trade_cal` 虽然对系统很重要，但它本质是交易所日历字典，不是“会随版本变化的投资信息快照”
- `concept`、`index_classify`、`hm_list` 更像分类/名录/字典
- 这些表没有“某个历史时点系统实际看到哪个版本”的保存机制

说明：

- `trade_cal` 应作为 PIT 规则依赖表使用
- 不应被表述为“自身支持 PIT 的业务事实表”

## 6.2 当前仅保存最新状态、缺少有效时间边界的表

- `stock_company`

原因：

- 当前是公司基础资料的最新快照
- 没有有效期区间
- 也没有历史版本
- 无法回答“某一天系统看到的公司简介/办公地址/主营业务版本是什么”

---

## 7. 一个容易混淆但必须讲清楚的点

很多表不是“完全不能做 PIT”，而是：

> **当前存储方式还不支持 PIT，但这些表的业务字段已经给了我们做 PIT 的材料。**

例如：

- `balancesheet`
- `fina_indicator`
- `dividend`（已补充 `dwd_stock_dividend` 标准层）
- `repurchase`
- `stk_holdernumber`
- `index_member`
- `ths_member`
- `stock_basic`

这些表之所以现在不能算“已支持 PIT”，不是因为业务上做不到，而是因为当前缺：

- 历史版本
- 可用日规则
- DWD 层标准化

---

## 8. 对上层模块的使用建议

## 8.1 目前可以直接进入 DWD 的表

建议优先把以下表作为 DWD 一期输入：

- 股票日线/周线/月线/复权因子/每日指标
- 指数日线/周线/月线/权重/每日指标
- 期货日线/持仓/结算/仓单/主力映射
- 资金流向、龙虎榜、大宗交易、涨停专题、筹码专题

因为它们最接近“天然快照事实表”。

## 8.2 目前不能直接作为 PIT 服务的表

以下表不建议直接给训练/回测/实盘模块用：

- 财务表
- 财务指标表
- 分红表
- 回购、质押、股东人数、股东增减持
- 公司基础资料
- 成分和名单关系表

这些表必须先过 DWD：

- 统一 `available_trade_date`
- 补系统版本字段
- 固化口径后再开放

---

## 9. 最终结论

### 9.1 如果按严格 PIT 标准

> 当前没有任何表支持严格 PIT。

### 9.2 如果按天级量化系统的一期口径

可以分成三类：

- **可直接作为交易日级弱 PIT 输入**
  - 主要是行情、日频市场快照、资金流、榜单、指数/期货日频事实表
- **具备 PIT 原料，但必须在 DWD 再加工**
  - 主要是财务、事件、名单、成分、主数据状态类表
- **当前不支持 PIT**
  - 主要是静态字典、纯最新状态表、缺少有效时间边界的参考表

### 9.3 最重要的工程结论

> 当前表层最大的 PIT 问题，不是“有没有日期字段”，而是“没有版本历史”。  
> 因此，真正的 PIT 能力仍然必须在 DWD 层建设，而不是直接宣称 ODS 表已经具备 PIT。
