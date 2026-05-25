# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html
import datetime
import hashlib
import json
import logging

import pandas as pd
import yaml
from scrapy.exceptions import DropItem

from tushare_integration.db_engine import DatabaseEngineFactory
from tushare_integration.settings import TushareIntegrationSettings
from tushare_integration.storage import build_latest_schema, build_raw_schema


class BasePipeline(object):
    def __init__(self, settings: TushareIntegrationSettings, *args, **kwargs):
        self.settings: TushareIntegrationSettings = settings
        self.schema: dict = {}

    def get_schema(self, schema: str):
        with open(f"tushare_integration/schema/{schema}.yaml", "r", encoding="utf-8") as f:
            self.schema = yaml.safe_load(f.read())

        return self.schema

    def open_spider(self, spider):
        self.schema = self.get_schema(spider.get_schema_name())

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            settings=TushareIntegrationSettings.model_validate(
                yaml.safe_load(open('config.yaml', 'r', encoding='utf8').read())
            )
        )


class TushareIntegrationFillNAPipeline(BasePipeline):
    @staticmethod
    def get_default_by_data_type(data_type: str):
        if data_type is None:
            raise ValueError("data_type is None")

        match data_type:
            case "str":
                return ""
            case "float":
                return 0.0
            case "int":
                return 0
            case "number":
                return 0.0
            case "date":
                return "1970-01-01"
            case "datetime":
                return "1970-01-01 00:00:00"
            case 'json':
                return '{}'
            case _:
                raise ValueError(f"Unsupported data_type: {data_type}")

    def process_item(self, item, spider):
        data: pd.DataFrame = item["data"]

        if data is None or len(data) == 0:
            raise DropItem()

        item["raw_data"] = data.copy(deep=True)
        item["latest_data"] = data.copy(deep=True)

        for column in self.schema["columns"]:
            default = column.get("default") or self.get_default_by_data_type(column["data_type"])
            # 需要特殊处理NaT,Pandas的fillna方法不支持NaT
            item["latest_data"][column["name"]] = (
                item["latest_data"][column["name"]].replace({pd.NaT: None}).fillna(default)
            )

        return item


class TransformDTypePipeline(BasePipeline):
    @staticmethod
    def is_nullish(value) -> bool:
        if value is None or value is pd.NaT:
            return True
        try:
            return bool(pd.isna(value))
        except (TypeError, ValueError):
            return False

    @classmethod
    def to_json_string(cls, value, default: str | None = None):
        if cls.is_nullish(value):
            return default
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def convert_series(cls, series: pd.Series, column: dict, preserve_nulls: bool) -> pd.Series:
        column_name = column["name"]
        data_type = column["data_type"]
        default_value = column.get("default", TushareIntegrationFillNAPipeline.get_default_by_data_type(data_type))

        match data_type:
            case "str":
                if preserve_nulls:
                    return series.map(lambda value: None if cls.is_nullish(value) else str(value))
                return series.astype(str)
            case "float" | "number":
                numeric_series = pd.to_numeric(series, errors="coerce")
                if preserve_nulls:
                    return numeric_series.astype(object).where(numeric_series.notna(), None)
                return numeric_series.fillna(float(default_value)).astype(float)
            case "int":
                numeric_series = pd.to_numeric(series, errors="coerce")
                if preserve_nulls:
                    return numeric_series.map(lambda value: None if cls.is_nullish(value) else int(value))
                return numeric_series.fillna(int(default_value)).astype('Int64')
            case "date":
                date_series = pd.to_datetime(series, format="mixed", errors="coerce").dt.date
                if preserve_nulls:
                    return date_series.astype(object).where(pd.notna(date_series), None)
                default_date = pd.to_datetime(default_value).date()
                return date_series.astype(object).where(pd.notna(date_series), default_date)
            case "datetime":
                datetime_series = pd.to_datetime(series, format="mixed", errors="coerce")
                if preserve_nulls:
                    return datetime_series.astype(object).where(datetime_series.notna(), None)
                return datetime_series.fillna(pd.to_datetime(default_value))
            case "json":
                default_json = default_value if default_value is not None else "{}"
                return series.map(lambda value: cls.to_json_string(value, None if preserve_nulls else default_json))
            case _:
                raise ValueError(f"Unsupported data_type: {column_name}/{data_type}")

    def process_item(self, item, spider):
        for column in self.schema["columns"]:
            item["raw_data"][column["name"]] = self.convert_series(
                item["raw_data"][column["name"]], column, preserve_nulls=True
            )
            item["latest_data"][column["name"]] = self.convert_series(
                item["latest_data"][column["name"]], column, preserve_nulls=False
            )
        return item


