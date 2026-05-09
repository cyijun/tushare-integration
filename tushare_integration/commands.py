import typer

from tushare_integration.dwd import DWDManager
from tushare_integration.dws import DWSManager
from tushare_integration.manager import CrawlManager

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
):
    manager = DWDManager()
    if table_name == 'all':
        manager.sync_all()
        return
    manager.sync_table(table_name)


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
):
    manager = DWSManager()
    if table_name == 'all':
        manager.sync_all()
        return
    manager.sync_table(table_name)


@dws_app.command('sql', help="Render DWS sync SQL", no_args_is_help=True)
def render_dws_sql(
    table_name: str = typer.Argument(..., help="DWS table name, e.g. dws_stock_factor_wide"),
):
    manager = DWSManager()
    print(manager.render_sync_sql(table_name))


@crawl_app.command('job', help="Run a job", no_args_is_help=True)
def run_job(job_name: str = typer.Argument(..., help="Name of the job to run")):
    manager = CrawlManager()
    manager.run_job(job_name)


@crawl_app.command('spider', help="Run spiders", no_args_is_help=True)
def run_spider(
    spider: str = typer.Argument(
        ...,
        help="Wildcard of the spider to run",
    )
):
    manager = CrawlManager()
    manager.run_spider(spider)
