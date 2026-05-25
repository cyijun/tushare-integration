import clickhouse_connect.dbapi
import jinja2
import pandas as pd
from sqlalchemy import create_engine, text

from tushare_integration.settings import TushareIntegrationSettings


class DBEngine(object):
    def __init__(self, settings: TushareIntegrationSettings):
        self.settings = settings
        self.templates = {}
        self._load_templates()

        self.functions = {
            'to_date': 'to_date',
        }

    def _load_templates(self):
        db_type = self.settings.database.db_type.lower()
        template_dir = f'tushare_integration/schema/template/{db_type}'
        for name in ('table', 'insert', 'upsert'):
            with open(f'{template_dir}/{name}.jinja2', 'r', encoding='utf-8') as f:
                self.templates[name] = jinja2.Template(f.read())

    def insert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        raise NotImplementedError

    def upsert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        raise NotImplementedError

    def create_table(self, table_name: str, schema: dict) -> None:
        raise NotImplementedError

    def query_df(self, sql: str) -> pd.DataFrame:
        raise NotImplementedError

    def query(self, sql: str) -> pd.DataFrame:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SQLAlchemyEngine(DBEngine):
    def __init__(self, settings: TushareIntegrationSettings):
        super().__init__(settings)
        self._engine = create_engine(self.settings.database.get_uri())
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = self._engine.connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def insert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        column_bindings = self.build_column_bindings(data.columns.tolist())
        sql = self.templates['insert'].render(
            db_name=self.settings.database.db_name,
            table_name=table_name,
            columns=data.columns.tolist(),
            column_bindings=column_bindings,
            template_params=self.settings.database.template_params,
        )

        self.conn.execute(statement=text(sql), parameters=self.build_bound_records(data, column_bindings))  # type: ignore

    def upsert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        column_bindings = self.build_column_bindings(data.columns.tolist())
        sql = self.templates['upsert'].render(
            db_name=self.settings.database.db_name,
            table_name=table_name,
            columns=data.columns.tolist(),
            column_bindings=column_bindings,
            primary_key=schema.get('primary_key', []),
            template_params=self.settings.database.template_params,
        )
        self.conn.execute(statement=text(sql), parameters=self.build_bound_records(data, column_bindings))  # type: ignore

    @staticmethod
    def build_column_bindings(columns: list[str]) -> list[dict[str, str]]:
        return [{"name": column, "bind": f"col_{index}"} for index, column in enumerate(columns)]

    @staticmethod
    def build_bound_records(data: pd.DataFrame, column_bindings: list[dict[str, str]]) -> list[dict[str, object]]:
        return [
            {binding["bind"]: row[binding["name"]] for binding in column_bindings}
            for row in data.to_dict("records")
        ]

    def create_table(self, table_name: str, schema: dict) -> None:
        self.conn.execute(
            statement=text(
                self.templates['table'].render(
                    db_name=self.settings.database.db_name,
                    table_name=table_name,
                    **schema,
                    template_params=self.settings.database.template_params,
                )
            )
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        return pd.read_sql(sql, self.conn)

    def query(self, sql: str):
        return self.conn.execute(statement=text(sql))


class MySQLEngine(SQLAlchemyEngine):
    def __init__(self, settings: TushareIntegrationSettings):
        super().__init__(settings)
        self.functions['to_date'] = 'Date'


class ApacheDorisEngine(SQLAlchemyEngine):
    def __init__(self, settings: TushareIntegrationSettings):
        super().__init__(settings)
        self.functions['to_date'] = 'to_date'


class ClickhouseEngine(DBEngine):
    def __init__(self, settings: TushareIntegrationSettings, send_receive_timeout: int | None = None):
        super().__init__(settings)
        self._client = None
        self._client_kwargs = {
            "host": settings.database.host,
            "port": settings.database.port,
            "username": settings.database.user,
            "password": settings.database.password,
            "database": settings.database.db_name,
            "apply_server_timezone": True,
        }
        if send_receive_timeout is not None:
            self._client_kwargs["send_receive_timeout"] = send_receive_timeout

        self.functions['to_date'] = 'toDate'

    @property
    def client(self):
        if self._client is None:
            self._client = clickhouse_connect.get_client(**self._client_kwargs)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def insert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        self.client.insert_df(table_name, data)

    def upsert(self, table_name: str, schema: dict, data: pd.DataFrame) -> None:
        self.client.insert_df(table_name, data)

    def create_table(self, table_name: str, schema: dict) -> None:
        self.client.query(
            self.templates['table'].render(
                db_name=self.settings.database.db_name,
                table_name=table_name,
                **schema,
                template_params=self.settings.database.template_params,
            )
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        return self.client.query_df(sql)

    def query(self, sql: str):
        return self.client.query(sql)


class DatabaseEngineFactory(object):
    @staticmethod
    def create(
        settings: TushareIntegrationSettings,
        clickhouse_send_receive_timeout: int | None = None,
    ) -> DBEngine:
        if settings.database.db_type == 'clickhouse':
            return ClickhouseEngine(settings, send_receive_timeout=clickhouse_send_receive_timeout)
        elif settings.database.db_type == 'doris':
            return ApacheDorisEngine(settings)
        elif settings.database.db_type == 'mysql':
            return MySQLEngine(settings)
        else:
            raise NotImplementedError
