from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from tushare_integration.db_engine import DatabaseEngineFactory
from tushare_integration.settings import TushareIntegrationSettings


ROOT_DIR = Path(__file__).resolve().parent.parent
DWD_SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema" / "dwd"
ODS_SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema"
FAR_FUTURE_TS_SQL = "toDateTime64('9999-12-31 00:00:00', 3)"
CALENDAR_SOURCE_TABLE = "trade_cal"
STOCK_FACTOR_BAR_SOURCES = ["dwd_stock_eod_price", "dwd_stock_daily_basic"]


COMMON_DWD_COLUMNS = [
    {"name": "instrument_id", "data_type": "str", "length": 64, "comment": "统一证券ID"},
    {"name": "instrument_type", "data_type": "str", "length": 32, "comment": "证券类型"},
    {"name": "exchange", "data_type": "str", "length": 32, "comment": "交易所"},
    {"name": "source_code", "data_type": "str", "length": 64, "comment": "源侧证券代码"},
    {"name": "event_date", "data_type": "date", "comment": "业务归属日期"},
    {"name": "available_trade_date", "data_type": "date", "comment": "最早可用交易日"},
    {"name": "sys_from", "data_type": "datetime", "comment": "版本开始时间"},
    {"name": "sys_to", "data_type": "datetime", "comment": "版本结束时间"},
    {"name": "source", "data_type": "str", "length": 32, "comment": "来源系统"},
    {"name": "source_table", "data_type": "str", "length": 64, "comment": "来源表"},
    {"name": "source_batch_id", "data_type": "str", "length": 64, "comment": "来源批次ID"},
    {"name": "source_record_hash", "data_type": "str", "length": 32, "comment": "来源记录哈希"},
]

COMMON_DWD_COLUMNS_NO_INSTRUMENT = [
    {"name": "event_date", "data_type": "date", "comment": "业务归属日期"},
    {"name": "available_trade_date", "data_type": "date", "comment": "最早可用交易日"},
    {"name": "sys_from", "data_type": "datetime", "comment": "版本开始时间"},
    {"name": "sys_to", "data_type": "datetime", "comment": "版本结束时间"},
    {"name": "source", "data_type": "str", "length": 32, "comment": "来源系统"},
    {"name": "source_table", "data_type": "str", "length": 64, "comment": "来源表"},
    {"name": "source_batch_id", "data_type": "str", "length": 64, "comment": "来源批次ID"},
    {"name": "source_record_hash", "data_type": "str", "length": 32, "comment": "来源记录哈希"},
]


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def _quote_column(name: str) -> str:
    return f"`{name}`"


def _nullable_copy(column: dict[str, Any]) -> dict[str, Any]:
    copied_column = deepcopy(column)
    copied_column["nullable"] = True
    copied_column.pop("default", None)
    return copied_column


