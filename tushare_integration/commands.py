import json
import re
from pathlib import Path

import typer
import yaml

from tushare_integration.dwd import DWDManager
from tushare_integration.dws import DWSManager
from tushare_integration.manager import CrawlManager
from tushare_integration.quality import QualityManager, QualityValidationError, ValidationMode

try:
    from rich import print
except ImportError:
    pass

crawl_app = typer.Typer(name='CrawlManager', help='CrawlManager help', no_args_is_help=True)

query_app = typer.Typer(
    name='QueryManager',
    help='QueryManager help',
    no_args_is_help=True,
)

dwd_app = typer.Typer(
    name='DWDManager',
    help='DWDManager help',
    no_args_is_help=True,
)

dws_app = typer.Typer(
    name='DWSManager',
    help='DWSManager help',
    no_args_is_help=True,
)

quality_app = typer.Typer(
    name='QualityManager',
    help='QualityManager help',
    no_args_is_help=True,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema"
QUALITY_LAYERS = ("ods", "dwd", "dws")


def _resolve_validation_mode(skip_validation: bool, validation_mode: str | None) -> ValidationMode | None:
    if skip_validation:
        return "skip"
    if validation_mode is None:
        return None
    if validation_mode not in {"strict", "warn_only", "skip"}:
        raise typer.BadParameter("validation mode must be one of: strict, warn_only, skip")
    return validation_mode  # type: ignore[return-value]


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def _list_ods_quality_tables() -> list[str]:
    table_names: set[str] = set()
    excluded_schema_dirs = {"dwd", "dws", "template"}
    for path in sorted(SCHEMA_DIR.glob("**/*.yaml")):
        relative_parts = path.relative_to(SCHEMA_DIR).parts
        if relative_parts[0] in excluded_schema_dirs:
            continue
        schema = _load_yaml(path)
        if schema.get("name") and schema.get("columns"):
            table_names.add(schema["name"])
    return sorted(table_names)


def _list_quality_tables(layer: str) -> list[str]:
    if layer == "ods":
        return _list_ods_quality_tables()
    if layer == "dwd":
        return DWDManager().list_tables()
    if layer == "dws":
        return DWSManager().list_tables()
    raise typer.BadParameter("validation layer must be one of: ods, dwd, dws")


def _resolve_quality_run_targets(
    selector: str | None,
    layer: str | None,
    table_name: str | None,
    all_tables: bool,
) -> list[tuple[str, str]]:
    if layer is not None and layer not in QUALITY_LAYERS:
        raise typer.BadParameter("validation layer must be one of: ods, dwd, dws")

    selected_table = table_name
    if selector and selector != "all":
        if selected_table and selected_table != selector:
            raise typer.BadParameter("provide a table either as positional argument or --table, not both")
        selected_table = selector

    run_all = all_tables or selector == "all" or selected_table == "all"
    if run_all:
        layers = [layer] if layer else list(QUALITY_LAYERS)
        return [(selected_layer, table) for selected_layer in layers for table in _list_quality_tables(selected_layer)]

    if not layer:
        raise typer.BadParameter("--layer is required when validating a single table")
    if not selected_table:
        raise typer.BadParameter("--table is required unless using all or --all")
    return [(layer, selected_table)]


def _compact_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _issue_rate(issue_count: int, checked_count: int | None) -> float | None:
    if not checked_count:
        return None
    return issue_count / checked_count


def _failed_rule_summaries(
    manager: QualityManager,
    run,
    include_sql: bool,
    checked_count: int | None,
) -> list[dict]:
    failed_results = [result for result in run.results if result.status == "FAIL"]
    if not failed_results:
        return []

    rule_sql_by_id = {}
    if include_sql:
        rule_sql_by_id = {
            rule.rule_id: _compact_sql(rule.issue_count_sql)
            for rule in manager.list_rules(
                layer=run.layer,
                table_name=run.table_name,
                target_table_name=run.target_table_name,
            )
        }

    failed_rules = []
    for result in failed_results:
        failed_rule = {
            "rule_id": result.rule_id,
            "severity": result.severity,
            "issue_count": result.issue_count,
            "checked_count": checked_count,
            "issue_rate": _issue_rate(result.issue_count, checked_count),
        }
        if include_sql and result.rule_id in rule_sql_by_id:
            failed_rule["issue_count_sql"] = rule_sql_by_id[result.rule_id]
        failed_rules.append(failed_rule)
    return failed_rules


def _checked_count_by_run(manager: QualityManager, runs) -> dict[str, int | None]:
    checked_counts = {}
    for run in runs:
        key = run.run_id
        try:
            checked_counts[key] = manager.checked_count(
                layer=run.layer,
                table_name=run.table_name,
                target_table_name=run.target_table_name,
            )
        except Exception:
            checked_counts[key] = None
    return checked_counts


def _quality_run_summary(manager: QualityManager, runs, include_sql: bool) -> dict:
    checked_counts = _checked_count_by_run(manager, runs)
    return {
        "status": "FAIL" if any(run.status == "FAIL" for run in runs) else "PASS",
        "run_count": len(runs),
        "runs": [
            {
                "run_id": run.run_id,
                "layer": run.layer,
                "table_name": run.table_name,
                "target_table_name": run.target_table_name,
                "mode": run.mode,
                "status": run.status,
                "checked_count": checked_counts[run.run_id],
                "failed_rules": _failed_rule_summaries(manager, run, include_sql, checked_counts[run.run_id]),
            }
            for run in runs
        ],
    }


@query_app.command('list', help="List spiders")
def list_spiders():
    manager = CrawlManager()
    print(manager.list_spiders())


@dwd_app.command('list', help="List DWD tables")
def list_dwd_tables():
    manager = DWDManager()
    print(manager.list_tables())


@dwd_app.command('create', help="Create a DWD table", no_args_is_help=True)
def create_dwd_table(
    table_name: str = typer.Argument(..., help="DWD table name, e.g. dwd_stock_eod_price"),
):
    manager = DWDManager()
    manager.create_table(table_name)


@dwd_app.command('sync', help="Sync ODS raw tables to DWD", no_args_is_help=True)
def sync_dwd_table(
    table_name: str = typer.Argument(..., help="DWD table name or all"),
    skip_validation: bool = typer.Option(False, "--skip-validation", help="Temporarily skip validation"),
    validation_mode: str | None = typer.Option(
        None,
        "--validation-mode",
        help="Override validation mode: strict, warn_only, or skip",
    ),
):
    manager = DWDManager()
    resolved_mode = _resolve_validation_mode(skip_validation, validation_mode)
    if table_name == 'all':
        manager.sync_all(validation_mode=resolved_mode, skip_validation=skip_validation)
        return
    manager.sync_table(table_name, validation_mode=resolved_mode, skip_validation=skip_validation)


@dwd_app.command('sql', help="Render DWD sync SQL", no_args_is_help=True)
def render_dwd_sql(
    table_name: str = typer.Argument(..., help="DWD table name, e.g. dwd_stock_eod_price"),
):
    manager = DWDManager()
    print(manager.render_sync_sql(table_name))


@dws_app.command('list', help="List DWS tables")
def list_dws_tables():
    manager = DWSManager()
    print(manager.list_tables())


@dws_app.command('create', help="Create a DWS table", no_args_is_help=True)
def create_dws_table(
    table_name: str = typer.Argument(..., help="DWS table name, e.g. dws_stock_factor_wide"),
):
    manager = DWSManager()
    manager.create_table(table_name)


@dws_app.command('sync', help="Sync DWD tables to DWS", no_args_is_help=True)
def sync_dws_table(
    table_name: str = typer.Argument(..., help="DWS table name or all"),
    skip_validation: bool = typer.Option(False, "--skip-validation", help="Temporarily skip validation"),
    validation_mode: str | None = typer.Option(
        None,
        "--validation-mode",
        help="Override validation mode: strict, warn_only, or skip",
    ),
):
    manager = DWSManager()
    resolved_mode = _resolve_validation_mode(skip_validation, validation_mode)
    if table_name == 'all':
        manager.sync_all(validation_mode=resolved_mode, skip_validation=skip_validation)
        return
    manager.sync_table(table_name, validation_mode=resolved_mode, skip_validation=skip_validation)


@dws_app.command('sql', help="Render DWS sync SQL", no_args_is_help=True)
def render_dws_sql(
    table_name: str = typer.Argument(..., help="DWS table name, e.g. dws_stock_factor_wide"),
):
    manager = DWSManager()
    print(manager.render_sync_sql(table_name))


@quality_app.command('list', help="List validation rules", no_args_is_help=True)
def list_quality_rules(
    layer: str = typer.Argument(..., help="Validation layer: ods, dwd, or dws"),
    table_name: str = typer.Argument(..., help="Logical table name"),
):
    manager = QualityManager()
    for rule in manager.list_rules(layer=layer, table_name=table_name):
        print(f"{rule.severity} {rule.rule_id}: {rule.description}")


@quality_app.command('run', help="Run validation rules", no_args_is_help=True)
def run_quality_rules(
    selector: str | None = typer.Argument(
        None,
        help="Logical table name or all. Use `all` to validate every table, optionally scoped by --layer.",
    ),
    layer: str | None = typer.Option(None, "--layer", help="Validation layer: ods, dwd, or dws"),
    table_name: str | None = typer.Option(None, "--table", help="Logical table name or all"),
    all_tables: bool = typer.Option(False, "--all", help="Validate all tables, optionally scoped by --layer"),
    target_table_name: str | None = typer.Option(
        None,
        "--target-table",
        help="Physical table to validate; valid only for single-table checks; defaults to --table",
    ),
    mode: str | None = typer.Option(None, "--mode", help="Override validation mode: strict, warn_only, or skip"),
    include_sql: bool = typer.Option(
        True,
        "--include-sql/--no-sql",
        help="Include failed rule issue-count SQL in all-table summaries",
    ),
):
    is_all_request = all_tables or selector == "all" or table_name == "all"
    targets = _resolve_quality_run_targets(selector, layer, table_name, all_tables)
    if target_table_name is not None and is_all_request:
        raise typer.BadParameter("--target-table can only be used when validating a single table")

    manager = QualityManager()
    resolved_mode = _resolve_validation_mode(False, mode)
    runs = []
    blocked = False
    for target_layer, target_table in targets:
        try:
            run = manager.validate_publish(
                layer=target_layer,
                table_name=target_table,
                target_table_name=target_table_name or target_table,
                stage=f"manual_{target_layer}_all" if is_all_request else f"manual_{target_layer}",
                mode=resolved_mode,
            )
        except QualityValidationError as exc:
            run = exc.run
            blocked = True
        runs.append(run)

    if len(runs) == 1 and not is_all_request:
        print(manager.run_to_json(runs[0]))
    else:
        print(
            json.dumps(
                _quality_run_summary(manager, runs, include_sql),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    if blocked:
        raise typer.Exit(1)


@quality_app.command('report', help="Show validation report", no_args_is_help=True)
def report_quality_run(
    run_id: str = typer.Argument(..., help="Validation run id"),
):
    manager = QualityManager()
    print(manager.report_run(run_id))


@crawl_app.command('job', help="Run a job", no_args_is_help=True)
def run_job(
    job_name: str = typer.Argument(..., help="Name of the job to run"),
    update_type: str | None = typer.Option(
        None,
        "--update-type",
        "-u",
        help="Optional update dimension to run: incremental/daily or full/fully.",
    ),
):
    manager = CrawlManager()
    manager.run_job(job_name, update_type=update_type)


@crawl_app.command('spider', help="Run spiders", no_args_is_help=True)
def run_spider(
    spider: str = typer.Argument(
        ...,
        help="Wildcard of the spider to run",
    )
):
    manager = CrawlManager()
    manager.run_spider(spider)
