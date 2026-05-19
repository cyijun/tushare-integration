# 数据质量规则报告

生成日期：2026-05-15

## 1. 报告范围

本报告基于当前仓库中的质量校验实现生成，主要覆盖 `tushare_integration/quality.py`、质量 CLI、DWD/DWS 发布钩子和现有测试用例。报告描述的是规则体系和执行机制，不代表某次数据库运行的实际校验结果。

当前质量校验服务于以下 PIT 数据发布链路：

```text
ODS raw/latest -> DWD tmp -> DWD publish -> DWS tmp -> DWS publish
```

DWD 和 DWS 同步命令会先构建临时表，再对临时表执行质量规则；在发布前发现阻断级问题时，可阻止临时表替换生产表。

## 2. 质量模式与阻断策略

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| `strict` | 执行规则，若存在 `BLOCKER` 失败则阻断发布 | 生产发布、关键表上线后 |
| `warn_only` | 执行规则并记录失败，但不阻断发布 | 默认模式、规则灰度期 |
| `skip` | 不执行规则，仅记录跳过行为 | 临时绕过、故障恢复窗口 |

模式优先级为：命令行覆盖参数 > 表级 `table_modes` > 分层模式 `ods_mode`/`dwd_mode`/`dws_mode` > 全局 `mode`。当 `skip_until` 过期后，系统会回退到分层或全局模式；如果回退后仍为 `skip`，则使用 `warn_only`。

严重级别定义为 `BLOCKER`、`WARN`、`MONITOR`。当前规则主要使用 `BLOCKER` 和 `WARN`，其中只有 `strict` 模式下的 `BLOCKER` 失败会阻断发布。

## 3. 分层规则概览

| 数据层 | 基础规则 | 业务规则 | 发布闸口 |
| --- | --- | --- | --- |
| ODS | 非空、必需元数据列存在、元数据字段非空 | 暂无接口级业务约束 | 手工质量 CLI |
| DWD | 非空、PIT 字段、版本窗口、血缘、开放版本唯一性、版本不重叠 | 行情、财务、融资融券、北向持仓、筹码分布、证券主数据等 | `pre_dwd_publish` |
| DWS | 非空 | `dws_stock_factor_wide` 宽表唯一键、OHLC、价格字段、可见日期 | `pre_dws_publish` |

交易相关 DWD 表仅校验 `event_date >= 2010-01-01` 的数据，DWS 宽表仅校验 `trade_date >= 2010-01-01` 的数据。该范围避免旧历史数据中的结构性缺口影响每日发布。

## 4. 通用规则

| 规则 ID | 严重级别 | 适用范围 | 规则含义 |
| --- | --- | --- | --- |
| `row_count_nonzero` | `BLOCKER` | ODS/DWD/DWS | 校验目标表在有效范围内不能为空 |
| `required_columns_exist` | `BLOCKER` | ODS/DWD | 校验质量规则依赖的必需列必须存在 |

ODS 必需元数据列为 `_source`、`_api_name`、`_batch_id`、`_ingest_time`、`_record_hash`；当目标表名以 `_raw` 结尾时，还要求 `_raw_json`。

DWD 必需质量列为 `event_date`、`available_trade_date`、`sys_from`、`sys_to`、`source`、`source_table`、`source_batch_id`、`source_record_hash`。

## 5. ODS 规则

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `ods_metadata_not_empty` | `BLOCKER` | `_source`、`_api_name`、`_batch_id`、`_record_hash` 不能为空字符串 |

ODS 层规则关注采集元数据是否可追溯，当前不校验各接口业务字段取值范围。

## 6. DWD 规则

### 6.1 PIT 与血缘规则

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `dwd_pit_dates_not_null` | `BLOCKER` | `event_date`、`available_trade_date`、`sys_from`、`sys_to` 均不能为空 |
| `dwd_sys_window_order` | `BLOCKER` | PIT 版本窗口必须满足 `sys_from < sys_to` |
| `dwd_lineage_not_empty` | `BLOCKER` | `source`、`source_table`、`source_batch_id`、`source_record_hash` 不能为空 |
| `dwd_single_open_version` | `BLOCKER` | 同一业务键最多只能有一个开放版本 |
| `dwd_no_overlapping_versions` | `BLOCKER` | 同一业务键的版本窗口不能重叠 |

开放版本和窗口重叠规则的业务键来自 DWD schema 的 `business_key`；若未配置，则回退到源 ODS schema 的 `primary_key`。特殊情况下，`dwd_trade_calendar` 使用交易日历业务键，`dwd_security_master` 使用 `instrument_id`。

### 6.2 行情类规则

