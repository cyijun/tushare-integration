import datetime

import pandas as pd

from tushare_integration.items import TushareIntegrationItem
from tushare_integration.spiders.stock.quotes import StockMonthlySpider, StockWeeklySpider
from tushare_integration.spiders.tushare import DailySpider, TushareSpider


class IndexDailySpider(DailySpider):
    name = "index/quotes/index_daily"
    custom_settings = {"TABLE_NAME": "index_daily", 'BASIC_TABLE': 'index_basic'}

    def start_requests(self):
        # index_daily需要特殊处理，这个接口不支持按日期获取数据，这意味着需要用ts_code去取
        # 接口一次性最大返回8000条数据，部分指数的数据量超过8000条，所以需要分批取
        # 到2024年最多的一年只有257个交易日，我们按一年260个交易日来计算，一次可以取30年的数据
        # 看了一下实际上现在就3000多个指数，全请求一次也就不到8000次，直接全量取吧
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        index_list = conn.query_df(f"select * from {db_name}.{self.custom_settings.get('BASIC_TABLE')}")

        if index_list.empty:
            return

        # 从base_date开始取，一次+30年
        for _, row in index_list.iterrows():
            ts_code = row["ts_code"]
            start_date = row["base_date"].date()
            end_date = start_date + datetime.timedelta(days=30 * 365)

            while True:
                if start_date > datetime.date.today():
                    break

                yield self.get_scrapy_request(
                    params={
                        "ts_code": ts_code,
                        "start_date": start_date.strftime("%Y%m%d"),
                        "end_date": end_date.strftime("%Y%m%d"),
                    }
                )
                start_date = end_date
                end_date = start_date + datetime.timedelta(days=30 * 365)


class DailyInfoSpider(DailySpider):
    name = "index/quotes/daily_info"
    custom_settings = {"TABLE_NAME": "daily_info", "MIN_CAL_DATE": "1990-12-19"}


# noinspection SpellCheckingInspection
class IndexDailyBasicSpider(DailySpider):
    name = "index/quotes/index_dailybasic"
    custom_settings = {"TABLE_NAME": "index_dailybasic", "MIN_CAL_DATE": "2004-01-02"}


class IndexGlobalSpider(DailySpider):
    name = "index/quotes/index_global"
    custom_settings = {"TABLE_NAME": "index_global", "MIN_CAL_DATE": "1990-12-19"}


class IndexMonthlySpider(StockMonthlySpider):
    name = "index/quotes/index_monthly"
    custom_settings = {"TABLE_NAME": "index_monthly"}


class IndexWeeklySpider(StockWeeklySpider):
    name = "index/quotes/index_weekly"
    custom_settings = {"TABLE_NAME": "index_weekly"}


class IndexWeightSpider(DailySpider):
    name = "index/quotes/index_weight"
    page_limit = 3000
    custom_settings = {
        "TABLE_NAME": "index_weight",
        "BASIC_TABLE": "index_basic",
        "MIN_CAL_DATE": "2005-04-08",  # 根据实际数据情况设置合适的起始日期
    }

    def start_requests(self):
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        start_date = self.get_incremental_start_date(conn, "trade_date")

        cal_dates = conn.query_df(
            f"""
                SELECT DISTINCT cal_date
                FROM {db_name}.trade_cal
                WHERE cal_date NOT IN (
                    SELECT trade_date FROM {db_name}.{self.get_table_name()}
                    WHERE trade_date >= '{start_date}'
                )
                  AND is_open = 1
                  AND cal_date >= '{start_date}'
                  AND cal_date <= today()
                  AND exchange = 'SSE'
                ORDER BY cal_date
                """
        )

        if cal_dates.empty:
            return

        for cal_date in cal_dates["cal_date"]:
            trade_date = cal_date.strftime("%Y%m%d")
            yield self.get_scrapy_request(
                params={'trade_date': trade_date, 'offset': 0, 'limit': self.page_limit},
                meta={'trade_date': trade_date, 'offset': 0, 'limit': self.page_limit, 'index_weight_pages': []},
            )

    def parse(self, response, **kwargs):
        page = self.parse_response(response, **kwargs)
        pages = response.meta.get('index_weight_pages', [])

        if page["data"].empty:
            if pages:
                yield TushareIntegrationItem(data=pd.concat(pages, ignore_index=True))
            return

        pages.append(page["data"])
        trade_date = response.meta['trade_date']
        offset = response.meta.get('offset', 0)
        limit = response.meta.get('limit', self.page_limit)

        if len(page["data"]) < limit:
            yield TushareIntegrationItem(data=pd.concat(pages, ignore_index=True))
            return

        next_offset = offset + limit
        yield self.get_scrapy_request(
            params={'trade_date': trade_date, 'offset': next_offset, 'limit': limit},
            meta={
                'trade_date': trade_date,
                'offset': next_offset,
                'limit': limit,
                'index_weight_pages': pages,
            },
        )


class SzDailyInfoSpider(DailySpider):
    name = "index/quotes/sz_daily_info"
    custom_settings = {"TABLE_NAME": "sz_daily_info", "MIN_CAL_DATE": "2008-01-02"}
