import datetime
import json
import logging
import threading
import time

import pandas as pd
import requests
import scrapy
import yaml

from tushare_integration.db_engine import DatabaseEngineFactory, DBEngine
from tushare_integration.items import TushareIntegrationItem
from tushare_integration.settings import TushareIntegrationSettings
from tushare_integration.storage import (
    build_latest_schema,
    build_raw_schema,
    get_latest_table_name,
    get_raw_table_name,
)


TUSHARE_EMPTY_DATA_MESSAGE_FRAGMENTS = (
    "查询数据失败，请确认参数",
    "指定数据不存在",
    "查询的数据为空",
    "数据为空",
    "没有数据",
    "暂无数据",
    "无数据",
)


class TushareRequestRateLimiter:
    _lock = threading.Lock()
    _last_request_at = 0.0

    @classmethod
    def wait(cls, min_interval: float):
        if min_interval <= 0:
            return

        with cls._lock:
            now = time.monotonic()
            sleep_seconds = cls._last_request_at + min_interval - now
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
                now = time.monotonic()
            cls._last_request_at = now


class TushareSpider(scrapy.Spider):
    name: str
    api_name: str
    schema: dict = {}
    latest_schema: dict = {}
    raw_schema: dict = {}
    spider_settings: TushareIntegrationSettings  # 不能直接叫settings，会覆盖掉scrapy的settings
    db_engine: DBEngine

    custom_settings: dict = {}

    def __init__(self, name=None, **kwargs):
        super().__init__(name, **kwargs)
        self.schema = self.get_schema()
        self.latest_schema = build_latest_schema(self.schema)
        self.raw_schema = build_raw_schema(self.schema)

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        with open('config.yaml', 'r', encoding='utf-8') as f:
            spider.spider_settings = TushareIntegrationSettings.model_validate(
                yaml.safe_load(f.read())
            )
        spider.create_table()
        return spider

    def create_table(self):
        logging.info(f"quest {self.name}: create table {self.get_latest_table_name()} and {self.get_raw_table_name()}")

        self.db_engine = DatabaseEngineFactory.create(self.spider_settings)
        self.db_engine.create_table(self.get_latest_table_name(), self.latest_schema)
        self.db_engine.create_table(self.get_raw_table_name(), self.raw_schema)

    def get_schema(self):
        with open(f"tushare_integration/schema/{self.get_schema_name()}.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f.read())

    def start_requests(self):
        yield self.get_scrapy_request()

    def parse(self, response, **kwargs):
        item = self.parse_response(response, **kwargs)

        if item['data'] is None or len(item['data']) == 0:
            return

        return item

    def parse_response(self, response, **kwargs):
        try:
            resp = json.loads(response.text)
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON from {self.get_api_name()}: {response.text[:200]}")
            raise RuntimeError(f"Non-JSON response: {e}")

        if resp.get("code") != 0:
            msg = resp.get("msg", "")
            if self.is_empty_data_response(resp):
                logging.info(
                    f"Request {self.get_api_name()} returned no data: {msg}, "
                    f"params: {getattr(response, 'meta', {}).get('params', {})}"
                )
                return self.build_empty_item()

            logging.error(f"Request {self.get_api_name()} failed: {msg}")
            raise RuntimeError(msg)

        data = resp.get("data") or {}
        fields = data.get("fields") or self.load_fields().split(",")
        items = data.get("items") or []

        return TushareIntegrationItem(data=pd.DataFrame(data=items, columns=fields))

    @staticmethod
    def is_empty_data_response(resp: dict) -> bool:
        msg = str(resp.get("msg", ""))
        return any(fragment in msg for fragment in TUSHARE_EMPTY_DATA_MESSAGE_FRAGMENTS)

    def build_empty_item(self):
        return TushareIntegrationItem(data=pd.DataFrame(columns=self.load_fields().split(",")))

    def get_db_engine(self):
        return self.db_engine

    def get_scrapy_request(self, params: dict | None = None, meta: dict | None = None):
        if not params:
            params = {}

        if not meta:
            meta = {}

        logging.info(f"Requesting {self.get_api_name()} with params: {params}")

        return scrapy.Request(
            url=self.spider_settings.tushare_url,
            method="POST",
            body=json.dumps(
                {
                    "api_name": self.get_api_name(),
                    "token": self.spider_settings.tushare_token,
                    "params": params,
                    "fields": self.load_fields(),
                }
            ),
            headers={
                "Content-Type": "application/json",
            },
            meta={
                'api_name': self.get_api_name(),
                'params': params,
            }
            | meta,
        )

    def wait_for_rate_limit(self):
        spider_settings = getattr(self, "spider_settings", None)
        get_download_delay = getattr(spider_settings, "get_download_delay", None)
        if get_download_delay:
            TushareRequestRateLimiter.wait(get_download_delay())

    @staticmethod
    def is_rate_limit_response(response) -> bool:
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return False

        try:
            code = int(payload.get("code", 0))
        except (TypeError, ValueError):
            return False
        return code // 100 == 402

    # 搞个函数，直接使用requests发起请求
    def request_with_requests(self, params: dict | None = None, meta: dict | None = None, timeout: float = 60.0) -> TushareIntegrationItem:
        retry_times = getattr(self.spider_settings, "retry_times", 0)
        retry_delay = max(60, getattr(self.spider_settings, "retry_delay", 60))

        for retry_count in range(retry_times + 1):
            logging.info(f"Requesting {self.get_api_name()} with params: {params}")
            self.wait_for_rate_limit()
            response = requests.post(
                url=self.spider_settings.tushare_url,
                json={
                    "api_name": self.get_api_name(),
                    "token": self.spider_settings.tushare_token,
                    "params": params,
                    "fields": self.load_fields(),
                },
                headers={
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )

            if self.is_rate_limit_response(response) and retry_count < retry_times:
                logging.warning(
                    f"Request {self.get_api_name()} hit rate limit, retrying after {retry_delay} seconds: {params}"
                )
                time.sleep(retry_delay)
                continue

            return self.parse_response(response)

        raise RuntimeError(f"Request {self.get_api_name()} failed before receiving a response")

    def load_fields(self):
        return ",".join([column["name"] for column in self.schema["columns"]])

    def get_schema_name(self):
        return self.name

    def get_api_name(self):
        if hasattr(self, 'api_name') and self.api_name:
            return self.api_name
        return self.name.split("/")[-1]

    def get_source_name(self) -> str:
        return "tushare"

    def get_latest_schema(self) -> dict:
        return self.latest_schema

    def get_raw_schema(self) -> dict:
        return self.raw_schema

    def get_latest_table_name(self) -> str:
        if self.custom_settings and self.custom_settings.get("TABLE_NAME"):
            return get_latest_table_name(self.custom_settings.get("TABLE_NAME", ""))
        return get_latest_table_name(self.name.split("/")[-1])

    def get_raw_table_name(self) -> str:
        return get_raw_table_name(self.get_latest_table_name())

    def get_table_name(self) -> str:
        return self.get_latest_table_name()


class DailySpider(TushareSpider):
    name: str
    custom_settings = {"TABLE_NAME": "daily", "TRADE_DATE_FIELD": "trade_date"}

    def get_trade_date_field(self) -> str:
        return self.custom_settings.get('TRADE_DATE_FIELD', 'trade_date')

    def get_min_cal_date(self) -> datetime.date:
        default_min_cal_date = getattr(self.spider_settings, "default_min_cal_date", '2010-01-01')
        min_cal_date = self.parse_date_value(self.custom_settings.get("MIN_CAL_DATE", default_min_cal_date))
        return min_cal_date or datetime.date(2010, 1, 1)

    def get_backfill_days(self) -> int:
        backfill_days = self.custom_settings.get(
            "BACKFILL_DAYS",
            getattr(self.spider_settings, "incremental_backfill_days", 0),
        )
        return max(0, int(backfill_days or 0))

    @staticmethod
    def parse_date_value(value) -> datetime.date | None:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    @staticmethod
    def format_trade_date(value) -> str:
        parsed = DailySpider.parse_date_value(value)
        if parsed is None:
            raise ValueError(f"Invalid trade date: {value}")
        return parsed.strftime("%Y%m%d")

    def get_incremental_start_date(
        self,
        conn,
        date_field: str | None = None,
        table_name: str | None = None,
        where_clause: str = "",
    ) -> datetime.date:
        date_field = date_field or self.get_trade_date_field()
        table_name = table_name or self.get_table_name()
        db_name = self.spider_settings.database.db_name
        min_cal_date = self.get_min_cal_date()

        latest_data = conn.query_df(
            f"""
                SELECT count() AS row_count, max(`{date_field}`) AS latest_trade_date
                FROM {db_name}.{table_name}
                {where_clause}
                """
        )
        if latest_data.empty or int(latest_data["row_count"].iloc[0]) == 0:
            return min_cal_date

        latest_trade_date = self.parse_date_value(latest_data["latest_trade_date"].iloc[0])
        if latest_trade_date is None:
            return min_cal_date

        start_date = latest_trade_date - datetime.timedelta(days=self.get_backfill_days())
        return max(min_cal_date, start_date)

    def start_requests(self):
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        trade_date_field = self.get_trade_date_field()
        start_date = self.get_incremental_start_date(conn, trade_date_field)

        cal_dates = conn.query_df(
            f"""
                SELECT DISTINCT cal_date
                FROM {db_name}.trade_cal
                WHERE cal_date NOT IN (
                    SELECT `{trade_date_field}` FROM {db_name}.{self.get_table_name()}
                    WHERE `{trade_date_field}` >= '{start_date}'
                )
                  AND is_open = 1
                  AND cal_date >= '{start_date}'
                  AND cal_date <= today()
                  AND exchange = 'SSE'
                ORDER BY cal_date
                """  # 期货交易日历共享同一张表，所以这里过滤SSE
        )

        if cal_dates.empty:
            return

        trade_dates = [cal_date.strftime("%Y%m%d") for cal_date in cal_dates["cal_date"]]

        for trade_date in trade_dates:
            yield self.get_scrapy_request(
                params={trade_date_field: trade_date}
            )


class TSCodeSpider(TushareSpider):
    name: str
    custom_settings = {'BASIC_TABLE': 'stock_basic'}

    def start_requests(self):
        table_name = self.custom_settings.get('BASIC_TABLE')
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name

        ts_codes = conn.query_df(f"SELECT ts_code FROM {db_name}.{table_name}")

        for ts_code in ts_codes['ts_code']:
            yield self.get_scrapy_request(params={"ts_code": ts_code})


class FinancialReportSpider(TushareSpider):
    name: str
    api_name = "financial_report"

    def start_requests(self):
        # 如果积分大于5000，使用vip接口
        if self.spider_settings.tushare_point >= 5000:
            return self.request_with_vip()
        else:
            return self.request_with_ts_code()

    @staticmethod
    def get_all_period():
        # 获取所有的period
        periods = []
        for year in range(2010, datetime.datetime.now().year + 1):
            for end_date in [f"{year}0331", f"{year}0630", f"{year}0930", f"{year}1231"]:
                periods.append(end_date)
        return periods

    def request_with_vip(self):
        # 每次全量同步即可，30年的数据只有4*30*12=1440次请求
        # 尽管实测半个小时同步完，但是毕竟离线数据，慢点也无妨，后期如果需要再进行优化

        if self.custom_settings.get('HAS_VIP', True):
            self.api_name = self.api_name + "_vip"
        for period in self.get_all_period():
            # 三大报表需要按照report_type分别请求
            if self.api_name.startswith(("income", "balance", "cashflow")):
                for report_type in range(1, 13):
                    params = {"period": period, "report_type": str(report_type)}
                    yield self.get_scrapy_request(params)
            else:
                # 其他报表只需要按period请求即可
                params = {"period": period}
                yield self.get_scrapy_request(params)

    def request_with_ts_code(self):
        # 按ts_code取数据，每次取一个股票的全量，几千次请求
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        ts_codes = conn.query_df(f' SELECT ts_code FROM {db_name}.stock_basic')['ts_code']

        for ts_code in ts_codes:
            params = {"ts_code": ts_code, "limit": 2000}
            yield self.get_scrapy_request(params)
