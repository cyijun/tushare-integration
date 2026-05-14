import json
import unittest
from unittest import mock

from typer.testing import CliRunner

from tushare_integration import commands
from tushare_integration.quality import ValidationResult, ValidationRule, ValidationRun


class FakeQualityManager:
    runs = []
    calls = []

    def validate_publish(self, **kwargs):
        self.calls.append(kwargs)
        run = ValidationRun(
            run_id=f"run_{kwargs['layer']}_{kwargs['table_name']}",
            layer=kwargs["layer"],
            stage=kwargs["stage"],
            table_name=kwargs["table_name"],
            target_table_name=kwargs["target_table_name"],
            mode=kwargs["mode"] or "warn_only",
            status="PASS",
            started_at="2026-05-14 00:00:00",
            finished_at="2026-05-14 00:00:00",
            results=[],
        )
        self.runs.append(run)
        return run

    def run_to_json(self, run):
        return run.run_id

    def checked_count(self, layer, table_name, target_table_name=None):
        return 100


class FailedQualityManager(FakeQualityManager):
    def validate_publish(self, **kwargs):
        run = super().validate_publish(**kwargs)
        return ValidationRun(
            run_id=run.run_id,
            layer=run.layer,
            stage=run.stage,
            table_name=run.table_name,
            target_table_name=run.target_table_name,
            mode=run.mode,
            status="FAIL",
            started_at=run.started_at,
            finished_at=run.finished_at,
            results=[
                ValidationResult(
                    rule_id="market_ohlc_consistency",
                    severity="BLOCKER",
                    status="FAIL",
                    issue_count=2,
                    description="bad ohlc",
                )
            ],
        )

    def list_rules(self, layer, table_name, target_table_name=None):
        return [
            ValidationRule(
                rule_id="market_ohlc_consistency",
                description="bad ohlc",
                severity="BLOCKER",
                issue_count_sql=f"""
                    SELECT count()
                    FROM {target_table_name or table_name}
                    WHERE high < low
                """,
            )
        ]


