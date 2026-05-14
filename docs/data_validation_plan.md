# Data Validation Plan

This project validates data as a staged quality gate around the PIT data flow:

```text
ODS raw/latest -> DWD tmp -> DWD publish -> DWS tmp -> DWS publish
```

Validation has three modes:

- `strict`: `BLOCKER` rules stop publish.
- `warn_only`: rules run and record failures, but publish continues.
- `skip`: validation is not executed; the bypass is recorded.

The default mode is `warn_only` so validation bugs do not stop daily data processing during rollout.

## Configuration

Validation can be configured in `config.yaml`:

```yaml
quality:
  mode: warn_only
  dwd_mode: warn_only
  dws_mode: warn_only
  table_modes:
    dwd_stock_financial_indicator: skip
  skip_until: "2026-05-13 23:59:59"
  create_result_tables: true
```

CLI overrides are available for one-off operations:

```bash
python main.py dwd sync dwd_stock_eod_price --validation-mode strict
python main.py dwd sync dwd_stock_eod_price --skip-validation
python main.py dws sync dws_stock_factor_wide --validation-mode warn_only
```

## Validation CLI

```bash
python main.py quality list dwd dwd_stock_eod_price
python main.py quality run --layer dwd --table dwd_stock_eod_price --mode warn_only
python main.py quality run --layer dwd --all --mode warn_only
python main.py quality run all --mode warn_only
python main.py quality report <run_id>
```

Validation results are stored in:

- `dq_validation_run`
- `dq_validation_result`
- `dq_validation_metric`
- `dq_issue_sample`

## Business Rules

Market data rules include OHLC consistency, nonnegative volume and amount, positive prices when traded, and PIT availability not before event date.

Daily basic rules include share hierarchy, market-value hierarchy, and nonnegative turnover fields.

Financial rules include placeholder-date rejection, quarter-end report periods, announcement date after report period, and no same-day PIT visibility.

Specialized rules cover positive adjustment factors, nonnegative margin fields, northbound holding bounds, chip percentile monotonicity, and valid security lifecycle dates.

## Publish Behavior

DWD and DWS sync commands build the temporary table first. Validation runs against the temporary table before it is exchanged into production.

In `strict` mode, blocker failures keep the current production table unchanged. In `warn_only` mode, failures are recorded but the table is published. In `skip` mode, validation is bypassed and the bypass is recorded.
