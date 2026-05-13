import datetime

from tushare_integration.spiders.tushare import DailySpider, FinancialReportSpider, TSCodeSpider, TushareSpider

# 这玩意儿后面停用了
# class MarginTargetSpider(TSCodeSpider):
#     name = "stock/market/margin_target"
#     api_name = "margin_target"
#     custom_settings = {"TABLE_NAME": "margin_detail", "BASIC_TABLE": "stock_basic"}


class MarginSecsSpider(DailySpider):
    name = "stock/market/margin_secs"
    api_name = "margin_secs"
    custom_settings = {"TABLE_NAME": "margin_secs"}


class Top10HoldersSpider(FinancialReportSpider):
    name = "stock/market/top10_holders"
    api_name = "top10_holders"
    custom_settings = {"TABLE_NAME": "top10_holders", "HAS_VIP": False}


class Top10FloatHoldersSpider(FinancialReportSpider):
    name = "stock/market/top10_floatholders"
    api_name = "top10_floatholders"
    custom_settings = {"TABLE_NAME": "top10_floatholders", "HAS_VIP": False}


class TopListSpider(DailySpider):
    name = "stock/market/top_list"
    api_name = "top_list"
    custom_settings = {"TABLE_NAME": "top_list", 'MIN_CAL_DATE': '2005-01-01'}


class TopInstSpider(DailySpider):
    name = "stock/market/top_inst"
    api_name = "top_inst"
    custom_settings = {"TABLE_NAME": "top_inst", 'MIN_CAL_DATE': '2005-01-01'}


class PledgeStatSpider(TSCodeSpider):
    name = "stock/market/pledge_stat"
    api_name = "pledge_stat"
    custom_settings = {"TABLE_NAME": "pledge_stat", "BASIC_TABLE": "stock_basic"}


class PledgeDetailSpider(TSCodeSpider):
    name = "stock/market/pledge_detail"
    api_name = "pledge_detail"
    custom_settings = {"TABLE_NAME": "pledge_detail", "BASIC_TABLE": "stock_basic"}


class RepurchaseSpider(TSCodeSpider):
    name = "stock/market/repurchase"
    api_name = "repurchase"
    custom_settings = {"TABLE_NAME": "repurchase", "BASIC_TABLE": "stock_basic"}


class ShareFloatSpider(TSCodeSpider):
    name = "stock/market/share_float"
    api_name = "share_float"
    custom_settings = {"TABLE_NAME": "share_float", "BASIC_TABLE": "stock_basic"}


class ConceptSpider(TushareSpider):
    name = "stock/market/concept"
    api_name = "concept"
    custom_settings = {"TABLE_NAME": "concept"}


class ConceptDetailSpider(TSCodeSpider):
    name = "stock/market/concept_detail"
    api_name = "concept_detail"
    custom_settings = {"TABLE_NAME": "concept_detail"}

    def start_requests(self):
        conn = self.get_db_engine()

        for code in conn.query_df('SELECT code FROM concept')['code']:
            yield self.get_scrapy_request(params={'id': code})


class BlockTradeSpider(DailySpider):
    name = "stock/market/block_trade"
    api_name = "block_trade"
    custom_settings = {"TABLE_NAME": "block_trade"}


class StkHoldernumberSpider(DailySpider):
    name = "stock/market/stk_holdernumber"
    api_name = "stk_holdernumber"
    custom_settings = {"TABLE_NAME": "stk_holdernumber", "TRADE_DATE_FIELD": "ann_date"}


class StkHoldertradeSpider(DailySpider):
    name = "stock/market/stk_holdertrade"
    api_name = "stk_holdertrade"
    custom_settings = {"TABLE_NAME": "stk_holdertrade", "TRADE_DATE_FIELD": "ann_date"}


class StkShockSpider(DailySpider):
    name = "stock/market/stk_shock"
    api_name = "stk_shock"
    custom_settings = {"TABLE_NAME": "stk_shock"}


class StkHighShockSpider(DailySpider):
    name = "stock/market/stk_high_shock"
    api_name = "stk_high_shock"
    custom_settings = {"TABLE_NAME": "stk_high_shock"}


class StkAlertSpider(DailySpider):
    name = "stock/market/stk_alert"
    api_name = "stk_alert"
    custom_settings = {"TABLE_NAME": "stk_alert", "TRADE_DATE_FIELD": "start_date"}


class StkAccountSpider(TushareSpider):
    name = "stock/market/stk_account"
    api_name = "stk_account"
    custom_settings = {"TABLE_NAME": "stk_account"}

    def start_requests(self):
        today = datetime.date.today()
        yield self.get_scrapy_request(params={"start_date": "20150530", "end_date": today.strftime("%Y%m%d")})


