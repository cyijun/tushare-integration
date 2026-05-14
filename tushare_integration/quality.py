from __future__ import annotations

import datetime
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

from tushare_integration.db_engine import DatabaseEngineFactory, DBEngine
from tushare_integration.settings import TushareIntegrationSettings


ValidationMode = Literal["strict", "warn_only", "skip"]
ValidationSeverity = Literal["BLOCKER", "WARN", "MONITOR"]

FAR_FUTURE_TS = "toDateTime64('9999-12-31 00:00:00', 3)"
VALIDATION_SYSTEM_ERROR = "VALIDATION_SYSTEM_ERROR"
TRADE_VALIDATION_MIN_DATE_SQL = "toDate32('2010-01-01')"
ROOT_DIR = Path(__file__).resolve().parent.parent
DWD_SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema" / "dwd"
ODS_SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema"

DWD_TRADE_RELEVANT_TABLES = {
    "dwd_trade_calendar",
    "dwd_stock_eod_price",
    "dwd_index_eod_price",
    "dwd_future_eod_price",
    "dwd_stock_daily_basic",
    "dwd_stock_eod_quote_metrics",
    "dwd_stock_adj_factor",
    "dwd_stock_margin_trading",
    "dwd_stock_northbound_holding",
    "dwd_stock_chip_distribution",
}

DWS_TRADE_DATE_COLUMNS = {
    "dws_stock_factor_wide": "trade_date",
}


@dataclass(frozen=True)
class ValidationRule:
    rule_id: str
    description: str
    severity: ValidationSeverity
    issue_count_sql: str
    sample_sql: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    rule_id: str
    severity: ValidationSeverity
    status: str
    issue_count: int
    description: str
    message: str = ""


@dataclass(frozen=True)
class ValidationRun:
    run_id: str
    layer: str
    stage: str
    table_name: str
    target_table_name: str
    mode: ValidationMode
    status: str
    started_at: datetime.datetime
    finished_at: datetime.datetime
    results: list[ValidationResult]

    @property
    def should_block(self) -> bool:
        return self.mode == "strict" and any(
            result.severity == "BLOCKER" and result.status == "FAIL" for result in self.results
        )


class QualityValidationError(RuntimeError):
    def __init__(self, run: ValidationRun):
        failed_rules = [
            f"{result.rule_id}({result.issue_count})"
            for result in run.results
            if result.severity == "BLOCKER" and result.status == "FAIL"
        ]
        super().__init__(
            f"Validation failed for {run.layer}.{run.table_name} in strict mode: {', '.join(failed_rules)}"
        )
        self.run = run


