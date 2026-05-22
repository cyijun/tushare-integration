import datetime

from tushare_integration.spiders.stock.quotes import StockMonthlySpider, StockWeeklySpider
from tushare_integration.spiders.tushare import DailySpider


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
    custom_settings = {
        "TABLE_NAME": "index_weight",
        "BASIC_TABLE": "index_basic",
        "MIN_CAL_DATE": "2005-04-08",  # 根据实际数据情况设置合适的起始日期
    }

    @staticmethod
    def iter_month_ranges(start_date: datetime.date, end_date: datetime.date):
        month_start = start_date.replace(day=1)
        while month_start <= end_date:
            if month_start.month == 12:
                next_month = datetime.date(month_start.year + 1, 1, 1)
            else:
                next_month = datetime.date(month_start.year, month_start.month + 1, 1)

            month_end = min(next_month - datetime.timedelta(days=1), end_date)
            yield month_start, month_end
            month_start = next_month

    @staticmethod
    def index_existed_in_month(index_row, month_start: datetime.date, month_end: datetime.date) -> bool:
        list_date = DailySpider.parse_date_value(getattr(index_row, "list_date", None))
        base_date = DailySpider.parse_date_value(getattr(index_row, "base_date", None))
        exp_date = DailySpider.parse_date_value(getattr(index_row, "exp_date", None))

        start_date = list_date or base_date
        if start_date and start_date > month_end:
            return False

        if exp_date and exp_date != datetime.date(1970, 1, 1) and exp_date < month_start:
            return False

        return True

    @staticmethod
    def get_request_end_date() -> datetime.date:
        return datetime.date.today()

    def start_requests(self):
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        start_date = self.get_incremental_start_date(conn, "trade_date")
        end_date = self.get_request_end_date()

        index_list = conn.query_df(
            f"""
                SELECT ts_code, base_date, list_date, exp_date
                FROM {db_name}.{self.custom_settings.get('BASIC_TABLE')}
                WHERE ts_code != ''
                ORDER BY ts_code
                """
        )

        if index_list.empty or start_date > end_date:
            return

        month_ranges = list(self.iter_month_ranges(start_date, end_date))
        for index_row in index_list.itertuples(index=False):
            for month_start, month_end in month_ranges:
                if not self.index_existed_in_month(index_row, month_start, month_end):
                    continue

                yield self.get_scrapy_request(
                    params={
                        "index_code": index_row.ts_code,
                        "start_date": month_start.strftime("%Y%m%d"),
                        "end_date": month_end.strftime("%Y%m%d"),
                    }
                )


class SzDailyInfoSpider(DailySpider):
    name = "index/quotes/sz_daily_info"
    custom_settings = {"TABLE_NAME": "sz_daily_info", "MIN_CAL_DATE": "2008-01-02"}
