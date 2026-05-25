import logging
import os
import sys

import yaml

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tushare_integration.db_engine import DatabaseEngineFactory
from tushare_integration.manager import CrawlManager
from tushare_integration.settings import TushareIntegrationSettings


def main():
    manager = CrawlManager()
    with open('config.yaml', 'r', encoding='utf-8') as f:
        settings = TushareIntegrationSettings.model_validate(yaml.safe_load(f))
    for spider in manager.list_spiders('.*'):
        table_name = spider.split('/')[-1]

        with open(f"tushare_integration/schema/{spider}.yaml", "r", encoding="utf-8") as f:
            schema = yaml.safe_load(f.read())
        db_engine = DatabaseEngineFactory.create(settings)
        try:
            logging.info(f"Creating table {table_name}")
            db_engine.create_table(table_name, schema)
        except Exception as e:
            print(spider, e)
        finally:
            db_engine.close()


if __name__ == '__main__':
    main()