class QualityCliTest(unittest.TestCase):
    def setUp(self):
        FakeQualityManager.runs = []
        FakeQualityManager.calls = []
        self.runner = CliRunner()

    def test_quality_run_all_cli_syntax(self):
        with (
            mock.patch.object(commands, "_list_quality_tables", return_value=["table_a"]),
            mock.patch.object(commands, "QualityManager", FakeQualityManager),
        ):
            result = self.runner.invoke(commands.quality_app, ["run", "all", "--mode", "warn_only"])

        self.assertEqual(result.exit_code, 0)
        output = json.loads(result.output)
        self.assertEqual(output["run_count"], 3)
        self.assertEqual(
            [(call["layer"], call["table_name"]) for call in FakeQualityManager.calls],
            [("ods", "table_a"), ("dwd", "table_a"), ("dws", "table_a")],
        )

    def test_quality_run_layer_all_cli_syntax(self):
        with (
            mock.patch.object(commands, "_list_quality_tables", return_value=["dwd_a"]),
            mock.patch.object(commands, "QualityManager", FakeQualityManager),
        ):
            result = self.runner.invoke(commands.quality_app, ["run", "--layer", "dwd", "--all"])

        self.assertEqual(result.exit_code, 0)
        output = json.loads(result.output)
        self.assertEqual(output["run_count"], 1)
        self.assertEqual(
            [(call["layer"], call["table_name"], call["stage"]) for call in FakeQualityManager.calls],
            [("dwd", "dwd_a", "manual_dwd_all")],
        )

    def test_quality_run_all_selector_expands_all_layers(self):
        with (
            mock.patch.object(commands, "_list_quality_tables") as list_quality_tables,
            mock.patch.object(commands, "QualityManager", FakeQualityManager),
            mock.patch.object(commands, "print"),
        ):
            list_quality_tables.side_effect = lambda layer: [f"{layer}_table"]

            commands.run_quality_rules(
                selector="all",
                layer=None,
                table_name=None,
                all_tables=False,
                target_table_name=None,
                mode="warn_only",
                include_sql=True,
            )

        self.assertEqual(
            [(call["layer"], call["table_name"]) for call in FakeQualityManager.calls],
            [("ods", "ods_table"), ("dwd", "dwd_table"), ("dws", "dws_table")],
        )

    def test_quality_run_all_option_scopes_to_layer(self):
        with (
            mock.patch.object(commands, "_list_quality_tables", return_value=["dwd_a", "dwd_b"]),
            mock.patch.object(commands, "QualityManager", FakeQualityManager),
            mock.patch.object(commands, "print"),
        ):
            commands.run_quality_rules(
                selector=None,
                layer="dwd",
                table_name=None,
                all_tables=True,
                target_table_name=None,
                mode="warn_only",
                include_sql=True,
            )

        self.assertEqual(
            [(call["layer"], call["table_name"], call["stage"]) for call in FakeQualityManager.calls],
            [("dwd", "dwd_a", "manual_dwd_all"), ("dwd", "dwd_b", "manual_dwd_all")],
        )

    def test_quality_run_single_table_still_supports_target_table(self):
        with (
            mock.patch.object(commands, "QualityManager", FakeQualityManager),
            mock.patch.object(commands, "print"),
        ):
            commands.run_quality_rules(
                selector=None,
                layer="dwd",
                table_name="dwd_stock_eod_price",
                all_tables=False,
                target_table_name="dwd_stock_eod_price_tmp",
                mode="strict",
                include_sql=True,
            )

        self.assertEqual(len(FakeQualityManager.calls), 1)
        self.assertEqual(FakeQualityManager.calls[0]["target_table_name"], "dwd_stock_eod_price_tmp")
        self.assertEqual(FakeQualityManager.calls[0]["mode"], "strict")

    def test_quality_run_all_rejects_target_table(self):
        with mock.patch.object(commands, "_list_quality_tables", return_value=["dwd_a"]):
            with self.assertRaises(Exception) as context:
                commands.run_quality_rules(
                    selector=None,
                    layer="dwd",
                    table_name=None,
                    all_tables=True,
                    target_table_name="dwd_tmp",
                    mode="warn_only",
                    include_sql=True,
                )

        self.assertIn("--target-table", str(context.exception))

    def test_quality_run_all_summary_includes_failed_rule_sql(self):
        with (
            mock.patch.object(commands, "_list_quality_tables", return_value=["dwd_a"]),
            mock.patch.object(commands, "QualityManager", FailedQualityManager),
        ):
            result = self.runner.invoke(commands.quality_app, ["run", "--layer", "dwd", "--all"])

        self.assertEqual(result.exit_code, 0)
        output = json.loads(result.output)
        failed_rule = output["runs"][0]["failed_rules"][0]
        self.assertEqual(failed_rule["rule_id"], "market_ohlc_consistency")
        self.assertEqual(output["runs"][0]["checked_count"], 100)
        self.assertEqual(failed_rule["checked_count"], 100)
        self.assertEqual(failed_rule["issue_rate"], 0.02)
        self.assertEqual(failed_rule["issue_count_sql"], "SELECT count() FROM dwd_a WHERE high < low")
        self.assertNotIn("\n", failed_rule["issue_count_sql"])
        self.assertNotIn("  ", failed_rule["issue_count_sql"])

    def test_quality_run_all_summary_can_omit_failed_rule_sql(self):
        with (
            mock.patch.object(commands, "_list_quality_tables", return_value=["dwd_a"]),
            mock.patch.object(commands, "QualityManager", FailedQualityManager),
        ):
            result = self.runner.invoke(commands.quality_app, ["run", "--layer", "dwd", "--all", "--no-sql"])

        self.assertEqual(result.exit_code, 0)
        output = json.loads(result.output)
        failed_rule = output["runs"][0]["failed_rules"][0]
        self.assertNotIn("issue_count_sql", failed_rule)


if __name__ == "__main__":
    unittest.main()