适用表：`dwd_stock_eod_price`、`dwd_index_eod_price`、`dwd_future_eod_price`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `market_ohlc_consistency` | `BLOCKER` | 有交易或价格活动的行必须满足 OHLC 内部一致性 |
| `market_nonnegative_volume_amount` | `BLOCKER` | 成交量和成交额不能为负 |
| `market_positive_prices_when_traded` | `BLOCKER` | 有成交量时，开高低收和昨收价格必须为正 |
| `market_available_not_before_event` | `BLOCKER` | `available_trade_date` 不能早于 `event_date` |
| `future_settle_positive_when_traded` | `BLOCKER` | 期货有成交量时，结算价、昨结算价必须为正，持仓量不能为负 |

`future_settle_positive_when_traded` 仅适用于 `dwd_future_eod_price`。

### 6.3 股票每日基础指标规则

适用表：`dwd_stock_daily_basic`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `daily_basic_share_hierarchy` | `BLOCKER` | 总股本、流通股本、自由流通股本不能为负，且应满足总股本 >= 流通股本 >= 自由流通股本 |
| `daily_basic_market_value_hierarchy` | `WARN` | 总市值和流通市值不能为负，且总市值应大于等于流通市值 |
| `daily_basic_nonnegative_turnover` | `BLOCKER` | 换手率和量比不能为负 |

### 6.4 行情衍生指标规则

适用表：`dwd_stock_eod_quote_metrics`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `quote_metrics_ohlc_consistency` | `BLOCKER` | 衍生行情 OHLC 字段必须内部一致 |
| `quote_metrics_average_price_range` | `WARN` | 有成交且均价为正时，均价应位于最低价和最高价之间 |
| `quote_metrics_nonnegative_market_fields` | `BLOCKER` | 成交量、成交额、量比、换手率不能为负 |

### 6.5 复权因子规则

适用表：`dwd_stock_adj_factor`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `adj_factor_positive` | `BLOCKER` | 复权因子必须大于 0 |

### 6.6 财务类规则

适用表：`dwd_stock_financial_indicator`、`dwd_stock_income`、`dwd_stock_balance_sheet`、`dwd_stock_cashflow`、`dwd_stock_dividend`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `financial_no_placeholder_dates` | `BLOCKER` | 财务 DWD 行不能使用占位日期 |
| `financial_quarter_end_event_date` | `BLOCKER` | 财报事件日期必须为季度末日期 |
| `financial_announced_after_period` | `BLOCKER` | 公告日期不能早于报告期末 |
| `financial_no_same_day_pit_visibility` | `BLOCKER` | 财务数据可见日期必须晚于公告日期，避免同日 PIT 可见 |
| `balance_sheet_assets_equation` | `WARN` | 资产负债表中总资产应与负债加权益近似勾稽 |
| `cashflow_operating_net_flow` | `WARN` | 经营现金流净额应与经营流入减经营流出近似勾稽 |
| `dividend_nonnegative_values` | `BLOCKER` | 分红送股比例、现金分红和基准股本不能为负 |
| `dividend_action_dates_not_before_announcement` | `BLOCKER` | 分红股权登记日、除权除息日、派息日等执行日期不能早于首次公告日 |

`balance_sheet_assets_equation` 仅适用于资产负债表，`cashflow_operating_net_flow` 仅适用于现金流量表，`dividend_*` 规则仅适用于分红送股表。

### 6.7 融资融券规则

适用表：`dwd_stock_margin_trading`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `margin_nonnegative_fields` | `BLOCKER` | 融资融券余额、买入额、偿还额、余量等字段不能为负 |
| `margin_total_balance_reconciliation` | `WARN` | 融资融券总余额应近似等于融资余额加融券余额 |

### 6.8 北向持仓规则

适用表：`dwd_stock_northbound_holding`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `northbound_holding_bounds` | `BLOCKER` | 持股量不能为负，持股比例必须位于 0 到 100 之间 |
| `northbound_channel_present` | `WARN` | 沪股通/深股通通道字段不应为空 |

### 6.9 筹码分布规则

适用表：`dwd_stock_chip_distribution`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `chip_price_bounds` | `BLOCKER` | 历史最高价不能低于历史最低价 |
| `chip_cost_percentiles_monotonic` | `BLOCKER` | 成本分位数必须单调递增 |
| `chip_winner_rate_bounds` | `BLOCKER` | 获利比例必须位于 0 到 100 之间 |

### 6.10 证券主数据规则

适用表：`dwd_security_master`。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `security_master_lifecycle_dates` | `BLOCKER` | 上市日期不能晚于退市日期 |
| `security_master_instrument_type` | `BLOCKER` | 证券类型必须为 `stock`、`index` 或 `future` |