class StkAccountOldSpider(TushareSpider):
    name = "stock/market/stk_account_old"
    api_name = "stk_account_old"
    custom_settings = {"TABLE_NAME": "stk_account_old"}

    def start_requests(self):
        yield self.get_scrapy_request(params={"start_date": "20080101", "end_date": "20150529"})


class DailyTypeSpider(DailySpider):
    param_name: str
    param_values: list[str]

    def start_requests(self):
        min_cal_date = self.custom_settings.get("MIN_CAL_DATE", '1970-01-01')
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        table_name = self.get_table_name()
        param_name = self.param_name

        existing_data = conn.query_df(
            f"""
                SELECT DISTINCT trade_date, `{param_name}`
                FROM {db_name}.{table_name}
                """
        )
        existing_keys = set()
        if not existing_data.empty:
            existing_keys = {
                (trade_date.strftime("%Y%m%d"), param_value)
                for trade_date, param_value in existing_data[["trade_date", param_name]].itertuples(index=False)
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
            for param_value in self.param_values:
                if (trade_date, param_value) in existing_keys:
                    continue
                yield self.get_scrapy_request(params={"trade_date": trade_date, param_name: param_value})


class DailyBoardMemberSpider(DailySpider):
    board_table: str
    board_code_field = "ts_code"
    member_code_field = "ts_code"
    request_code_param = "ts_code"

    @staticmethod
    def format_trade_date(trade_date) -> str:
        return trade_date.strftime("%Y%m%d")

    def start_requests(self):
        min_cal_date = self.custom_settings.get("MIN_CAL_DATE", '1970-01-01')
        conn = self.get_db_engine()
        db_name = self.spider_settings.database.db_name
        table_name = self.get_table_name()

        existing_data = conn.query_df(
            f"""
                SELECT DISTINCT trade_date, `{self.member_code_field}` AS board_code
                FROM {db_name}.{table_name}
                """
        )
        existing_keys = set()
        if not existing_data.empty:
            existing_keys = {
                (self.format_trade_date(trade_date), board_code)
                for trade_date, board_code in existing_data[["trade_date", "board_code"]].itertuples(index=False)
            }

        board_pairs = conn.query_df(
            f"""
                SELECT DISTINCT trade_date, `{self.board_code_field}` AS board_code
                FROM {db_name}.{self.board_table}
                WHERE {self.board_code_field} != ''
                  AND trade_date >= '{min_cal_date}'
                  AND trade_date <= today()
                ORDER BY trade_date, board_code
                """
        )
        if board_pairs.empty:
            return

        for trade_date_value, board_code in board_pairs[["trade_date", "board_code"]].itertuples(index=False):
            trade_date = self.format_trade_date(trade_date_value)
            if (trade_date, board_code) in existing_keys:
                continue
            yield self.get_scrapy_request(params={"trade_date": trade_date, self.request_code_param: board_code})


class DCIndexSpider(DailyTypeSpider):
    name = "stock/market/dc_index"
    api_name = "dc_index"
    custom_settings = {"TABLE_NAME": "dc_index"}
    param_name = "idx_type"
    param_values = ["行业板块", "概念板块", "地域板块"]


class DCMemberSpider(DailyBoardMemberSpider):
    name = "stock/market/dc_member"
    api_name = "dc_member"
    custom_settings = {"TABLE_NAME": "dc_member"}
    board_table = "dc_index"


class DCDailySpider(DailySpider):
    name = "stock/market/dc_daily"
    api_name = "dc_daily"
    custom_settings = {"TABLE_NAME": "dc_daily", "MIN_CAL_DATE": "2020-01-01"}


class TDXIndexSpider(DailyTypeSpider):
    name = "stock/market/tdx_index"
    api_name = "tdx_index"
    custom_settings = {"TABLE_NAME": "tdx_index"}
    param_name = "idx_type"
    param_values = ["概念板块", "行业板块", "风格板块", "地区板块"]


class TDXMemberSpider(DailyBoardMemberSpider):
    name = "stock/market/tdx_member"
    api_name = "tdx_member"
    custom_settings = {"TABLE_NAME": "tdx_member"}
    board_table = "tdx_index"


class TDXDailySpider(DailySpider):
    name = "stock/market/tdx_daily"
    api_name = "tdx_daily"
    custom_settings = {"TABLE_NAME": "tdx_daily"}


class DCConceptSpider(DailySpider):
    name = "stock/market/dc_concept"
    api_name = "dc_concept"
    custom_settings = {"TABLE_NAME": "dc_concept", "MIN_CAL_DATE": "2026-02-03"}


class DCConceptConsSpider(DailyBoardMemberSpider):
    name = "stock/market/dc_concept_cons"
    api_name = "dc_concept_cons"
    custom_settings = {"TABLE_NAME": "dc_concept_cons", "MIN_CAL_DATE": "2026-02-03"}
    board_table = "dc_concept"
    board_code_field = "theme_code"
    member_code_field = "theme_code"
    request_code_param = "theme_code"
