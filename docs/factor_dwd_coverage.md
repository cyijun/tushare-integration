# Factor DWD Coverage

Source workbook: `/data/flc/code/quant/factor_mapping_readable.xlsx`

The workbook contains 40 factor expressions. Expressions reference these QLib-style fields:

| Expression field | Factor-ready DWD field | Upstream DWD source field |
| --- | --- | --- |
| `$open` | `dwd_stock_factor_bar.open` | `dwd_stock_eod_price.open` |
| `$high` | `dwd_stock_factor_bar.high` | `dwd_stock_eod_price.high` |
| `$low` | `dwd_stock_factor_bar.low` | `dwd_stock_eod_price.low` |
| `$close` | `dwd_stock_factor_bar.close` | `dwd_stock_eod_price.close` |
| `$volume` | `dwd_stock_factor_bar.volume` | `dwd_stock_eod_price.vol` |
| `$turnover` | `dwd_stock_factor_bar.turnover` | `dwd_stock_daily_basic.turnover_rate` |

The original 11 DWD tables contain the required upstream data, but not as one factor-ready table with the expression field names. `dwd_stock_factor_bar` fills that gap by joining `dwd_stock_eod_price` and `dwd_stock_daily_basic`.

| Factor ID | Name | Required expression fields | Essential DWD table fields |
| --- | --- | --- | --- |
| `ac_amt_impact_decay` | 成交额冲击衰减 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `ac_cov_px_vol_short` | 短窗量价协方差 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `ac_hl_amp_vol_link` | 振幅与成交量联动 | `$high`, `$low`, `$volume` | `dwd_stock_factor_bar.high`, `dwd_stock_factor_bar.low`, `dwd_stock_factor_bar.volume` |
| `ac_hl_range_position_delta` | 高低区间位置变化 | `$close`, `$high`, `$low` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.high`, `dwd_stock_factor_bar.low` |
| `ac_mom_vol_mix` | 动量波动联合 | `$close` | `dwd_stock_factor_bar.close` |
| `ac_oc_spread_norm` | 开收盘价差标准化 | `$close`, `$open` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.open` |
| `ac_rankcorr_px_vol` | 价量秩相关短窗 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `ac_ret_skew_roll` | 滚动收益偏度 | `$close` | `dwd_stock_factor_bar.close` |
| `ac_ts_rank_ret_short` | 短期收益时序排名 | `$close` | `dwd_stock_factor_bar.close` |
| `ac_ts_rank_vol_short` | 短期成交量时序排名 | `$volume` | `dwd_stock_factor_bar.volume` |
| `ac_vol_cluster_ratio` | 波动聚集短中窗比 | `$close` | `dwd_stock_factor_bar.close` |
| `ac_vwap_bias` | VWAP偏离 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `cb_indneu_mom_20` | 行业中性短动量20 | `$close` | `dwd_stock_factor_bar.close` |
| `cb_indneu_vol_20` | 行业中性波动20 | `$close` | `dwd_stock_factor_bar.close` |
| `cb_turn_shock_recover_5_20` | 换手冲击恢复5_20 | `$turnover` | `dwd_stock_factor_bar.turnover` |
| `cb_vol_price_asym` | 缩量涨放量跌不对称 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `qb_amp_mean_10` | 10日振幅均值 | `$high`, `$low` | `dwd_stock_factor_bar.high`, `dwd_stock_factor_bar.low` |
| `qb_amp_mean_20` | 20日振幅均值 | `$high`, `$low` | `dwd_stock_factor_bar.high`, `dwd_stock_factor_bar.low` |
| `qb_amt_trend_20` | 20日成交额趋势 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `qb_boll_pos_20` | 布林带位置20日 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_corr_px_vol_20` | 20日价量相关 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `qb_dist_high_20` | 相对20日最高价偏离 | `$close`, `$high` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.high` |
| `qb_dist_low_20` | 相对20日最低价偏离 | `$close`, `$low` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.low` |
| `qb_div_px_vol_20` | 20日量价背离 | `$close`, `$volume` | `dwd_stock_factor_bar.close`, `dwd_stock_factor_bar.volume` |
| `qb_ma_bias_20` | 相对20日均线偏离 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_ma_bias_60` | 相对60日均线偏离 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_macd_hist` | MACD柱值 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_mom_10` | 10日收益率动量 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_mom_20` | 20日收益率动量 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_mom_5` | 5日收益率动量 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_mom_60` | 60日收益率动量 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_rev_20` | 20日反转 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_rev_5` | 5日反转 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_rsi_14` | RSI14 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_turn_mean_20` | 20日换手率均值 | `$turnover` | `dwd_stock_factor_bar.turnover` |
| `qb_turn_mean_5` | 5日换手率均值 | `$turnover` | `dwd_stock_factor_bar.turnover` |
| `qb_turn_std_20` | 20日换手率波动率 | `$turnover` | `dwd_stock_factor_bar.turnover` |
| `qb_vol_ret_20` | 20日收益波动率 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_vol_ret_60` | 60日收益波动率 | `$close` | `dwd_stock_factor_bar.close` |
| `qb_volratio_5_20` | 5日量比相对20日 | `$volume` | `dwd_stock_factor_bar.volume` |
