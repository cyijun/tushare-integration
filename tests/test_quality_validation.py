import unittest
from unittest import mock

import pandas as pd

from tushare_integration.dwd import DWDManager
from tushare_integration.quality import QualityManager, QualityValidationError, ValidationResult
from tushare_integration.settings import TushareIntegrationSettings


class DummyDB:
    def __init__(self):
        self.inserts = []
        self.created_tables = []

    def create_table(self, table_name, schema):
        self.created_tables.append(table_name)

    def insert(self, table_name, schema, data):
        self.inserts.append((table_name, data.copy()))

    def query_df(self, sql):
        return pd.DataFrame({"issue_count": [0]})


class QualityValidationTest(unittest.TestCase):
    def _settings(self, quality=None):
        return TushareIntegrationSettings(
            tushare_token="token",
            feishu_webhook="",
            database={
                "db_type": "clickhouse",
                "host": "localhost",
                "port": 8123,
                "user": "default",
                "password": "",
                "db_name": "default",
            },
            quality=quality or {"mode": "warn_only", "create_result_tables": False},
        )

    def test_skip_mode_records_bypass_without_running_rules(self):
        db = DummyDB()
        manager = QualityManager(settings=self._settings(), db_engine=db)

        with mock.patch.object(manager, "run_rules") as run_rules:
            run = manager.validate_publish(
                layer="dwd",
                table_name="dwd_stock_eod_price",
                target_table_name="dwd_stock_eod_price_tmp",
                stage="pre_dwd_publish",
                skip_validation=True,
            )

        run_rules.assert_not_called()
        self.assertEqual(run.mode, "skip")
        self.assertEqual(run.status, "SKIPPED")
        self.assertEqual(db.inserts[0][0], "dq_validation_run")

    def test_warn_only_records_failures_but_does_not_raise(self):
        db = DummyDB()
        manager = QualityManager(settings=self._settings(), db_engine=db)
        failure = ValidationResult(
            rule_id="market_ohlc_consistency",
            severity="BLOCKER",
            status="FAIL",
            issue_count=2,
            description="bad ohlc",
        )

        with mock.patch.object(manager, "run_rules", return_value=[failure]):
            run = manager.validate_publish(
                layer="dwd",
                table_name="dwd_stock_eod_price",
                target_table_name="dwd_stock_eod_price_tmp",
                stage="pre_dwd_publish",
                mode="warn_only",
            )

        self.assertEqual(run.status, "FAIL")
        self.assertFalse(run.should_block)
        self.assertEqual(db.inserts[1][0], "dq_validation_result")

    def test_strict_blocks_on_blocker_failure(self):
        db = DummyDB()
        manager = QualityManager(settings=self._settings(), db_engine=db)
        failure = ValidationResult(
            rule_id="dwd_single_open_version",
            severity="BLOCKER",
            status="FAIL",
            issue_count=1,
            description="duplicate open version",
        )

        with mock.patch.object(manager, "run_rules", return_value=[failure]):
            with self.assertRaises(QualityValidationError):
                manager.validate_publish(
                    layer="dwd",
                    table_name="dwd_stock_eod_price",
                    target_table_name="dwd_stock_eod_price_tmp",
                    stage="pre_dwd_publish",
                    mode="strict",
                )

    def test_table_mode_overrides_global_mode(self):
        manager = QualityManager(
            settings=self._settings(
                {
                    "mode": "strict",
                    "table_modes": {"dwd_stock_financial_indicator": "skip"},
                    "create_result_tables": False,
                }
            ),
            db_engine=DummyDB(),
        )

        self.assertEqual(manager.resolve_mode("dwd", "dwd_stock_financial_indicator"), "skip")
        self.assertEqual(manager.resolve_mode("dwd", "dwd_stock_eod_price"), "strict")

    def test_dwd_market_rules_include_business_checks(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        rule_ids = {
            rule.rule_id
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_stock_eod_price",
                target_table_name="dwd_stock_eod_price_tmp",
            )
        }

        self.assertIn("market_ohlc_consistency", rule_ids)
        self.assertIn("market_positive_prices_when_traded", rule_ids)
        self.assertIn("dwd_single_open_version", rule_ids)

    def test_checked_count_sql_uses_trade_date_scope(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        dwd_sql = manager.checked_count_sql(
            layer="dwd",
            table_name="dwd_stock_eod_price",
            target_table_name="dwd_stock_eod_price_tmp",
        )
        dws_sql = manager.checked_count_sql(
            layer="dws",
            table_name="dws_stock_factor_wide",
            target_table_name="dws_stock_factor_wide_tmp",
        )
        ods_sql = manager.checked_count_sql(layer="ods", table_name="daily", target_table_name="daily")

        self.assertIn("event_date >= toDate32('2010-01-01')", dwd_sql)
        self.assertIn("trade_date >= toDate32('2010-01-01')", dws_sql)
        self.assertNotIn("2010-01-01", ods_sql)

    def test_market_ohlc_consistency_only_checks_active_price_rows(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_future_eod_price",
                target_table_name="dwd_future_eod_price_tmp",
            )
        }

        self.assertIn("vol > 0 OR open > 0 OR high > 0 OR low > 0", rules["market_ohlc_consistency"].issue_count_sql)
        self.assertIn("high < low OR high < open OR high < close", rules["market_ohlc_consistency"].issue_count_sql)

    def test_dwd_trade_rules_are_limited_to_rows_since_2010(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_stock_eod_price",
                target_table_name="dwd_stock_eod_price_tmp",
            )
        }

        self.assertIn("event_date >= toDate32('2010-01-01')", rules["row_count_nonzero"].issue_count_sql)
        self.assertIn("event_date >= toDate32('2010-01-01')", rules["dwd_single_open_version"].issue_count_sql)
        self.assertIn("event_date >= toDate32('2010-01-01')", rules["market_ohlc_consistency"].issue_count_sql)
        self.assertNotIn("2010-01-01", rules["required_columns_exist"].issue_count_sql)

    def test_dws_trade_rules_are_limited_to_trade_dates_since_2010(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dws",
                table_name="dws_stock_factor_wide",
                target_table_name="dws_stock_factor_wide_tmp",
            )
        }

        self.assertIn("trade_date >= toDate32('2010-01-01')", rules["row_count_nonzero"].issue_count_sql)
        self.assertIn("trade_date >= toDate32('2010-01-01')", rules["dws_factor_wide_unique_key"].issue_count_sql)
        self.assertIn("trade_date >= toDate32('2010-01-01')", rules["dws_factor_wide_ohlc"].issue_count_sql)

    def test_non_trade_dwd_rules_are_not_date_limited(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_stock_income",
                target_table_name="dwd_stock_income_tmp",
            )
        }

        self.assertNotIn("2010-01-01", rules["financial_no_placeholder_dates"].issue_count_sql)

    def test_dwd_open_version_rule_uses_source_business_key(self):
        manager = QualityManager(settings=self._settings(), db_engine=DummyDB())

        income_rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_stock_income",
                target_table_name="dwd_stock_income_tmp",
            )
        }
        calendar_rules = {
            rule.rule_id: rule
            for rule in manager.list_rules(
                layer="dwd",
                table_name="dwd_trade_calendar",
                target_table_name="dwd_trade_calendar_tmp",
            )
        }

        self.assertIn("report_type", income_rules["dwd_single_open_version"].issue_count_sql)
        self.assertIn("update_flag", income_rules["dwd_single_open_version"].issue_count_sql)
        self.assertIn(
            "GROUP BY ts_code, ann_date, f_ann_date, end_date, report_type, update_flag",
            income_rules["dwd_single_open_version"].issue_count_sql,
        )
        self.assertIn(
            "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING",
            income_rules["dwd_no_overlapping_versions"].issue_count_sql,
        )
        self.assertIn("GROUP BY cal_date, exchange", calendar_rules["dwd_single_open_version"].issue_count_sql)
        self.assertNotIn("GROUP BY event_date", calendar_rules["dwd_single_open_version"].issue_count_sql)

    def test_dwd_version_sql_uses_full_window_frame(self):
        sql = DWDManager().render_sync_sql("dwd_stock_income")

        self.assertIn("ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING", sql)


if __name__ == "__main__":
    unittest.main()
