import json
import unittest

import pandas as pd

from tushare_integration.dws import DWSManager, STOCK_FACTOR_WIDE_MATRIX_UDF
from tushare_integration.factors.engine import FactorEngine, compute_factor_rows
from tushare_integration.settings import TushareIntegrationSettings


class FactorWideMatrixTest(unittest.TestCase):
    def _clickhouse_settings(self):
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
        )

    def test_compute_factor_rows_returns_json_payload_per_trade_row(self):
        engine = FactorEngine(
            mapping_df=pd.DataFrame(
                [
                    {
                        "group": "demo",
                        "source": "unit",
                        "factor_id": "demo_ma2",
                        "name": "MA2",
                        "expression": "Mean($close, 2) / $close",
                    },
                    {
                        "group": "demo",
                        "source": "unit",
                        "factor_id": "demo_volume_alias",
                        "name": "VolumeAlias",
                        "expression": "$volume / 10",
                    },
                ]
            )
        )
        rows = [
            ["2026-01-02", "2026-01-02", "2026-01-05", "batch-1", "hash-1", 10.0, 100.0],
            ["2026-01-05", "2026-01-05", "2026-01-06", "batch-2", "hash-2", 20.0, 200.0],
        ]

        output = compute_factor_rows(["close", "vol"], rows, engine=engine)

        self.assertEqual(len(output), 2)
        self.assertEqual(output[1][0:3], ["2026-01-05", "2026-01-05", "2026-01-06"])
        payload = json.loads(output[1][3])
        self.assertEqual(payload["values"]["demo_ma2"], 0.75)
        self.assertEqual(payload["values"]["demo_volume_alias"], 20.0)
        self.assertEqual(output[1][5], 2)

    def test_dws_factor_wide_matrix_sql_uses_python_udf(self):
        manager = object.__new__(DWSManager)
        manager.settings = self._clickhouse_settings()

        sql = manager.render_sync_sql("dws_stock_factor_wide_matrix")

        self.assertIn(f"arrayJoin({STOCK_FACTOR_WIDE_MATRIX_UDF}(", sql)
        self.assertIn("toJSONString(rows)", sql)
        self.assertIn(
            "toFloat64OrNull(JSONExtractRaw(factor_values_json, 'values', 'a158_beta10')) AS `a158_beta10`",
            sql,
        )
        self.assertIn(
            "toFloat64OrNull(JSONExtractRaw(factor_values_json, 'values', 'wide_ps_ttm_raw')) "
            "AS `wide_ps_ttm_raw`",
            sql,
        )
        self.assertIn('"volume"', sql)
        self.assertIn('"vwap"', sql)
        self.assertIn('"turnover"', sql)
        self.assertNotIn("Mean($close", sql)
        self.assertNotIn("Corr($close", sql)

    def test_dws_factor_wide_matrix_declares_wide_source(self):
        manager = object.__new__(DWSManager)
        spec = manager.load_spec("dws_stock_factor_wide_matrix")

        self.assertEqual(manager.get_required_source_tables(spec), ["dws_stock_factor_wide"])

    def test_dws_factor_wide_matrix_schema_has_physical_factor_columns(self):
        manager = object.__new__(DWSManager)
        spec = manager.load_spec("dws_stock_factor_wide_matrix")
        column_names = [column["name"] for column in spec["schema"]["columns"]]

        self.assertIn("a158_beta10", column_names)
        self.assertIn("wide_ps_ttm_raw", column_names)
        self.assertNotIn("factor_values_json", column_names)
        self.assertEqual(sum(1 for name in column_names if name.startswith("a158_")), 158)
