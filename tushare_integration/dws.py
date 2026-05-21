from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from tushare_integration.db_engine import DatabaseEngineFactory
from tushare_integration.dwd import FAR_FUTURE_TS_SQL
from tushare_integration.quality import QualityManager, ValidationMode
from tushare_integration.settings import TushareIntegrationSettings


ROOT_DIR = Path(__file__).resolve().parent.parent
DWS_SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema" / "dws"
FACTOR_MAPPING_CSV = ROOT_DIR / "docs" / "prd" / "factor_mapping_readable.csv"
DWS_CLICKHOUSE_SEND_RECEIVE_TIMEOUT = 1200

STOCK_FACTOR_WIDE_SOURCES = [
    "dwd_stock_eod_price",
    "dwd_stock_daily_basic",
    "dwd_stock_eod_quote_metrics",
    "dwd_stock_financial_indicator",
    "dwd_stock_northbound_holding",
    "dwd_stock_margin_trading",
    "dwd_stock_chip_distribution",
]
STOCK_FACTOR_WIDE_MATRIX_SOURCES = ["dws_stock_factor_wide"]
STOCK_FACTOR_WIDE_MATRIX_UDF = "dws_stock_factor_rows"
STOCK_FACTOR_WIDE_MATRIX_PREFIX_COLUMNS = [
    "trade_date",
    "event_date",
    "available_trade_date",
    "source_batch_id",
    "source_record_hash",
]
STOCK_FACTOR_WIDE_MATRIX_EXCLUDED_FIELDS = {
    "build_time",
}
STOCK_FACTOR_WIDE_MATRIX_ALIASES = {
    "volume": "`vol`",
    "vwap": "`avg_price`",
    "turnover": "coalesce(`turnover_rate_f`, `turn_over`)",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _load_factor_ids() -> list[str]:
    with open(FACTOR_MAPPING_CSV, "r", encoding="utf-8") as f:
        rows = csv.DictReader(f)
        factor_ids = []
        seen = set()
        for row in rows:
            factor_id = row["factor_id"].strip()
            if factor_id and factor_id not in seen:
                seen.add(factor_id)
                factor_ids.append(factor_id)
        return factor_ids


class DWSManager:
    def __init__(self):
        self.settings = TushareIntegrationSettings.model_validate(
            yaml.safe_load(open("config.yaml", "r", encoding="utf-8").read())
        )
        self.db_engine = None

    def get_db_engine(self):
        if self.db_engine is None:
            clickhouse_timeout = (
                DWS_CLICKHOUSE_SEND_RECEIVE_TIMEOUT
                if self.settings.database.db_type == "clickhouse"
                else None
            )
            self.db_engine = DatabaseEngineFactory.create(
                self.settings,
                clickhouse_send_receive_timeout=clickhouse_timeout,
            )
        return self.db_engine

    def list_tables(self) -> list[str]:
        table_names = []
        for path in sorted(DWS_SCHEMA_DIR.glob("*.yaml")):
            spec = _load_yaml(path)
            table_names.append(spec["name"])
        return table_names

    def load_spec(self, table_name: str) -> dict[str, Any]:
        for path in DWS_SCHEMA_DIR.glob("*.yaml"):
            spec = _load_yaml(path)
            if spec["name"] == table_name:
                return spec
        raise ValueError(f"DWS table {table_name} not found")

    def build_schema(self, spec: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(spec["schema"])

    def _render_stock_factor_wide_sync_sql(self, target_table_name: str) -> str:
        db_name = self.settings.database.db_name
        source_table_sql = ",".join(STOCK_FACTOR_WIDE_SOURCES)
        return f"""
INSERT INTO {db_name}.{target_table_name}
WITH
price AS (
    SELECT *
    FROM {db_name}.dwd_stock_eod_price
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
daily_basic AS (
    SELECT *
    FROM {db_name}.dwd_stock_daily_basic
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
quote_metrics AS (
    SELECT *
    FROM {db_name}.dwd_stock_eod_quote_metrics
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
financial_indicator AS (
    SELECT
        instrument_id,
        event_date,
        available_trade_date,
        source_batch_id,
        source_record_hash,
        roe,
        roa,
        roic,
        grossprofit_margin,
        netprofit_margin,
        or_yoy,
        netprofit_yoy,
        op_yoy,
        basic_eps_yoy,
        q_roe,
        q_gsprofit_margin,
        q_netprofit_yoy,
        q_sales_yoy,
        ocf_to_or,
        ocf_to_profit,
        debt_to_assets,
        current_ratio,
        eps,
        bps,
        ocfps,
        rd_exp
    FROM (
        SELECT
            src.*,
            row_number() OVER (
                PARTITION BY src.instrument_id, src.available_trade_date
                ORDER BY
                    src.event_date DESC,
                    src.sys_from DESC,
                    src.source_record_hash DESC
            ) AS financial_rank
        FROM {db_name}.dwd_stock_financial_indicator src
        WHERE src.sys_to = {FAR_FUTURE_TS_SQL}
    ) src
    WHERE financial_rank = 1
),
northbound_holding AS (
    SELECT *
    FROM {db_name}.dwd_stock_northbound_holding
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
margin_trading AS (
    SELECT *
    FROM {db_name}.dwd_stock_margin_trading
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
chip_distribution AS (
    SELECT *
    FROM {db_name}.dwd_stock_chip_distribution
    WHERE sys_to = {FAR_FUTURE_TS_SQL}
),
wide_candidates AS (
    SELECT
        price.instrument_id AS instrument_id,
        price.instrument_type AS instrument_type,
        price.exchange AS exchange,
        price.source_code AS source_code,
        price.event_date AS event_date,
        price.event_date AS trade_date,
        greatest(
            price.available_trade_date,
            coalesce(daily_basic.available_trade_date, price.available_trade_date),
            coalesce(quote_metrics.available_trade_date, price.available_trade_date),
            coalesce(financial_indicator.available_trade_date, price.available_trade_date),
            coalesce(northbound_holding.available_trade_date, price.available_trade_date),
            coalesce(margin_trading.available_trade_date, price.available_trade_date),
            coalesce(chip_distribution.available_trade_date, price.available_trade_date)
        ) AS available_trade_date,
        price.open AS open,
        price.high AS high,
        price.low AS low,
        price.close AS close,
        price.pre_close AS pre_close,
        price.pct_chg AS pct_chg,
        price.vol AS vol,
        price.amount AS amount,
        quote_metrics.buying AS buying,
        quote_metrics.selling AS selling,
        quote_metrics.vol_ratio AS vol_ratio,
        quote_metrics.turn_over AS turn_over,
        quote_metrics.swing AS swing,
        quote_metrics.avg_price AS avg_price,
        quote_metrics.strength AS strength,
        quote_metrics.activity AS activity,
        quote_metrics.avg_turnover AS avg_turnover,
        quote_metrics.attack AS attack,
        daily_basic.pe_ttm AS pe_ttm,
        daily_basic.pb AS pb,
        daily_basic.ps_ttm AS ps_ttm,
        daily_basic.dv_ttm AS dv_ttm,
        daily_basic.turnover_rate_f AS turnover_rate_f,
        daily_basic.volume_ratio AS volume_ratio_db,
        daily_basic.circ_mv AS circ_mv,
        daily_basic.total_mv AS total_mv,
        daily_basic.total_share AS total_share,
        daily_basic.float_share AS float_share,
        daily_basic.free_share AS free_share,
        financial_indicator.roe AS roe,
        financial_indicator.roa AS roa,
        financial_indicator.roic AS roic,
        financial_indicator.grossprofit_margin AS grossprofit_margin,
        financial_indicator.netprofit_margin AS netprofit_margin,
        financial_indicator.or_yoy AS or_yoy,
        financial_indicator.netprofit_yoy AS netprofit_yoy,
        financial_indicator.op_yoy AS op_yoy,
        financial_indicator.basic_eps_yoy AS basic_eps_yoy,
        financial_indicator.q_roe AS q_roe,
        financial_indicator.q_gsprofit_margin AS q_gsprofit_margin,
        financial_indicator.q_netprofit_yoy AS q_netprofit_yoy,
        financial_indicator.q_sales_yoy AS q_sales_yoy,
        financial_indicator.ocf_to_or AS ocf_to_or,
        financial_indicator.ocf_to_profit AS ocf_to_profit,
        financial_indicator.debt_to_assets AS debt_to_assets,
        financial_indicator.current_ratio AS current_ratio,
        financial_indicator.eps AS eps,
        financial_indicator.bps AS bps,
        financial_indicator.ocfps AS ocfps,
        financial_indicator.rd_exp AS rd_exp,
        northbound_holding.vol AS hk_hold_vol,
        northbound_holding.ratio AS hk_hold_ratio,
        margin_trading.rzye AS rzye,
        margin_trading.rzmre AS rzmre,
        margin_trading.rzche AS rzche,
        margin_trading.rqye AS rqye,
        margin_trading.rqyl AS rqyl,
        margin_trading.rqmcl AS rqmcl,
        chip_distribution.winner_rate AS winner_rate,
        chip_distribution.cost_5pct AS cost_5pct,
        chip_distribution.cost_50pct AS cost_50pct,
        chip_distribution.cost_95pct AS cost_95pct,
        chip_distribution.weight_avg AS weight_avg_cost,
        now64(3) AS build_time,
        'derived' AS source,
        '{source_table_sql}' AS source_table,
        concat(
            price.source_batch_id,
            '|', coalesce(daily_basic.source_batch_id, ''),
            '|', coalesce(quote_metrics.source_batch_id, ''),
            '|', coalesce(financial_indicator.source_batch_id, ''),
            '|', coalesce(northbound_holding.source_batch_id, ''),
            '|', coalesce(margin_trading.source_batch_id, ''),
            '|', coalesce(chip_distribution.source_batch_id, '')
        ) AS source_batch_id,
        lower(hex(MD5(concat(
            price.source_record_hash,
            '|', coalesce(daily_basic.source_record_hash, ''),
            '|', coalesce(quote_metrics.source_record_hash, ''),
            '|', coalesce(financial_indicator.source_record_hash, ''),
            '|', coalesce(northbound_holding.source_record_hash, ''),
            '|', coalesce(margin_trading.source_record_hash, ''),
            '|', coalesce(chip_distribution.source_record_hash, '')
        )))) AS source_record_hash
    FROM price
    LEFT JOIN daily_basic
        ON daily_basic.instrument_id = price.instrument_id
       AND daily_basic.event_date = price.event_date
    LEFT JOIN quote_metrics
        ON quote_metrics.instrument_id = price.instrument_id
       AND quote_metrics.event_date = price.event_date
    ASOF LEFT JOIN financial_indicator
        ON price.instrument_id = financial_indicator.instrument_id
       AND price.available_trade_date >= financial_indicator.available_trade_date
    LEFT JOIN northbound_holding
        ON northbound_holding.instrument_id = price.instrument_id
       AND northbound_holding.event_date = price.event_date
    LEFT JOIN margin_trading
        ON margin_trading.instrument_id = price.instrument_id
       AND margin_trading.event_date = price.event_date
    LEFT JOIN chip_distribution
        ON chip_distribution.instrument_id = price.instrument_id
       AND chip_distribution.event_date = price.event_date
)
SELECT
    instrument_id,
    instrument_type,
    exchange,
    source_code,
    event_date,
    trade_date,
    available_trade_date,
    open,
    high,
    low,
    close,
    pre_close,
    pct_chg,
    vol,
    amount,
    buying,
    selling,
    vol_ratio,
    turn_over,
    swing,
    avg_price,
    strength,
    activity,
    avg_turnover,
    attack,
    pe_ttm,
    pb,
    ps_ttm,
    dv_ttm,
    turnover_rate_f,
    volume_ratio_db,
    circ_mv,
    total_mv,
    total_share,
    float_share,
    free_share,
    roe,
    roa,
    roic,
    grossprofit_margin,
    netprofit_margin,
    or_yoy,
    netprofit_yoy,
    op_yoy,
    basic_eps_yoy,
    q_roe,
    q_gsprofit_margin,
    q_netprofit_yoy,
    q_sales_yoy,
    ocf_to_or,
    ocf_to_profit,
    debt_to_assets,
    current_ratio,
    eps,
    bps,
    ocfps,
    rd_exp,
    hk_hold_vol,
    hk_hold_ratio,
    rzye,
    rzmre,
    rzche,
    rqye,
    rqyl,
    rqmcl,
    winner_rate,
    cost_5pct,
    cost_50pct,
    cost_95pct,
    weight_avg_cost,
    build_time,
    source,
    source_table,
    source_batch_id,
    source_record_hash
FROM wide_candidates
"""

    def _stock_factor_matrix_source_fields(self) -> list[tuple[str, str]]:
        wide_spec = self.load_spec("dws_stock_factor_wide")
        source_columns = wide_spec["schema"]["columns"]
        numeric_fields = [
            column["name"]
            for column in source_columns
            if column.get("data_type") in {"float", "number", "int"}
            and column["name"] not in STOCK_FACTOR_WIDE_MATRIX_EXCLUDED_FIELDS
        ]
        field_exprs: dict[str, str] = {field_name: f"`{field_name}`" for field_name in numeric_fields}
        for alias, expression in STOCK_FACTOR_WIDE_MATRIX_ALIASES.items():
            field_exprs.setdefault(alias, expression)
        return sorted(field_exprs.items())

    def _render_stock_factor_wide_matrix_sync_sql(self, target_table_name: str) -> str:
        db_name = self.settings.database.db_name
        source_table = STOCK_FACTOR_WIDE_MATRIX_SOURCES[0]
        fields = self._stock_factor_matrix_source_fields()
        factor_ids = _load_factor_ids()
        field_names_json = _sql_string_literal(json.dumps([name for name, _ in fields], ensure_ascii=False))
        row_tuple_values = ",\n                ".join(
            [f"`{column}`" for column in STOCK_FACTOR_WIDE_MATRIX_PREFIX_COLUMNS]
            + [expression for _, expression in fields]
        )
        factor_select_sql = ",\n    ".join(
            [
                "toFloat64OrNull("
                f"JSONExtractRaw(factor_values_json, 'values', {_sql_string_literal(factor_id)})"
                f") AS `{factor_id}`"
                for factor_id in factor_ids
            ]
        )
        return f"""
INSERT INTO {db_name}.{target_table_name}
WITH
panel AS (
    SELECT
        instrument_id,
        anyLast(instrument_type) AS instrument_type,
        anyLast(exchange) AS exchange,
        anyLast(source_code) AS source_code,
        arraySort(
            row -> tupleElement(row, 1),
            groupArray(tuple(
                {row_tuple_values}
            ))
        ) AS rows
    FROM {db_name}.{source_table}
    GROUP BY instrument_id
),
factorized AS (
    SELECT
        instrument_id,
        instrument_type,
        exchange,
        source_code,
        arrayJoin({STOCK_FACTOR_WIDE_MATRIX_UDF}({field_names_json}, toJSONString(rows))) AS factor_row
    FROM panel
),
factor_rows AS (
    SELECT
        instrument_id,
        instrument_type,
        exchange,
        source_code,
        tupleElement(factor_row, 1) AS event_date,
        tupleElement(factor_row, 2) AS trade_date,
        tupleElement(factor_row, 3) AS available_trade_date,
        tupleElement(factor_row, 4) AS factor_values_json,
        tupleElement(factor_row, 5) AS factor_errors_json,
        tupleElement(factor_row, 6) AS factor_count,
        tupleElement(factor_row, 7) AS source_batch_id,
        tupleElement(factor_row, 8) AS source_record_hash
    FROM factorized
)
SELECT
    instrument_id,
    instrument_type,
    exchange,
    source_code,
    event_date,
    trade_date,
    available_trade_date,
    {factor_select_sql},
    factor_errors_json,
    factor_count,
    now64(3) AS build_time,
    'python_udf' AS source,
    '{source_table}' AS source_table,
    source_batch_id,
    source_record_hash
FROM factor_rows
"""

    def render_sync_sql(self, table_name: str, target_table_name: str | None = None) -> str:
        spec = self.load_spec(table_name)
        target_table_name = target_table_name or spec["name"]
        if spec.get("builder") == "stock_factor_wide":
            return self._render_stock_factor_wide_sync_sql(target_table_name)
        if spec.get("builder") == "stock_factor_wide_matrix":
            return self._render_stock_factor_wide_matrix_sync_sql(target_table_name)
        raise ValueError(f"Unsupported DWS builder for {table_name}: {spec.get('builder')}")

    def get_required_source_tables(self, spec: dict[str, Any]) -> list[str]:
        if spec.get("builder") == "stock_factor_wide":
            return STOCK_FACTOR_WIDE_SOURCES
        if spec.get("builder") == "stock_factor_wide_matrix":
            return STOCK_FACTOR_WIDE_MATRIX_SOURCES
        return []

    def ensure_source_tables(self, spec: dict[str, Any]) -> None:
        db_name = self.settings.database.db_name
        required_tables = self.get_required_source_tables(spec)
        source_table_list = ", ".join([f"'{table_name}'" for table_name in required_tables])
        existing_tables = self.get_db_engine().query_df(
            f"""
            SELECT name
            FROM system.tables
            WHERE database = '{db_name}'
              AND name IN ({source_table_list})
            """
        )["name"].tolist()

        missing_tables = sorted(set(required_tables) - set(existing_tables))
        if missing_tables:
            raise ValueError(
                f"Missing source tables for {spec['name']}: {', '.join(missing_tables)}. "
                "Sync the corresponding upstream tables first."
            )

    def ensure_required_functions(self, spec: dict[str, Any]) -> None:
        if self.settings.database.db_type != "clickhouse":
            return
        if spec.get("builder") != "stock_factor_wide_matrix":
            return
        result = self.get_db_engine().query_df(
            f"""
            SELECT count() AS function_count
            FROM system.functions
            WHERE name = '{STOCK_FACTOR_WIDE_MATRIX_UDF}'
              AND origin = 'ExecutableUserDefined'
            """
        )
        if int(result["function_count"].iloc[0]) <= 0:
            raise ValueError(
                f"Missing ClickHouse executable UDF {STOCK_FACTOR_WIDE_MATRIX_UDF}. "
                "Install deploy/clickhouse/user_scripts/dws_stock_factor_rows.py under user_scripts_path "
                "and deploy/clickhouse/user_defined_functions/dws_stock_factor_rows.xml under "
                "user_defined_executable_functions_config, then reload ClickHouse functions."
            )

    def create_table(self, table_name: str) -> None:
        spec = self.load_spec(table_name)
        self.get_db_engine().create_table(spec["name"], self.build_schema(spec))

    def _clickhouse_table_exists(self, table_name: str) -> bool:
        db_name = self.settings.database.db_name
        result = self.get_db_engine().query_df(
            f"""
            SELECT count() AS table_count
            FROM system.tables
            WHERE database = '{db_name}'
              AND name = '{table_name}'
            """
        )
        return int(result["table_count"].iloc[0]) > 0

    def _replace_clickhouse_table_from_tmp(self, target_table: str, tmp_table: str) -> None:
        db_name = self.settings.database.db_name
        db_engine = self.get_db_engine()
        qualified_target = f"{db_name}.{target_table}"
        qualified_tmp = f"{db_name}.{tmp_table}"

        if not self._clickhouse_table_exists(target_table):
            db_engine.query(f"RENAME TABLE {qualified_tmp} TO {qualified_target}")
            return

        try:
            db_engine.query(f"EXCHANGE TABLES {qualified_target} AND {qualified_tmp}")
        except Exception:
            db_engine.query(f"DROP TABLE IF EXISTS {qualified_target}")
            db_engine.query(f"RENAME TABLE {qualified_tmp} TO {qualified_target}")
        else:
            db_engine.query(f"DROP TABLE IF EXISTS {qualified_tmp}")

    def sync_table(
        self,
        table_name: str,
        validation_mode: ValidationMode | None = None,
        skip_validation: bool = False,
    ) -> None:
        spec = self.load_spec(table_name)
        self.ensure_source_tables(spec)
        self.ensure_required_functions(spec)
        target_table = spec["name"]
        tmp_table = f"{target_table}_tmp"
        schema = self.build_schema(spec)
        tmp_schema = deepcopy(schema)
        tmp_schema["comment"] = f"{schema['comment']} TMP"

        db_name = self.settings.database.db_name
        db_engine = self.get_db_engine()
        db_engine.query(f"DROP TABLE IF EXISTS {db_name}.{tmp_table}")
        db_engine.create_table(tmp_table, tmp_schema)
        db_engine.query(self.render_sync_sql(table_name, target_table_name=tmp_table))

        QualityManager(settings=self.settings, db_engine=db_engine).validate_publish(
            layer="dws",
            table_name=target_table,
            target_table_name=tmp_table,
            stage="pre_dws_publish",
            mode=validation_mode,
            skip_validation=skip_validation,
        )

        if self.settings.database.db_type == "clickhouse":
            self._replace_clickhouse_table_from_tmp(target_table, tmp_table)
            return

        db_engine.create_table(target_table, schema)
        db_engine.query(f"TRUNCATE TABLE {db_name}.{target_table}")
        db_engine.query(f"INSERT INTO {db_name}.{target_table} SELECT * FROM {db_name}.{tmp_table}")
        db_engine.query(f"DROP TABLE IF EXISTS {db_name}.{tmp_table}")

    def sync_all(
        self,
        validation_mode: ValidationMode | None = None,
        skip_validation: bool = False,
    ) -> None:
        for table_name in self.list_tables():
            self.sync_table(table_name, validation_mode=validation_mode, skip_validation=skip_validation)