## 7. DWS 规则

当前 DWS 层对所有 DWS 表执行非空检查；对 `dws_stock_factor_wide` 执行额外宽表规则。

| 规则 ID | 严重级别 | 规则含义 |
| --- | --- | --- |
| `dws_factor_wide_unique_key` | `BLOCKER` | 同一 `instrument_id` 和 `trade_date` 只能有一行 |
| `dws_factor_wide_required_prices` | `BLOCKER` | `open`、`high`、`low`、`close`、`vol` 等核心行情字段不能为空 |
| `dws_factor_wide_ohlc` | `BLOCKER` | 有交易或价格活动的行必须满足 OHLC 内部一致性 |
| `dws_factor_wide_no_future_trade_visibility` | `BLOCKER` | `available_trade_date` 不能早于 `trade_date` |

## 8. 执行与报告命令

常用命令：

```bash
python main.py quality list dwd dwd_stock_eod_price
python main.py quality run --layer dwd --table dwd_stock_eod_price --mode warn_only
python main.py quality run --layer dwd --all --mode warn_only
python main.py quality run all --mode warn_only
python main.py quality report <run_id>
```

DWD/DWS 同步时可使用：

```bash
python main.py dwd sync dwd_stock_eod_price --validation-mode strict
python main.py dwd sync dwd_stock_eod_price --skip-validation
python main.py dws sync dws_stock_factor_wide --validation-mode warn_only
```

当批量执行质量规则时，CLI 会输出 JSON 汇总，包含运行状态、校验行数、失败规则、问题数、问题率，并可通过 `--include-sql/--no-sql` 控制是否输出失败规则 SQL。

## 9. 结果落表

质量校验结果写入以下元数据表：

| 表名 | 用途 |
| --- | --- |
| `dq_validation_run` | 记录每次质量运行的层级、阶段、目标表、模式和状态 |
| `dq_validation_result` | 记录每条规则的状态、问题数、描述和消息 |
| `dq_validation_metric` | 将规则问题数落为指标，便于后续监控 |
| `dq_issue_sample` | 预留失败样本表结构 |

如果 `quality.create_result_tables` 为 `true`，系统会在记录结果前尝试创建这些表。结果落表异常会记录日志，但不会反向影响已经完成的校验流程。

## 10. 测试覆盖

现有测试覆盖了以下关键行为：

- `skip` 模式会记录绕过结果，且不会运行规则。
- `warn_only` 模式会记录失败，但不会抛出阻断异常。
- `strict` 模式遇到 `BLOCKER` 失败会抛出 `QualityValidationError`。
- 表级模式优先于全局模式。
- DWD 行情规则包含 OHLC、一价正数、开放版本唯一性等关键校验。
- DWD/DWS 交易相关规则使用 2010-01-01 之后的数据范围。
- DWD 版本窗口规则使用完整窗口帧，避免 ClickHouse 窗口默认帧导致误判。
- CLI 支持单表、分层全量、全层全量和失败规则 SQL 汇总。

## 11. 已识别改进点

1. `MONITOR` 严重级别和 `dq_issue_sample` 表结构已经预留，但当前规则执行尚未落失败样本，也没有监控级规则。
2. 质量规则 SQL 当前仅支持 ClickHouse；Doris、MySQL、StarRocks 的写入能力存在，但质量校验未实现对应 SQL 方言。
3. ODS 层主要校验元数据完整性，尚未根据各接口 schema 自动生成字段级空值、主键唯一性或枚举范围规则。
4. DWS 层的业务规则集中在 `dws_stock_factor_wide`，其他 DWS 表目前主要依赖非空检查。
5. `dq_validation_metric` 当前只记录问题数，可继续扩展通过率、失败率、检查行数和分位统计等运行指标。
6. 规则描述和 SQL 分散在 Python 实现中，后续可考虑导出规则清单或用 YAML 声明部分可配置规则，降低审计和文档维护成本。

## 12. 结论

当前质量规则体系已经覆盖发布链路中的关键风险：空表发布、DWD PIT 时间窗口错误、血缘丢失、同业务键多开放版本、行情价格异常、财务日期穿越、融资融券和持仓边界异常，以及 DWS 宽表重复键和未来可见问题。

建议生产环境将核心 DWD/DWS 表逐步切换到 `strict`，先从行情、交易日历、证券主数据和 DWS 因子宽表开始；对仍处在规则调优期的表继续使用 `warn_only` 并监控 `dq_validation_result` 和 `dq_validation_metric`。
