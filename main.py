import typer

from tushare_integration.commands import crawl_app, dwd_app, query_app

app = typer.Typer(name='CrawlManager', help='CrawlManager help', no_args_is_help=True)


def main():
    app.add_typer(crawl_app, name='run')
    app.add_typer(query_app, name='query')
    app.add_typer(dwd_app, name='dwd')

    app()


if __name__ == '__main__':
    main()
