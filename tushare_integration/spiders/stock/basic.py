import datetime
import pandas as pd

from tushare_integration.spiders.tushare import DailySpider, TSCodeSpider, TushareSpider
from tushare_integration.items import TushareIntegrationItem


class StockBasicSpider(TushareSpider):
    name = "stock/basic/stock_basic"
    description = '股票列表'
    api_name = "stock_basic"

    def start_requests(self):
        exchanges = ["SSE", "SZSE", "BSE"]
        list_statuses = ["L", "D", "P", "G"]

        for exchange in exchanges:
            for list_status in list_statuses:
                params = {"exchange": exchange, "list_status": list_status}
                yield self.get_scrapy_request(params)


class StockNameChangeSpider(TushareSpider):
    name = "stock/basic/namechange"
    description = '股票名称变更'
    api_name = "namechange"

    def start_requests(self):
        # 不能用start_date和end_date筛选，部分数据没有ann_date导致无法完整同步数据
        # 每次拉5000条数据
        request = self.get_scrapy_request(params={'offset': 0, 'limit': 5000})
        request.meta["offset"] = 0
        request.meta["limit"] = 5000
        yield request

    def parse(self, response, **kwargs):
        first_page = self.parse_response(response, **kwargs)
        if first_page["data"].empty:
            return None

        all_data = [first_page["data"]]
        offset = response.meta["offset"] + response.meta["limit"]
        limit = response.meta["limit"]

        while True:
            parsed_data = self.request_with_requests(params={'offset': offset, 'limit': limit})
            if parsed_data["data"].empty:
                break
            all_data.append(parsed_data["data"])
            offset += limit

        return TushareIntegrationItem(data=pd.concat(all_data, ignore_index=True))


class StockHSConstSpider(TushareSpider):
    name = "stock/basic/hs_const"
    description = '沪深股通成份股'
    api_name = "hs_const"

    def start_requests(self):
        for hs_type in ["SH", "SZ"]:
            params = {"hs_type": hs_type}
            yield self.get_scrapy_request(params)


class StockSTSpider(DailySpider):
    name = "stock/basic/stock_st"
    description = 'ST股票列表'
    api_name = "stock_st"
    custom_settings = {"TABLE_NAME": "stock_st", "MIN_CAL_DATE": "2016-01-01"}


class STSpider(TSCodeSpider):
    name = "stock/basic/st"
    description = 'ST风险警示板股票'
    api_name = "st"
    custom_settings = {"TABLE_NAME": "st", "BASIC_TABLE": "stock_basic"}


class StockHSGTSpider(DailySpider):
    name = "stock/basic/stock_hsgt"
    description = '沪深港通股票列表'
    api_name = "stock_hsgt"
    custom_settings = {"TABLE_NAME": "stock_hsgt", "MIN_CAL_DATE": "2025-08-12"}
    stock_hsgt_types = ["HK_SZ", "SZ_HK", "HK_SH", "SH_HK"]

    def start_requests(self):
        min_cal_date = self.custom_settings.get("MIN_CAL_DATE", '1970-01-01')
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        table_name = self.get_table_name()

        existing_data = conn.query_df(
            f"""
                SELECT DISTINCT trade_date, type
                FROM {db_name}.{table_name}
                """
        )
        existing_keys = set()
        if not existing_data.empty:
            existing_keys = {
                (trade_date.strftime("%Y%m%d"), hsgt_type)
                for trade_date, hsgt_type in existing_data[["trade_date", "type"]].itertuples(index=False)
            }

        cal_dates = conn.query_df(
            f"""
                SELECT DISTINCT cal_date
                FROM {db_name}.trade_cal
                WHERE is_open = 1
                  AND cal_date >= '{min_cal_date}'
                  AND cal_date <= today()
                  AND exchange = 'SSE'
                ORDER BY cal_date
                """
        )

        for cal_date in cal_dates["cal_date"]:
            trade_date = cal_date.strftime("%Y%m%d")
            for hsgt_type in self.stock_hsgt_types:
                if (trade_date, hsgt_type) in existing_keys:
                    continue
                yield self.get_scrapy_request(params={"trade_date": trade_date, "type": hsgt_type})


class BSEMappingSpider(TushareSpider):
    name = "stock/basic/bse_mapping"
    description = '北交所新旧代码对照'
    api_name = "bse_mapping"
    custom_settings = {"TABLE_NAME": "bse_mapping"}


class TradeCalSpider(TushareSpider):
    name = "stock/basic/trade_cal"
    api_name = "trade_cal"
    description = '交易日历'
    custom_settings = {"TABLE_NAME": "trade_cal"}

    def start_requests(self):
        for exchange in ["SSE", "SZSE", "CFFEX", "DCE", "CZCE", "SHFE", "INE"]:
            params = {"exchange": exchange}
            yield self.get_scrapy_request(params)


class StockCompanySpider(TushareSpider):
    name = "stock/basic/stock_company"
    description = '上市公司基本信息'
    api_name = "stock_company"

    def start_requests(self):
        for exchange in ["SSE", "SZSE", "BSE"]:
            params = {"exchange": exchange}
            yield self.get_scrapy_request(params)


class StockManagers(TSCodeSpider):
    name = "stock/basic/stk_managers"
    description = '上市公司管理层'
    api_name = "stk_managers"


class StockRewards(TSCodeSpider):
    name = "stock/basic/stk_rewards"
    description = '管理层薪酬和持股'
    api_name = "stk_rewards"


class StockNewShareSpider(TushareSpider):
    name = "stock/basic/new_share"
    description = 'IPO新股上市'
    api_name = "new_share"

    def start_requests(self):
        # 用start_date和end_date筛选，每次拉取一年的数据
        for year in range(2010, datetime.datetime.now().year + 1, 5):
            params = {"start_date": str(year) + "0101", "end_date": str(year + 5) + "1231"}
            yield self.get_scrapy_request(params)


class StkPremarket(DailySpider):
    name = "stock/basic/stk_premarket"
    description = '每日股本(盘前数据)'
    api_name = "stk_premarket"
    custom_settings = {"TABLE_NAME": "stk_premarket"}


class StockBakBasicSpider(DailySpider):
    name = "stock/basic/bak_basic"
    description = '备用列表'
    api_name = "bak_basic"
    custom_settings = {"MIN_CAL_DATE": "2016-08-01"}
