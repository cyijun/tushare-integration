import json
import unittest
from types import SimpleNamespace

import pandas as pd

from tushare_integration.spiders.stock.market import DCIndexSpider
from tushare_integration.spiders.stock.quotes import StockDailySpider


class DummyDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def query_df(self, sql):
        self.queries.append(sql)
        return self.responses.pop(0)


class IncrementalPolicyTest(unittest.TestCase):
    @staticmethod
    def _settings(backfill_days=0, default_min_cal_date="2010-01-01"):
        return SimpleNamespace(
            tushare_url="https://api.tushare.pro",
            tushare_token="token",
            incremental_backfill_days=backfill_days,
            default_min_cal_date=default_min_cal_date,
            database=SimpleNamespace(db_name="default"),
        )

    @staticmethod
    def _request_params(request):
        return json.loads(request.body.decode("utf-8"))["params"]

    def test_daily_spider_uses_latest_date_with_configured_backfill_window(self):
        spider = StockDailySpider()
        spider.spider_settings = self._settings(backfill_days=7)
        spider.db_engine = DummyDB(
            [
                pd.DataFrame({"row_count": [10], "latest_trade_date": [pd.Timestamp("2026-05-08")]}),
                pd.DataFrame({"cal_date": pd.to_datetime(["2026-05-11"])}),
            ]
        )

        requests = list(spider.start_requests())

        self.assertEqual([self._request_params(request) for request in requests], [{"trade_date": "20260511"}])
        self.assertIn("cal_date >= '2026-05-01'", spider.db_engine.queries[-1])
        self.assertNotIn("1970-01-01", spider.db_engine.queries[-1])

    def test_empty_daily_table_uses_configured_default_when_min_cal_date_missing(self):
        spider = StockDailySpider()
        spider.custom_settings = {"TABLE_NAME": "daily"}
        spider.spider_settings = self._settings(default_min_cal_date="2024-01-01")
        spider.db_engine = DummyDB(
            [
                pd.DataFrame({"row_count": [0], "latest_trade_date": [pd.NaT]}),
                pd.DataFrame({"cal_date": pd.to_datetime(["2024-01-02"])}),
            ]
        )

        requests = list(spider.start_requests())

        self.assertEqual([self._request_params(request) for request in requests], [{"trade_date": "20240102"}])
        self.assertIn("cal_date >= '2024-01-01'", spider.db_engine.queries[-1])
        self.assertNotIn("1970-01-01", spider.db_engine.queries[-1])

    def test_daily_type_spider_uses_dimension_high_watermark_not_full_history(self):
        spider = DCIndexSpider()
        spider.spider_settings = self._settings(backfill_days=7)
        spider.db_engine = DummyDB(
            [
                pd.DataFrame({"row_count": [100], "latest_trade_date": [pd.Timestamp("2026-05-11")]}),
                pd.DataFrame({"row_count": [100], "latest_trade_date": [pd.Timestamp("2026-05-11")]}),
                pd.DataFrame({"row_count": [100], "latest_trade_date": [pd.Timestamp("2026-05-11")]}),
                pd.DataFrame(columns=["trade_date", "idx_type"]),
                pd.DataFrame({"cal_date": pd.to_datetime(["2026-05-05"])}),
            ]
        )

        requests = list(spider.start_requests())

        self.assertEqual(
            [self._request_params(request) for request in requests],
            [
                {"trade_date": "20260505", "idx_type": "行业板块"},
                {"trade_date": "20260505", "idx_type": "概念板块"},
                {"trade_date": "20260505", "idx_type": "地域板块"},
            ],
        )
        self.assertIn("cal_date >= '2026-05-04'", spider.db_engine.queries[-1])
        self.assertNotIn("1990-12-19", "\n".join(spider.db_engine.queries))


if __name__ == "__main__":
    unittest.main()