class DWDManager:
    def __init__(self):
        self.settings = TushareIntegrationSettings.model_validate(
            yaml.safe_load(open("config.yaml", "r", encoding="utf-8").read())
        )
        self.db_engine = None

    def get_db_engine(self):
        if self.db_engine is None:
            self.db_engine = DatabaseEngineFactory.create(self.settings)
        return self.db_engine

    def list_tables(self) -> list[str]:
        table_names = []
        for path in sorted(DWD_SCHEMA_DIR.glob("*.yaml")):
            spec = _load_yaml(path)
            table_names.append(spec["name"])
        return table_names

    def load_spec(self, table_name: str) -> dict[str, Any]:
        for path in DWD_SCHEMA_DIR.glob("*.yaml"):
            spec = _load_yaml(path)
            if spec["name"] == table_name:
                return spec
        raise ValueError(f"DWD table {table_name} not found")

    def load_source_schema(self, schema_name: str) -> dict[str, Any]:
        return _load_yaml(ODS_SCHEMA_DIR / f"{schema_name}.yaml")

    def _build_common_columns(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        common_columns = (
            COMMON_DWD_COLUMNS if spec.get("with_instrument", True) else COMMON_DWD_COLUMNS_NO_INSTRUMENT
        )
        extra_columns = [_nullable_copy(column) for column in spec.get("extra_columns", [])]
        return common_columns + extra_columns

    def build_schema(self, spec: dict[str, Any]) -> dict[str, Any]:
        if spec.get("builder", "raw_versioned") == "security_master":
            schema = deepcopy(spec["schema"])
            schema["primary_key"] = []
            return schema

        if spec.get("builder") == "stock_factor_bar":
            schema = deepcopy(spec["schema"])
            schema["primary_key"] = []
            return schema

        source_schema = self.load_source_schema(spec["source"]["schema_name"])
        source_columns = [_nullable_copy(column) for column in source_schema["columns"]]
        common_columns = self._build_common_columns(spec)

        return {
            "comment": spec["comment"],
            "primary_key": [],
            "partition_key": spec["partition_key"],
            "indexes": spec["indexes"],
            "columns": source_columns + common_columns,
        }

    def _calendar_map_sql(self) -> str:
        db_name = self.settings.database.db_name
        return f"""
calendar_map AS (
    SELECT
        c.cal_date AS calendar_date,
        min(o.cal_date) AS next_trade_date
    FROM {db_name}.{CALENDAR_SOURCE_TABLE} c
    LEFT JOIN {db_name}.{CALENDAR_SOURCE_TABLE} o
        ON o.exchange = c.exchange
       AND o.is_open = 1
       AND o.cal_date > c.cal_date
    WHERE c.exchange = 'SSE'
    GROUP BY c.cal_date
)"""

    def _render_generic_sync_sql(self, spec: dict[str, Any], target_table_name: str) -> str:
        db_name = self.settings.database.db_name
        source_schema = self.load_source_schema(spec["source"]["schema_name"])
        business_key = spec.get("business_key") or source_schema.get("primary_key", [])
        if not business_key:
            raise ValueError(f"{spec['name']} requires business_key or source primary_key")

        source_columns = [column["name"] for column in source_schema["columns"]]
        business_key_partition = ", ".join([f"src.{_quote_column(column)}" for column in business_key])
        business_key_not_null = " AND ".join([f"src.{_quote_column(column)} IS NOT NULL" for column in business_key])
        source_column_select = ",\n    ".join([f"src.{_quote_column(column)}" for column in source_columns])

        derived_selects: list[str] = []
        if spec.get("with_instrument", True):
            derived_selects.extend(
                [
                    f"{spec['instrument_id_expr']} AS `instrument_id`",
                    f"'{spec['instrument_type']}' AS `instrument_type`",
                    f"{spec['exchange_expr']} AS `exchange`",
                    f"{spec['source_code_expr']} AS `source_code`",
                ]
            )

        derived_selects.extend(
            [
                f"{spec['event_date_expr']} AS `event_date`",
                f"{spec['available_trade_date_expr']} AS `available_trade_date`",
                "src._ingest_time AS `sys_from`",
                "src._next_sys_from AS `sys_to`",
                "src._source AS `source`",
                f"'{spec['source']['table_name']}' AS `source_table`",
                "src._batch_id AS `source_batch_id`",
                "src._record_hash AS `source_record_hash`",
            ]
        )

        for column in spec.get("extra_columns", []):
            derived_selects.append(f"{column['expr']} AS `{column['name']}`")

        derived_column_select = ",\n    ".join(derived_selects)
        with_items = []
        if spec.get("calendar_date_expr"):
            with_items.append(self._calendar_map_sql())

        with_item_sql = ",\n".join(with_items)
        with_clause = f"WITH\n{with_item_sql}" if with_items else ""
        calendar_join = (
            "LEFT JOIN calendar_map ON calendar_map.calendar_date = src._calendar_lookup_date"
            if spec.get("calendar_date_expr")
            else ""
        )
        calendar_lookup_select = (
            f",\n        {spec['calendar_date_expr']} AS _calendar_lookup_date" if spec.get("calendar_date_expr") else ""
        )

        return f"""
INSERT INTO {db_name}.{target_table_name}
{with_clause}
SELECT
    {source_column_select},
    {derived_column_select}
FROM (
    SELECT
        src.*,
        leadInFrame(src._ingest_time, 1, {FAR_FUTURE_TS_SQL}) OVER (
            PARTITION BY {business_key_partition}
            ORDER BY src._ingest_time, src._batch_id, src._record_hash
        ) AS _next_sys_from
        {calendar_lookup_select}
    FROM (
        SELECT
            src.*,
            lagInFrame(src._record_hash) OVER (
                PARTITION BY {business_key_partition}
                ORDER BY src._ingest_time, src._batch_id, src._record_hash
            ) AS _prev_record_hash
        FROM {db_name}.{spec['source']['table_name']} src
        WHERE {business_key_not_null}
    ) src
    WHERE src._prev_record_hash IS NULL OR src._prev_record_hash != src._record_hash
) src
{calendar_join}
"""

    def _render_security_master_sync_sql(self, spec: dict[str, Any], target_table_name: str) -> str:
        db_name = self.settings.database.db_name
        return f"""
INSERT INTO {db_name}.{target_table_name}
WITH
{self._calendar_map_sql()},
security_union AS (
    SELECT
        concat('stock:', src.ts_code) AS instrument_id,
        'stock' AS instrument_type,
        src.exchange AS exchange,
        src.ts_code AS source_code,
        src.symbol AS symbol,
        src.name AS instrument_name,
        src.fullname AS full_name,
        src.enname AS english_name,
        src.market AS market,
        CAST(NULL, 'Nullable(String)') AS category,
        CAST(NULL, 'Nullable(String)') AS publisher,
        src.curr_type AS currency,
        src.list_status AS list_status,
        src.list_date AS list_date,
        src.delist_date AS delist_date,
        src.area AS area,
        src.industry AS industry,
        src.is_hs AS is_hs,
        CAST(NULL, 'Nullable(String)') AS underlying_code,
        CAST(NULL, 'Nullable(Float64)') AS contract_multiplier,
        CAST(NULL, 'Nullable(String)') AS trade_unit,
        CAST(NULL, 'Nullable(String)') AS quote_unit,
        coalesce(src.list_date, toDate(src._ingest_time)) AS event_date,
        coalesce(src.list_date, calendar_map.next_trade_date, toDate(src._ingest_time)) AS available_trade_date,
        src._ingest_time AS sys_from,
        src._source AS source,
        'stock_basic_raw' AS source_table,
        src._batch_id AS source_batch_id,
        src._record_hash AS source_record_hash
    FROM {db_name}.stock_basic_raw src
    LEFT JOIN calendar_map ON calendar_map.calendar_date = toDate(src._ingest_time)
    WHERE src.ts_code IS NOT NULL

    UNION ALL

    SELECT
        concat('index:', src.ts_code) AS instrument_id,
        'index' AS instrument_type,
        arrayElement(splitByChar('.', src.ts_code), 2) AS exchange,
        src.ts_code AS source_code,
        CAST(NULL, 'Nullable(String)') AS symbol,
        src.name AS instrument_name,
        src.fullname AS full_name,
        CAST(NULL, 'Nullable(String)') AS english_name,
        src.market AS market,
        src.category AS category,
        src.publisher AS publisher,
        CAST(NULL, 'Nullable(String)') AS currency,
        CAST(NULL, 'Nullable(String)') AS list_status,
        src.list_date AS list_date,
        src.exp_date AS delist_date,
        CAST(NULL, 'Nullable(String)') AS area,
        CAST(NULL, 'Nullable(String)') AS industry,
        CAST(NULL, 'Nullable(String)') AS is_hs,
        CAST(NULL, 'Nullable(String)') AS underlying_code,
        CAST(NULL, 'Nullable(Float64)') AS contract_multiplier,
        CAST(NULL, 'Nullable(String)') AS trade_unit,
        CAST(NULL, 'Nullable(String)') AS quote_unit,
        coalesce(src.list_date, toDate(src._ingest_time)) AS event_date,
        coalesce(src.list_date, calendar_map.next_trade_date, toDate(src._ingest_time)) AS available_trade_date,
        src._ingest_time AS sys_from,
        src._source AS source,
        'index_basic_raw' AS source_table,
        src._batch_id AS source_batch_id,
        src._record_hash AS source_record_hash
    FROM {db_name}.index_basic_raw src
    LEFT JOIN calendar_map ON calendar_map.calendar_date = toDate(src._ingest_time)
    WHERE src.ts_code IS NOT NULL

    UNION ALL

    SELECT
        concat('future:', src.ts_code) AS instrument_id,
        'future' AS instrument_type,
        src.exchange AS exchange,
        src.ts_code AS source_code,
        src.symbol AS symbol,
        src.name AS instrument_name,
        CAST(NULL, 'Nullable(String)') AS full_name,
        CAST(NULL, 'Nullable(String)') AS english_name,
        CAST(NULL, 'Nullable(String)') AS market,
        CAST(NULL, 'Nullable(String)') AS category,
        CAST(NULL, 'Nullable(String)') AS publisher,
        CAST(NULL, 'Nullable(String)') AS currency,
        CAST(NULL, 'Nullable(String)') AS list_status,
        src.list_date AS list_date,
        src.delist_date AS delist_date,
        CAST(NULL, 'Nullable(String)') AS area,
        CAST(NULL, 'Nullable(String)') AS industry,
        CAST(NULL, 'Nullable(String)') AS is_hs,
        src.fut_code AS underlying_code,
        src.multiplier AS contract_multiplier,
        src.trade_unit AS trade_unit,
        src.quote_unit AS quote_unit,
        coalesce(src.list_date, toDate(src._ingest_time)) AS event_date,
        coalesce(src.list_date, calendar_map.next_trade_date, toDate(src._ingest_time)) AS available_trade_date,
        src._ingest_time AS sys_from,
        src._source AS source,
        'fut_basic_raw' AS source_table,
        src._batch_id AS source_batch_id,
        src._record_hash AS source_record_hash
    FROM {db_name}.fut_basic_raw src
    LEFT JOIN calendar_map ON calendar_map.calendar_date = toDate(src._ingest_time)
    WHERE src.ts_code IS NOT NULL
),
versioned AS (
    SELECT
        src.*,
        leadInFrame(src.sys_from, 1, {FAR_FUTURE_TS_SQL}) OVER (
            PARTITION BY src.instrument_id
            ORDER BY src.sys_from, src.source_batch_id, src.source_record_hash
        ) AS sys_to
    FROM (
        SELECT
            src.*,
            lagInFrame(src.source_record_hash) OVER (
                PARTITION BY src.instrument_id
                ORDER BY src.sys_from, src.source_batch_id, src.source_record_hash
            ) AS prev_record_hash
        FROM security_union src
    ) src
    WHERE src.prev_record_hash IS NULL OR src.prev_record_hash != src.source_record_hash
)
SELECT
    instrument_id,
    instrument_type,
    exchange,
    source_code,
    symbol,
    instrument_name,
    full_name,
    english_name,
    market,
    category,
    publisher,
    currency,
    list_status,
    list_date,
    delist_date,
    area,
    industry,
    is_hs,
    underlying_code,
    contract_multiplier,
    trade_unit,
    quote_unit,
    event_date,
    available_trade_date,
    sys_from,
    sys_to,
    source,
    source_table,
    source_batch_id,
    source_record_hash
FROM versioned
"""

    def _render_stock_factor_bar_sync_sql(self, spec: dict[str, Any], target_table_name: str) -> str:
        db_name = self.settings.database.db_name
        return f"""
INSERT INTO {db_name}.{target_table_name}
SELECT
    price.instrument_id AS instrument_id,
    price.instrument_type AS instrument_type,
    price.exchange AS exchange,
    price.source_code AS source_code,
    price.event_date AS event_date,
    greatest(price.available_trade_date, daily_basic.available_trade_date) AS available_trade_date,
    price.open AS open,
    price.high AS high,
    price.low AS low,
    price.close AS close,
    price.vol AS volume,
    price.amount AS amount,
    daily_basic.turnover_rate AS turnover,
    daily_basic.turnover_rate_f AS turnover_free_float,
    daily_basic.volume_ratio AS volume_ratio,
    greatest(price.sys_from, daily_basic.sys_from) AS sys_from,
    least(price.sys_to, daily_basic.sys_to) AS sys_to,
    'derived' AS source,
    '{",".join(STOCK_FACTOR_BAR_SOURCES)}' AS source_table,
    concat(coalesce(price.source_batch_id, ''), '|', coalesce(daily_basic.source_batch_id, '')) AS source_batch_id,
    lower(hex(MD5(concat(price.source_record_hash, '|', daily_basic.source_record_hash)))) AS source_record_hash
FROM {db_name}.dwd_stock_eod_price price
INNER JOIN {db_name}.dwd_stock_daily_basic daily_basic
    ON daily_basic.instrument_id = price.instrument_id
   AND daily_basic.event_date = price.event_date
   AND price.sys_from < daily_basic.sys_to
   AND daily_basic.sys_from < price.sys_to
WHERE least(price.sys_to, daily_basic.sys_to) > greatest(price.sys_from, daily_basic.sys_from)
"""

    def render_sync_sql(self, table_name: str, target_table_name: str | None = None) -> str:
        spec = self.load_spec(table_name)
        target_table_name = target_table_name or spec["name"]
        if spec.get("builder", "raw_versioned") == "security_master":
            return self._render_security_master_sync_sql(spec, target_table_name)
        if spec.get("builder") == "stock_factor_bar":
            return self._render_stock_factor_bar_sync_sql(spec, target_table_name)
        return self._render_generic_sync_sql(spec, target_table_name)

    def get_required_source_tables(self, spec: dict[str, Any]) -> list[str]:
        if spec.get("builder", "raw_versioned") == "security_master":
            return ["stock_basic_raw", "index_basic_raw", "fut_basic_raw", CALENDAR_SOURCE_TABLE]

        if spec.get("builder") == "stock_factor_bar":
            return STOCK_FACTOR_BAR_SOURCES

        required_tables = [spec["source"]["table_name"]]
        if spec.get("calendar_date_expr"):
            required_tables.append(CALENDAR_SOURCE_TABLE)
        return sorted(set(required_tables))

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
                "Run the corresponding ODS ingestion first."
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

    def sync_table(self, table_name: str) -> None:
        spec = self.load_spec(table_name)
        self.ensure_source_tables(spec)
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

        if self.settings.database.db_type == "clickhouse":
            self._replace_clickhouse_table_from_tmp(target_table, tmp_table)
            return

        db_engine.create_table(target_table, schema)
        db_engine.query(f"TRUNCATE TABLE {db_name}.{target_table}")
        db_engine.query(f"INSERT INTO {db_name}.{target_table} SELECT * FROM {db_name}.{tmp_table}")
        db_engine.query(f"DROP TABLE IF EXISTS {db_name}.{tmp_table}")

    def sync_all(self) -> None:
        for table_name in self.list_tables():
            self.sync_table(table_name)
