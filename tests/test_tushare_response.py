import json
import unittest

from tushare_integration.manager import CrawlManager
from tushare_integration.spiders.stock.special import CyqChipsSpider


class DummyResponse:
    def __init__(self, payload, params=None):
        self.text = json.dumps(payload, ensure_ascii=False)
        self.meta = {"params": params or {}}


class TushareResponseTest(unittest.TestCase):
    def test_parse_response_treats_common_no_data_message_as_empty_item(self):
        spider = CyqChipsSpider()
        response = DummyResponse(
            {
                "code": -2001,
                "msg": "抱歉，您查询的数据为空，请确认输入参数是否正确",
                "data": None,
            },
            params={"ts_code": "000001.SZ", "trade_date": "20180102"},
        )

        item = spider.parse_response(response)

        self.assertTrue(item["data"].empty)
        self.assertEqual(item["data"].columns.tolist(), ["ts_code", "trade_date", "price", "percent"])

    def test_parse_response_treats_success_without_data_payload_as_empty_item(self):
        spider = CyqChipsSpider()
        response = DummyResponse({"code": 0, "msg": "", "data": None})

        item = spider.parse_response(response)

        self.assertTrue(item["data"].empty)
        self.assertEqual(item["data"].columns.tolist(), ["ts_code", "trade_date", "price", "percent"])

    def test_manager_signal_description_includes_failure_message(self):
        class DummyFailure:
            value = RuntimeError("api failed")

        detail = CrawlManager.describe_signal({"signal": object(), "spider": None, "failure": DummyFailure()})

        self.assertIn("api failed", detail)


if __name__ == "__main__":
    unittest.main()
