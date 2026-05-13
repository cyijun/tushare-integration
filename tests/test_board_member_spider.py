import json
import unittest
from types import SimpleNamespace

import pandas as pd

from tushare_integration.spiders.stock.market import DCConceptConsSpider, DCMemberSpider


class DummyDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def query_df(self, sql):
        self.queries.append(sql)
        return self.responses.pop(0)


class BoardMemberSpiderTest(unittest.TestCase):
    @staticmethod
    def _settings():
        return SimpleNamespace(
            tushare_url="https://api.tushare.pro",
            tushare_token="token",
            database=SimpleNamespace(db_name="default"),
        )

    @staticmethod
    def _request_params(request):
        return json.loads(request.body.decode("utf-8"))["params"]

    def test_dc_member_requests_only_existing_board_date_pairs(self):
        spider = DCMemberSpider()
        spider.spider_settings = self._settings()
        spider.db_engine = DummyDB(
            [
                pd.DataFrame(
                    {
                        "trade_date": [pd.Timestamp("2024-12-20")],
                        "board_code": ["BK0837.DC"],
                    }
                ),
                pd.DataFrame(
                    {
                        "trade_date": [
                            pd.Timestamp("2024-12-20"),
                            pd.Timestamp("2024-12-20"),
                            pd.Timestamp("2024-12-23"),
                        ],
                        "board_code": ["BK0837.DC", "BK0838.DC", "BK0837.DC"],
                    }
                ),
            ]
        )

        requests = list(spider.start_requests())

        self.assertEqual(
            [self._request_params(request) for request in requests],
            [
                {"trade_date": "20241220", "ts_code": "BK0838.DC"},
                {"trade_date": "20241223", "ts_code": "BK0837.DC"},
            ],
        )
        self.assertEqual(len(spider.db_engine.queries), 2)
        self.assertIn("FROM default.dc_member", spider.db_engine.queries[0])
        self.assertIn("FROM default.dc_index", spider.db_engine.queries[1])
        self.assertNotIn("trade_cal", "\n".join(spider.db_engine.queries))

    def test_dc_concept_cons_uses_theme_code_pairs(self):
        spider = DCConceptConsSpider()
        spider.spider_settings = self._settings()
        spider.db_engine = DummyDB(
            [
                pd.DataFrame(columns=["trade_date", "board_code"]),
                pd.DataFrame(
                    {
                        "trade_date": [pd.Timestamp("2026-02-03")],
                        "board_code": ["TS001"],
                    }
                ),
            ]
        )

        requests = list(spider.start_requests())

        self.assertEqual(
            [self._request_params(request) for request in requests],
            [{"trade_date": "20260203", "theme_code": "TS001"}],
        )
        self.assertIn("`theme_code` AS board_code", spider.db_engine.queries[0])
        self.assertIn("`theme_code` AS board_code", spider.db_engine.queries[1])


if __name__ == "__main__":
    unittest.main()