class TushareIntegrationDataPipeline(BasePipeline):
    def __init__(self, settings, *args, **kwargs) -> None:
        super().__init__(settings, *args, **kwargs)

        self.db_engine = DatabaseEngineFactory.create(self.settings)

        self.table_name: str = ""
        self.raw_table_name: str = ""
        self.latest_schema: dict = {}
        self.raw_schema: dict = {}
        self.business_columns: list[str] = []
        self.truncate: bool = False

    def open_spider(self, spider):
        super().open_spider(spider)
        self.table_name = spider.get_latest_table_name()
        self.raw_table_name = spider.get_raw_table_name()
        self.latest_schema = build_latest_schema(self.schema)
        self.raw_schema = build_raw_schema(self.schema)
        self.business_columns = [column["name"] for column in self.schema["columns"]]

    @staticmethod
    def normalize_scalar(value):
        if TransformDTypePipeline.is_nullish(value):
            return None
        if hasattr(value, "item") and callable(value.item):
            value = value.item()
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return value

    @classmethod
    def build_row_payload(cls, row: pd.Series, columns: list[str]) -> dict:
        return {column: cls.normalize_scalar(row[column]) for column in columns}

    @classmethod
    def build_record_hash(cls, row: pd.Series, columns: list[str]) -> str:
        row_values = [cls.normalize_scalar(row[column]) for column in columns]
        row_text = json.dumps(row_values, ensure_ascii=False, separators=(",", ":"), default=str)
        return hashlib.md5(row_text.encode("utf-8")).hexdigest()

    def add_metadata_columns(
        self,
        data: pd.DataFrame,
        spider,
        ingest_time: datetime.datetime,
        batch_id: str,
        record_hashes: pd.Series,
        raw_payloads: pd.Series | None = None,
    ) -> pd.DataFrame:
        enriched_data = data.copy(deep=True)
        enriched_data["_source"] = spider.get_source_name()
        enriched_data["_api_name"] = spider.get_api_name()
        enriched_data["_batch_id"] = batch_id
        enriched_data["_ingest_time"] = ingest_time
        enriched_data["_record_hash"] = record_hashes

        if raw_payloads is not None:
            enriched_data["_raw_json"] = raw_payloads

        return enriched_data

    def process_item(self, item, spider):
        raw_data: pd.DataFrame = item["raw_data"]
        latest_data: pd.DataFrame = item["latest_data"]

        if raw_data.empty:
            return item

        ingest_time = datetime.datetime.now()
        batch_id = spider.settings.get("BATCH_ID", "") or self.settings.batch_id
        record_hashes = raw_data.apply(lambda row: self.build_record_hash(row, self.business_columns), axis=1)
        raw_payloads = raw_data.apply(
            lambda row: json.dumps(
                self.build_row_payload(row, self.business_columns),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
            axis=1,
        )

        raw_data = self.add_metadata_columns(
            raw_data,
            spider,
            ingest_time=ingest_time,
            batch_id=batch_id,
            record_hashes=record_hashes,
            raw_payloads=raw_payloads,
        )
        latest_data = self.add_metadata_columns(
            latest_data,
            spider,
            ingest_time=ingest_time,
            batch_id=batch_id,
            record_hashes=record_hashes,
        )

        logging.debug(f"Insert raw data into {self.raw_table_name}, data count: {len(raw_data)}")
        self.db_engine.insert(self.raw_table_name, schema=self.raw_schema, data=raw_data)

        if (primary_key := self.schema.get("primary_key", None)) is not None and len(primary_key) > 0:
            latest_data = latest_data.drop_duplicates(subset=primary_key, keep="last")
            self.db_engine.upsert(self.table_name, schema=self.latest_schema, data=latest_data)
        else:
            logging.debug(f"Insert latest data into {self.table_name}, data count: {len(latest_data)}")
            self.db_engine.insert(self.table_name, schema=self.latest_schema, data=latest_data)

        return item


    def close_spider(self, spider):
        self.db_engine.close()


class RecordLogPipeline(BasePipeline):
    def __init__(self, settings, *args, **kwargs) -> None:
        super().__init__(settings, *args, **kwargs)

        self.db_engine = DatabaseEngineFactory.create(self.settings)

        self.table_name: str = "tushare_integration_log"

        self.count: int = 0
        self.start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_schema = self.get_log_schema()
        self.create_log_table()

    @staticmethod
    def get_log_schema():
        return {
            'primary_key': ['batch_id'],
            'columns': [
                {
                    'name': 'batch_id',
                    'data_type': 'str',
                    'comment': '批次ID',
                },
                {
                    'name': 'spider_name',
                    'data_type': 'str',
                    'comment': '爬虫名称',
                },
                {
                    'name': 'description',
                    'data_type': 'str',
                    'comment': '描述',
                },
                {
                    'name': 'count',
                    'data_type': 'int',
                    'comment': '数量',
                },
                {
                    'name': 'start_time',
                    'data_type': 'datetime',
                    'comment': '开始时间',
                },
                {
                    'name': 'end_time',
                    'data_type': 'datetime',
                    'comment': '结束时间',
                },
            ],
        }

    def create_log_table(self):
        self.db_engine.create_table(self.table_name, self.log_schema)

    def open_spider(self, spider):
        super().open_spider(spider)
        self.start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def close_spider(self, spider):
        statistics_data = pd.DataFrame(
            [
                {
                    "batch_id": spider.settings.get("BATCH_ID", ''),
                    "spider_name": spider.name,
                    "description": self.schema.get("name", ""),
                    "count": self.count,
                    "start_time": self.start_time,
                    "end_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            ]
        )

        statistics_data[['start_time', 'end_time']] = statistics_data[['start_time', 'end_time']].apply(pd.to_datetime)

        self.db_engine.insert(self.table_name, self.log_schema, statistics_data)
        self.db_engine.close()

    def process_item(self, item, spider):
        self.count += len(item["data"])
        return item