class QualityManager:
    def __init__(self, settings: TushareIntegrationSettings | None = None, db_engine: DBEngine | None = None):
        self.settings = settings or TushareIntegrationSettings.model_validate(
            yaml.safe_load(open("config.yaml", "r", encoding="utf-8").read())
        )
        self.db_engine = db_engine

    def get_db_engine(self) -> DBEngine:
        if self.db_engine is None:
            self.db_engine = DatabaseEngineFactory.create(self.settings)
        return self.db_engine

    @staticmethod
    def _quote_table(db_name: str, table_name: str) -> str:
        return f"{db_name}.{table_name}"

    @staticmethod
    def _first_int(data: pd.DataFrame, default: int = 0) -> int:
        if data.empty:
            return default
        value = data.iloc[0, 0]
        if pd.isna(value):
            return default
        return int(value)

    def resolve_mode(
        self,
        layer: str,
        table_name: str,
        override_mode: ValidationMode | None = None,
        skip_validation: bool = False,
    ) -> ValidationMode:
        if skip_validation:
            return "skip"
        if override_mode is not None:
            return override_mode

        quality = self.settings.quality
        table_mode = quality.table_modes.get(table_name)
        stage_mode = getattr(quality, f"{layer}_mode", None)
        mode: ValidationMode = table_mode or stage_mode or quality.mode

        if mode == "skip" and quality.skip_until:
            try:
                skip_until = datetime.datetime.fromisoformat(quality.skip_until)
            except ValueError:
                logging.warning("Invalid quality.skip_until value %s; using skip mode", quality.skip_until)
                return mode
            if datetime.datetime.now(skip_until.tzinfo) > skip_until:
                fallback_mode = stage_mode or quality.mode
                return fallback_mode if fallback_mode != "skip" else "warn_only"
        return mode

    def validate_publish(
        self,
        layer: str,
        table_name: str,
        target_table_name: str,
        stage: str,
        mode: ValidationMode | None = None,
        skip_validation: bool = False,
    ) -> ValidationRun:
        resolved_mode = self.resolve_mode(
            layer=layer,
            table_name=table_name,
            override_mode=mode,
            skip_validation=skip_validation,
        )
        started_at = datetime.datetime.now()
        run_id = uuid.uuid4().hex

        if resolved_mode == "skip":
            run = ValidationRun(
                run_id=run_id,
                layer=layer,
                stage=stage,
                table_name=table_name,
                target_table_name=target_table_name,
                mode=resolved_mode,
                status="SKIPPED",
                started_at=started_at,
                finished_at=datetime.datetime.now(),
                results=[],
            )
            self._record_run(run)
            logging.warning("Validation skipped for %s.%s target=%s", layer, table_name, target_table_name)
            return run

        try:
            results = self.run_rules(layer=layer, table_name=table_name, target_table_name=target_table_name)
        except Exception as exc:
            logging.exception("Validation system error for %s.%s target=%s", layer, table_name, target_table_name)
            results = [
                ValidationResult(
                    rule_id=VALIDATION_SYSTEM_ERROR,
                    severity="BLOCKER",
                    status="FAIL",
                    issue_count=1,
                    description="Validation engine raised an internal error",
                    message=repr(exc),
                )
            ]
            if resolved_mode == "warn_only":
                logging.warning("Continuing because validation mode is warn_only")

        status = "PASS"
        if any(result.status == "FAIL" for result in results):
            status = "FAIL"
        run = ValidationRun(
            run_id=run_id,
            layer=layer,
            stage=stage,
            table_name=table_name,
            target_table_name=target_table_name,
            mode=resolved_mode,
            status=status,
            started_at=started_at,
            finished_at=datetime.datetime.now(),
            results=results,
        )
        self._record_run(run)
        if run.should_block:
            raise QualityValidationError(run)
        return run

    def run_rules(self, layer: str, table_name: str, target_table_name: str | None = None) -> list[ValidationResult]:
        target_table_name = target_table_name or table_name
        rules = self.build_rules(layer=layer, table_name=table_name, target_table_name=target_table_name)
        results = []
        db_engine = self.get_db_engine()
        for rule in rules:
            issue_count = self._first_int(db_engine.query_df(rule.issue_count_sql))
            results.append(
                ValidationResult(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    status="FAIL" if issue_count > 0 else "PASS",
                    issue_count=issue_count,
                    description=rule.description,
                )
            )
        return results

    def list_rules(self, layer: str, table_name: str, target_table_name: str | None = None) -> list[ValidationRule]:
        return self.build_rules(layer=layer, table_name=table_name, target_table_name=target_table_name or table_name)

    def checked_count_sql(self, layer: str, table_name: str, target_table_name: str | None = None) -> str:
        target_table_name = target_table_name or table_name
        db_name = self.settings.database.db_name
        qualified = self._quote_table(db_name, target_table_name)
        validation_filter = None
        if layer == "dwd":
            validation_filter = self._dwd_validation_filter(table_name)
        elif layer == "dws":
            validation_filter = self._dws_validation_filter(table_name)
        elif layer != "ods":
            raise ValueError(f"Unsupported validation layer: {layer}")
        return f"""
            SELECT count() AS checked_count
            FROM {qualified}
            {self._where_sql(validation_filter=validation_filter)}
        """

    def checked_count(self, layer: str, table_name: str, target_table_name: str | None = None) -> int:
        return self._first_int(
            self.get_db_engine().query_df(
                self.checked_count_sql(layer=layer, table_name=table_name, target_table_name=target_table_name)
            )
        )

    def build_rules(self, layer: str, table_name: str, target_table_name: str) -> list[ValidationRule]:
        if self.settings.database.db_type != "clickhouse":
            raise NotImplementedError("Quality validation currently supports ClickHouse SQL only")
        if layer == "dwd":
            return self._build_dwd_rules(table_name, target_table_name)
        if layer == "dws":
            return self._build_dws_rules(table_name, target_table_name)
        if layer == "ods":
            return self._build_ods_rules(table_name, target_table_name)
        raise ValueError(f"Unsupported validation layer: {layer}")

    def _build_ods_rules(self, table_name: str, target_table_name: str) -> list[ValidationRule]:
        db_name = self.settings.database.db_name
        qualified = self._quote_table(db_name, target_table_name)
        metadata_columns = ["_source", "_api_name", "_batch_id", "_ingest_time", "_record_hash"]
        if target_table_name.endswith("_raw"):
            metadata_columns.append("_raw_json")
        return [
            self._row_count_rule(qualified),
            self._required_columns_rule(db_name, target_table_name, metadata_columns),
            ValidationRule(
                rule_id="ods_metadata_not_empty",
                description="ODS metadata fields must be populated",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE _source = '' OR _api_name = '' OR _batch_id = '' OR _record_hash = ''
                """,
            ),
        ]

    def _build_dwd_rules(self, table_name: str, target_table_name: str) -> list[ValidationRule]:
        db_name = self.settings.database.db_name
        qualified = self._quote_table(db_name, target_table_name)
        validation_filter = self._dwd_validation_filter(table_name)
        rules = [
            self._row_count_rule(qualified, validation_filter),
            self._required_columns_rule(
                db_name,
                target_table_name,
                [
                    "event_date",
                    "available_trade_date",
                    "sys_from",
                    "sys_to",
                    "source",
                    "source_table",
                    "source_batch_id",
                    "source_record_hash",
                ],
            ),
            ValidationRule(
                rule_id="dwd_pit_dates_not_null",
                description="DWD PIT dates must be populated",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("event_date IS NULL OR available_trade_date IS NULL OR sys_from IS NULL OR sys_to IS NULL", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="dwd_sys_window_order",
                description="DWD version windows must satisfy sys_from < sys_to",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("sys_from >= sys_to", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="dwd_lineage_not_empty",
                description="DWD rows must keep source lineage",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("source = '' OR source_table = '' OR source_batch_id = '' OR source_record_hash = ''", validation_filter)}
                """,
            ),
        ]
        rules.extend(self._dwd_open_version_rules(table_name, qualified, validation_filter))
        rules.extend(self._dwd_business_rules(table_name, qualified, validation_filter))
        return rules

    def _build_dws_rules(self, table_name: str, target_table_name: str) -> list[ValidationRule]:
        db_name = self.settings.database.db_name
        qualified = self._quote_table(db_name, target_table_name)
        validation_filter = self._dws_validation_filter(table_name)
        rules = [self._row_count_rule(qualified, validation_filter)]
        if table_name == "dws_stock_factor_wide":
            rules.extend(
                [
                    ValidationRule(
                        rule_id="dws_factor_wide_unique_key",
                        description="DWS factor wide must have one row per instrument and trade date",
                        severity="BLOCKER",
                        issue_count_sql=f"""
                            SELECT count() AS issue_count
                            FROM (
                                SELECT instrument_id, trade_date
                                FROM {qualified}
                                {self._where_sql(validation_filter=validation_filter)}
                                GROUP BY instrument_id, trade_date
                                HAVING count() > 1
                            )
                        """,
                    ),
                    ValidationRule(
                        rule_id="dws_factor_wide_required_prices",
                        description="DWS factor wide must keep required OHLCV fields",
                        severity="BLOCKER",
                        issue_count_sql=f"""
                            SELECT count() AS issue_count
                            FROM {qualified}
                            {self._where_sql("open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR vol IS NULL", validation_filter)}
                        """,
                    ),
                    ValidationRule(
                        rule_id="dws_factor_wide_ohlc",
                        description="DWS factor wide OHLC fields must be internally consistent",
                        severity="BLOCKER",
                        issue_count_sql=self._ohlc_issue_sql(
                            qualified,
                            validation_filter,
                            "vol > 0 OR open > 0 OR high > 0 OR low > 0",
                        ),
                    ),
                    ValidationRule(
                        rule_id="dws_factor_wide_no_future_trade_visibility",
                        description="DWS factor rows must not be available before their trade date",
                        severity="BLOCKER",
                        issue_count_sql=f"""
                            SELECT count() AS issue_count
                            FROM {qualified}
                            {self._where_sql("available_trade_date < trade_date", validation_filter)}
                        """,
                    ),
                ]
            )
        return rules

    @staticmethod
    def _where_sql(condition: str | None = None, validation_filter: str | None = None) -> str:
        predicates = [predicate for predicate in [validation_filter, condition] if predicate]
        if not predicates:
            return ""
        return "WHERE " + "\n                      AND ".join(f"({predicate})" for predicate in predicates)

    @classmethod
    def _row_count_rule(cls, qualified_table_name: str, validation_filter: str | None = None) -> ValidationRule:
        return ValidationRule(
            rule_id="row_count_nonzero",
            description="Validated table must not be empty",
            severity="BLOCKER",
            issue_count_sql=f"""
                SELECT if(count() = 0, 1, 0) AS issue_count
                FROM {qualified_table_name}
                {cls._where_sql(validation_filter=validation_filter)}
            """,
        )

    @staticmethod
    def _required_columns_rule(db_name: str, table_name: str, columns: list[str]) -> ValidationRule:
        columns_sql = ", ".join([f"'{column}'" for column in columns])
        return ValidationRule(
            rule_id="required_columns_exist",
            description="Required validation columns must exist",
            severity="BLOCKER",
            issue_count_sql=f"""
                SELECT {len(columns)} - count() AS issue_count
                FROM system.columns
                WHERE database = '{db_name}'
                  AND table = '{table_name}'
                  AND name IN ({columns_sql})
            """,
        )

    @staticmethod
    def _ohlc_issue_sql(
        qualified_table_name: str,
        validation_filter: str | None = None,
        activity_condition: str | None = None,
    ) -> str:
        ohlc_condition = "high < low OR high < open OR high < close OR low > open OR low > close"
        if activity_condition:
            ohlc_condition = f"({activity_condition}) AND ({ohlc_condition})"
        return f"""
            SELECT count() AS issue_count
            FROM {qualified_table_name}
            {QualityManager._where_sql(ohlc_condition, validation_filter)}
        """

    @staticmethod
    def _dwd_validation_filter(table_name: str) -> str | None:
        if table_name in DWD_TRADE_RELEVANT_TABLES:
            return f"event_date >= {TRADE_VALIDATION_MIN_DATE_SQL}"
        return None

    @staticmethod
    def _dws_validation_filter(table_name: str) -> str | None:
        date_column = DWS_TRADE_DATE_COLUMNS.get(table_name)
        if date_column:
            return f"{date_column} >= {TRADE_VALIDATION_MIN_DATE_SQL}"
        return None

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f.read())

    def _load_dwd_spec(self, table_name: str) -> dict[str, Any]:
        for path in DWD_SCHEMA_DIR.glob("*.yaml"):
            spec = self._load_yaml(path)
            if spec["name"] == table_name:
                return spec
        raise ValueError(f"DWD table {table_name} not found")

    def _load_ods_schema(self, schema_name: str) -> dict[str, Any]:
        return self._load_yaml(ODS_SCHEMA_DIR / f"{schema_name}.yaml")

    def _dwd_business_key_columns(self, table_name: str) -> list[str]:
        spec = self._load_dwd_spec(table_name)
        if spec.get("builder", "raw_versioned") == "security_master":
            return ["instrument_id"]

        source_schema = self._load_ods_schema(spec["source"]["schema_name"])
        key_columns = spec.get("business_key") or source_schema.get("primary_key", [])
        if not key_columns:
            if table_name == "dwd_trade_calendar":
                return ["event_date"]
            return ["instrument_id", "event_date"]
        return key_columns

    def _dwd_open_version_rules(
        self,
        table_name: str,
        qualified: str,
        validation_filter: str | None = None,
    ) -> list[ValidationRule]:
        key_columns = self._dwd_business_key_columns(table_name)

        key_select = ", ".join(key_columns)
        partition = ", ".join(key_columns)
        return [
            ValidationRule(
                rule_id="dwd_single_open_version",
                description="DWD tables must have at most one open PIT version per business key",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM (
                        SELECT {key_select}
                        FROM {qualified}
                        {self._where_sql(f"sys_to = {FAR_FUTURE_TS}", validation_filter)}
                        GROUP BY {key_select}
                        HAVING count() > 1
                    )
                """,
            ),
            ValidationRule(
                rule_id="dwd_no_overlapping_versions",
                description="DWD version windows must not overlap for the same business key",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM (
                        SELECT
                            {key_select},
                            sys_from,
                            sys_to,
                            leadInFrame(sys_from, 1, {FAR_FUTURE_TS}) OVER (
                                PARTITION BY {partition}
                                ORDER BY sys_from, source_batch_id, source_record_hash
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                            ) AS next_sys_from
                        FROM {qualified}
                        {self._where_sql(validation_filter=validation_filter)}
                    )
                    WHERE sys_to > next_sys_from
                """,
            ),
        ]

    def _dwd_business_rules(
        self,
        table_name: str,
        qualified: str,
        validation_filter: str | None = None,
    ) -> list[ValidationRule]:
        rules: list[ValidationRule] = []
        if table_name in {"dwd_stock_eod_price", "dwd_index_eod_price", "dwd_future_eod_price"}:
            rules.extend(self._market_price_rules(table_name, qualified, validation_filter))
        if table_name == "dwd_stock_daily_basic":
            rules.extend(self._daily_basic_rules(qualified, validation_filter))
        if table_name == "dwd_stock_eod_quote_metrics":
            rules.extend(self._quote_metric_rules(qualified, validation_filter))
        if table_name == "dwd_stock_adj_factor":
            rules.append(
                ValidationRule(
                    rule_id="adj_factor_positive",
                    description="Adjustment factor must be positive",
                    severity="BLOCKER",
                    issue_count_sql=f"""
                        SELECT count() AS issue_count
                        FROM {qualified}
                        {self._where_sql("adj_factor <= 0", validation_filter)}
                    """,
                )
            )
        if table_name in {
            "dwd_stock_financial_indicator",
            "dwd_stock_income",
            "dwd_stock_balance_sheet",
            "dwd_stock_cashflow",
        }:
            rules.extend(self._financial_rules(table_name, qualified))
        if table_name == "dwd_stock_margin_trading":
            rules.extend(self._margin_rules(qualified, validation_filter))
        if table_name == "dwd_stock_northbound_holding":
            rules.extend(self._northbound_rules(qualified, validation_filter))
        if table_name == "dwd_stock_chip_distribution":
            rules.extend(self._chip_rules(qualified, validation_filter))
        if table_name == "dwd_security_master":
            rules.extend(self._security_master_rules(qualified))
        return rules

    def _market_price_rules(
        self,
        table_name: str,
        qualified: str,
        validation_filter: str | None = None,
    ) -> list[ValidationRule]:
        rules = [
            ValidationRule(
                rule_id="market_ohlc_consistency",
                description="Market OHLC fields must be internally consistent",
                severity="BLOCKER",
                issue_count_sql=self._ohlc_issue_sql(
                    qualified,
                    validation_filter,
                    "vol > 0 OR open > 0 OR high > 0 OR low > 0",
                ),
            ),
            ValidationRule(
                rule_id="market_nonnegative_volume_amount",
                description="Market volume and amount must be nonnegative",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("vol < 0 OR amount < 0", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="market_positive_prices_when_traded",
                description="Traded rows must have positive OHLC and pre-close prices",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("vol > 0 AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR pre_close <= 0)", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="market_available_not_before_event",
                description="Market data cannot be available before event date",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("available_trade_date < event_date", validation_filter)}
                """,
            ),
        ]
        if table_name == "dwd_future_eod_price":
            rules.append(
                ValidationRule(
                    rule_id="future_settle_positive_when_traded",
                    description="Traded future rows must have positive settlement prices",
                    severity="BLOCKER",
                    issue_count_sql=f"""
                        SELECT count() AS issue_count
                        FROM {qualified}
                        {self._where_sql("vol > 0 AND (settle <= 0 OR pre_settle <= 0 OR oi < 0)", validation_filter)}
                    """,
                )
            )
        return rules

    def _daily_basic_rules(self, qualified: str, validation_filter: str | None = None) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="daily_basic_share_hierarchy",
                description="Total shares must be at least float shares and free shares",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("total_share < 0 OR float_share < 0 OR free_share < 0 OR total_share < float_share OR float_share < free_share", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="daily_basic_market_value_hierarchy",
                description="Total market value must be at least circulating market value",
                severity="WARN",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("total_mv < 0 OR circ_mv < 0 OR total_mv < circ_mv", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="daily_basic_nonnegative_turnover",
                description="Turnover and volume-ratio fields must be nonnegative",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("turnover_rate < 0 OR turnover_rate_f < 0 OR volume_ratio < 0", validation_filter)}
                """,
            ),
        ]

    def _quote_metric_rules(self, qualified: str, validation_filter: str | None = None) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="quote_metrics_ohlc_consistency",
                description="Quote metric OHLC fields must be internally consistent",
                severity="BLOCKER",
                issue_count_sql=self._ohlc_issue_sql(
                    qualified,
                    validation_filter,
                    "vol > 0 OR open > 0 OR high > 0 OR low > 0",
                ),
            ),
            ValidationRule(
                rule_id="quote_metrics_average_price_range",
                description="Average price should be inside the daily low-high range when traded",
                severity="WARN",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("vol > 0 AND avg_price > 0 AND (avg_price < low OR avg_price > high)", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="quote_metrics_nonnegative_market_fields",
                description="Quote metric market activity fields must be nonnegative",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("vol < 0 OR amount < 0 OR vol_ratio < 0 OR turn_over < 0", validation_filter)}
                """,
            ),
        ]

    def _financial_rules(self, table_name: str, qualified: str) -> list[ValidationRule]:
        rules = [
            ValidationRule(
                rule_id="financial_no_placeholder_dates",
                description="Financial DWD rows must not use placeholder dates",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE event_date <= toDate32('1971-01-01')
                       OR ann_date <= toDate32('1971-01-01')
                       OR available_trade_date <= toDate32('1971-01-01')
                """,
            ),
            ValidationRule(
                rule_id="financial_quarter_end_event_date",
                description="Financial event date must be a quarter-end date",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE formatDateTime(event_date, '%m-%d') NOT IN ('03-31', '06-30', '09-30', '12-31')
                """,
            ),
            ValidationRule(
                rule_id="financial_announced_after_period",
                description="Financial announcement date must not precede report period end",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE ann_date < event_date
                """,
            ),
            ValidationRule(
                rule_id="financial_no_same_day_pit_visibility",
                description="Financial rows must become available after announcement date",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE available_trade_date <= ann_date
                """,
            ),
        ]
        if table_name == "dwd_stock_balance_sheet":
            rules.append(
                ValidationRule(
                    rule_id="balance_sheet_assets_equation",
                    description="Balance sheet assets should reconcile with liabilities plus equity",
                    severity="WARN",
                    issue_count_sql=f"""
                        SELECT count() AS issue_count
                        FROM {qualified}
                        WHERE total_assets IS NOT NULL
                          AND total_liab IS NOT NULL
                          AND total_hldr_eqy_inc_min_int IS NOT NULL
                          AND abs(total_assets - total_liab - total_hldr_eqy_inc_min_int)
                              > greatest(abs(total_assets) * 0.01, 1)
                    """,
                )
            )
        if table_name == "dwd_stock_cashflow":
            rules.append(
                ValidationRule(
                    rule_id="cashflow_operating_net_flow",
                    description="Operating cash-flow net amount should reconcile with inflow minus outflow",
                    severity="WARN",
                    issue_count_sql=f"""
                        SELECT count() AS issue_count
                        FROM {qualified}
                        WHERE c_inf_fr_operate_a IS NOT NULL
                          AND st_cash_out_act IS NOT NULL
                          AND n_cashflow_act IS NOT NULL
                          AND abs(n_cashflow_act - c_inf_fr_operate_a + st_cash_out_act)
                              > greatest(abs(n_cashflow_act) * 0.01, 1)
                    """,
                )
            )
        return rules

    def _margin_rules(self, qualified: str, validation_filter: str | None = None) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="margin_nonnegative_fields",
                description="Margin balances and flows must be nonnegative",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("rzye < 0 OR rqye < 0 OR rzmre < 0 OR rzche < 0 OR rqyl < 0 OR rqchl < 0 OR rqmcl < 0 OR rzrqye < 0", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="margin_total_balance_reconciliation",
                description="Total margin balance should equal financing plus securities lending balance",
                severity="WARN",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("abs(rzrqye - rzye - rqye) > greatest(abs(rzrqye) * 0.001, 1)", validation_filter)}
                """,
            ),
        ]

    def _northbound_rules(self, qualified: str, validation_filter: str | None = None) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="northbound_holding_bounds",
                description="Northbound holding volume and ratio must be in valid bounds",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("vol < 0 OR ratio < 0 OR ratio > 100", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="northbound_channel_present",
                description="Northbound holding rows should keep the connect channel",
                severity="WARN",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("connect_channel = ''", validation_filter)}
                """,
            ),
        ]

    def _chip_rules(self, qualified: str, validation_filter: str | None = None) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="chip_price_bounds",
                description="Chip distribution historical high must be at least historical low",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("his_high < his_low", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="chip_cost_percentiles_monotonic",
                description="Chip distribution cost percentiles must be monotonic",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("cost_5pct > cost_15pct OR cost_15pct > cost_50pct OR cost_50pct > cost_85pct OR cost_85pct > cost_95pct", validation_filter)}
                """,
            ),
            ValidationRule(
                rule_id="chip_winner_rate_bounds",
                description="Chip distribution winner rate must be between 0 and 100",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    {self._where_sql("winner_rate < 0 OR winner_rate > 100", validation_filter)}
                """,
            ),
        ]

    def _security_master_rules(self, qualified: str) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="security_master_lifecycle_dates",
                description="Security master list date must not be after delist date",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE delist_date IS NOT NULL AND list_date IS NOT NULL AND list_date > delist_date
                """,
            ),
            ValidationRule(
                rule_id="security_master_instrument_type",
                description="Security master instrument type must be recognized",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count() AS issue_count
                    FROM {qualified}
                    WHERE instrument_type NOT IN ('stock', 'index', 'future')
                """,
            ),
        ]

    def _metadata_table_schemas(self) -> dict[str, dict[str, Any]]:
        common_indexes = [{"name": "quality_idx", "columns": ["run_id"]}]
        return {
            "dq_validation_run": {
                "comment": "Data quality validation run",
                "primary_key": [],
                "partition_key": ["toYYYYMM(started_at)"],
                "indexes": common_indexes,
                "columns": [
                    {"name": "run_id", "data_type": "str", "length": 64, "comment": "Validation run id"},
                    {"name": "layer", "data_type": "str", "length": 32, "comment": "Data layer"},
                    {"name": "stage", "data_type": "str", "length": 64, "comment": "Validation stage"},
                    {"name": "table_name", "data_type": "str", "length": 128, "comment": "Logical table name"},
                    {"name": "target_table_name", "data_type": "str", "length": 128, "comment": "Physical target table"},
                    {"name": "mode", "data_type": "str", "length": 32, "comment": "Validation mode"},
                    {"name": "status", "data_type": "str", "length": 32, "comment": "Validation run status"},
                    {"name": "started_at", "data_type": "datetime", "comment": "Run start time"},
                    {"name": "finished_at", "data_type": "datetime", "comment": "Run finish time"},
                ],
            },
            "dq_validation_result": {
                "comment": "Data quality validation rule result",
                "primary_key": [],
                "partition_key": ["toYYYYMM(created_at)"],
                "indexes": common_indexes,
                "columns": [
                    {"name": "run_id", "data_type": "str", "length": 64, "comment": "Validation run id"},
                    {"name": "rule_id", "data_type": "str", "length": 128, "comment": "Validation rule id"},
                    {"name": "severity", "data_type": "str", "length": 32, "comment": "Rule severity"},
                    {"name": "status", "data_type": "str", "length": 32, "comment": "Rule status"},
                    {"name": "issue_count", "data_type": "int", "comment": "Issue row count"},
                    {"name": "description", "data_type": "str", "length": 512, "comment": "Rule description"},
                    {"name": "message", "data_type": "str", "length": 1024, "comment": "Rule message"},
                    {"name": "created_at", "data_type": "datetime", "comment": "Created time"},
                ],
            },
            "dq_validation_metric": {
                "comment": "Data quality validation metrics",
                "primary_key": [],
                "partition_key": ["toYYYYMM(created_at)"],
                "indexes": common_indexes,
                "columns": [
                    {"name": "run_id", "data_type": "str", "length": 64, "comment": "Validation run id"},
                    {"name": "metric_name", "data_type": "str", "length": 128, "comment": "Metric name"},
                    {"name": "metric_value", "data_type": "float", "comment": "Metric value"},
                    {"name": "table_name", "data_type": "str", "length": 128, "comment": "Logical table name"},
                    {"name": "created_at", "data_type": "datetime", "comment": "Created time"},
                ],
            },
            "dq_issue_sample": {
                "comment": "Data quality failed-row samples",
                "primary_key": [],
                "partition_key": ["toYYYYMM(created_at)"],
                "indexes": common_indexes,
                "columns": [
                    {"name": "run_id", "data_type": "str", "length": 64, "comment": "Validation run id"},
                    {"name": "rule_id", "data_type": "str", "length": 128, "comment": "Validation rule id"},
                    {"name": "sample_json", "data_type": "json", "length": 8192, "comment": "Failed-row sample JSON"},
                    {"name": "created_at", "data_type": "datetime", "comment": "Created time"},
                ],
            },
        }

    def ensure_result_tables(self) -> None:
        db_engine = self.get_db_engine()
        for table_name, schema in self._metadata_table_schemas().items():
            db_engine.create_table(table_name, schema)

    def _record_run(self, run: ValidationRun) -> None:
        try:
            if self.settings.quality.create_result_tables:
                self.ensure_result_tables()

            db_engine = self.get_db_engine()
            db_engine.insert(
                "dq_validation_run",
                self._metadata_table_schemas()["dq_validation_run"],
                pd.DataFrame(
                    [
                        {
                            "run_id": run.run_id,
                            "layer": run.layer,
                            "stage": run.stage,
                            "table_name": run.table_name,
                            "target_table_name": run.target_table_name,
                            "mode": run.mode,
                            "status": run.status,
                            "started_at": run.started_at,
                            "finished_at": run.finished_at,
                        }
                    ]
                ),
            )
            if run.results:
                db_engine.insert(
                    "dq_validation_result",
                    self._metadata_table_schemas()["dq_validation_result"],
                    pd.DataFrame(
                        [
                            {
                                "run_id": run.run_id,
                                "rule_id": result.rule_id,
                                "severity": result.severity,
                                "status": result.status,
                                "issue_count": result.issue_count,
                                "description": result.description,
                                "message": result.message,
                                "created_at": run.finished_at,
                            }
                            for result in run.results
                        ]
                    ),
                )
                db_engine.insert(
                    "dq_validation_metric",
                    self._metadata_table_schemas()["dq_validation_metric"],
                    pd.DataFrame(
                        [
                            {
                                "run_id": run.run_id,
                                "metric_name": f"{result.rule_id}.issue_count",
                                "metric_value": float(result.issue_count),
                                "table_name": run.table_name,
                                "created_at": run.finished_at,
                            }
                            for result in run.results
                        ]
                    ),
                )
        except Exception:
            logging.exception("Failed to record validation run %s", run.run_id)

    def report_run(self, run_id: str) -> str:
        db_name = self.settings.database.db_name
        run_df = self.get_db_engine().query_df(
            f"""
            SELECT *
            FROM {db_name}.dq_validation_run
            WHERE run_id = '{run_id}'
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        result_df = self.get_db_engine().query_df(
            f"""
            SELECT rule_id, severity, status, issue_count, description, message
            FROM {db_name}.dq_validation_result
            WHERE run_id = '{run_id}'
            ORDER BY severity, rule_id
            """
        )
        if run_df.empty:
            return f"Validation run {run_id} not found"
        run = run_df.iloc[0].to_dict()
        lines = [
            f"run_id: {run_id}",
            f"table: {run.get('layer')}.{run.get('table_name')} target={run.get('target_table_name')}",
            f"mode/status: {run.get('mode')}/{run.get('status')}",
        ]
        for row in result_df.to_dict("records"):
            message = f" message={row['message']}" if row.get("message") else ""
            lines.append(
                f"- {row['severity']} {row['status']} {row['rule_id']} issues={row['issue_count']}{message}"
            )
        return "\n".join(lines)

    @staticmethod
    def run_to_json(run: ValidationRun) -> str:
        return json.dumps(
            {
                "run_id": run.run_id,
                "layer": run.layer,
                "stage": run.stage,
                "table_name": run.table_name,
                "target_table_name": run.target_table_name,
                "mode": run.mode,
                "status": run.status,
                "results": [result.__dict__ for result in run.results],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
