from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
os.chdir(ROOT_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tushare_integration.db_engine import DatabaseEngineFactory
from tushare_integration.dwd import DWDManager
from tushare_integration.settings import TushareIntegrationSettings
from tushare_integration.storage import build_latest_schema


SCHEMA_DIR = ROOT_DIR / "tushare_integration" / "schema"
DEFAULT_OUTPUT = ROOT_DIR / "docs" / "downloaded_tables_report.txt"
INTERNAL_TABLES = {"tushare_integration_log"}


@dataclass
class TableDoc:
    name: str
    comment: str
    columns: list[dict[str, Any]]
    row_count: int
    schema_source: str
    sample: pd.DataFrame | None = None


def load_settings(config_path: Path) -> TushareIntegrationSettings:
    with open(config_path, "r", encoding="utf-8") as f:
        return TushareIntegrationSettings.model_validate(yaml.safe_load(f.read()))


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def build_schema_index() -> dict[str, dict[str, Any]]:
    schema_index: dict[str, dict[str, Any]] = {}

    for path in sorted(SCHEMA_DIR.glob("**/*.yaml")):
        relative_path = path.relative_to(SCHEMA_DIR)
        if relative_path.parts[0] in {"dwd", "template"}:
            continue

        schema = load_yaml(path)
        table_name = schema.get("name")
        if not table_name or "columns" not in schema:
            continue

        # Latest tables are the business table plus system metadata columns.
        schema_index.setdefault(
            table_name,
            {
                "comment": schema.get("comment", ""),
                "columns": build_latest_schema(schema)["columns"],
                "schema_source": str(relative_path),
            },
        )

    dwd_manager = DWDManager()
    for table_name in dwd_manager.list_tables():
        spec = dwd_manager.load_spec(table_name)
        schema = dwd_manager.build_schema(spec)
        schema_index[table_name] = {
            "comment": schema.get("comment") or spec.get("comment", ""),
            "columns": schema.get("columns", []),
            "schema_source": f"dwd/{table_name}.yaml",
        }

    return schema_index


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def query_table_names(db_engine, settings: TushareIntegrationSettings) -> list[str]:
    db_name = settings.database.db_name
    if settings.database.db_type == "clickhouse":
        sql = f"""
            SELECT name
            FROM system.tables
            WHERE database = currentDatabase()
              AND NOT endsWith(name, '_raw')
            ORDER BY name
        """
    else:
        sql = f"""
            SELECT table_name AS name
            FROM information_schema.tables
            WHERE table_schema = {sql_string(db_name)}
              AND table_name NOT LIKE '%\\_raw'
            ORDER BY table_name
        """

    table_names = db_engine.query_df(sql)["name"].tolist()
    return [table_name for table_name in table_names if table_name not in INTERNAL_TABLES]


def count_rows(db_engine, settings: TushareIntegrationSettings, table_name: str) -> int:
    db_name = settings.database.db_name
    count_expr = "count()" if settings.database.db_type == "clickhouse" else "count(*)"
    sql = f"SELECT {count_expr} AS row_count FROM {quote_identifier(db_name)}.{quote_identifier(table_name)}"
    result = db_engine.query_df(sql)
    if result.empty:
        return 0
    return int(result.iloc[0]["row_count"])


def query_fallback_table_doc(db_engine, settings: TushareIntegrationSettings, table_name: str) -> dict[str, Any]:
    db_name = settings.database.db_name
    if settings.database.db_type == "clickhouse":
        columns_sql = f"""
            SELECT name, comment
            FROM system.columns
            WHERE database = currentDatabase()
              AND table = {sql_string(table_name)}
            ORDER BY position
        """
    else:
        columns_sql = f"""
            SELECT column_name AS name, column_comment AS comment
            FROM information_schema.columns
            WHERE table_schema = {sql_string(db_name)}
              AND table_name = {sql_string(table_name)}
            ORDER BY ordinal_position
        """

    columns_df = db_engine.query_df(columns_sql)
    return {
        "comment": "未在本地 schema 中匹配到表说明",
        "columns": columns_df.fillna("").to_dict("records"),
        "schema_source": "database metadata",
    }


def query_sample(db_engine, settings: TushareIntegrationSettings, table_name: str, sample_size: int) -> pd.DataFrame | None:
    if sample_size <= 0:
        return None

    db_name = settings.database.db_name
    sql = f"SELECT * FROM {quote_identifier(db_name)}.{quote_identifier(table_name)} LIMIT {sample_size}"
    return db_engine.query_df(sql)


def collect_table_docs(
    config_path: Path,
    include_empty: bool,
    sample_size: int,
) -> tuple[TushareIntegrationSettings, list[TableDoc]]:
    settings = load_settings(config_path)
    db_engine = DatabaseEngineFactory.create(settings)
    schema_index = build_schema_index()
    table_docs: list[TableDoc] = []

    for table_name in query_table_names(db_engine, settings):
        row_count = count_rows(db_engine, settings, table_name)
        if row_count == 0 and not include_empty:
            continue

        schema_doc = schema_index.get(table_name)
        if schema_doc is None:
            schema_doc = query_fallback_table_doc(db_engine, settings, table_name)

        table_docs.append(
            TableDoc(
                name=table_name,
                comment=schema_doc.get("comment", ""),
                columns=schema_doc.get("columns", []),
                row_count=row_count,
                schema_source=schema_doc.get("schema_source", ""),
                sample=query_sample(db_engine, settings, table_name, sample_size),
            )
        )

    return settings, table_docs


def format_number(value: int) -> str:
    return f"{value:,}"


def normalize_comment(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text if text else "无字段说明"


def format_sample(sample: pd.DataFrame) -> list[str]:
    if sample.empty:
        return ["    无样例数据"]

    display_sample = sample.copy()
    for column in display_sample.columns:
        display_sample[column] = display_sample[column].map(lambda value: str(value)[:80] if value is not None else "")

    table_text = display_sample.to_string(index=False, max_colwidth=80)
    return [f"    {line}" for line in table_text.splitlines()]


def render_report(
    settings: TushareIntegrationSettings,
    table_docs: list[TableDoc],
    include_types: bool,
    include_empty: bool,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_rows = sum(table_doc.row_count for table_doc in table_docs)
    database_label = (
        f"{settings.database.db_type}://{settings.database.host}:{settings.database.port}/"
        f"{settings.database.db_name}"
    )

    empty_scope = "包含 0 行空表" if include_empty else "排除 0 行空表"

    lines = [
        "已下载数据表清单（非 RAW 表）",
        "============================================================",
        f"生成时间：{generated_at}",
        f"数据库：{database_label}",
        f"统计范围：数据库中实际存在、表名不以 _raw 结尾、排除内部日志表、{empty_scope}的数据表",
        f"表数量：{len(table_docs)} 张",
        f"总数据量：{format_number(total_rows)} 行",
        "",
        "一、表清单总览",
        "------------------------------------------------------------",
    ]

    for index, table_doc in enumerate(table_docs, start=1):
        lines.append(
            f"{index:02d}. {table_doc.name}｜{table_doc.comment or '无表说明'}｜"
            f"{format_number(table_doc.row_count)} 行"
        )

    lines.extend(
        [
            "",
            "二、字段明细",
            "============================================================",
        ]
    )

    for index, table_doc in enumerate(table_docs, start=1):
        lines.extend(
            [
                "",
                "------------------------------------------------------------",
                f"{index:02d}. {table_doc.name}",
                f"表说明：{table_doc.comment or '无表说明'}",
                f"数据量：{format_number(table_doc.row_count)} 行",
                f"字段数量：{len(table_doc.columns)} 个",
                f"结构来源：{table_doc.schema_source}",
                "字段清单：",
            ]
        )

        if not table_doc.columns:
            lines.append("    无字段信息")
        else:
            for column_index, column in enumerate(table_doc.columns, start=1):
                column_name = column.get("name", "")
                column_comment = normalize_comment(column.get("comment", ""))
                if include_types and column.get("data_type"):
                    lines.append(
                        f"    {column_index:02d}. {column_name}（{column.get('data_type')}）：{column_comment}"
                    )
                else:
                    lines.append(f"    {column_index:02d}. {column_name}：{column_comment}")

        if table_doc.sample is not None:
            lines.append("样例数据：")
            lines.extend(format_sample(table_doc.sample))

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成已下载数据表清单纯文本文档")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"), help="配置文件路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 txt 文件路径")
    parser.add_argument("--include-empty", action="store_true", help="包含 0 行空表")
    parser.add_argument("--include-types", action="store_true", help="字段清单中包含字段类型")
    parser.add_argument("--sample-size", type=int, default=0, help="每张表输出的样例数据行数，默认不输出")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    output_path = Path(args.output).resolve()

    settings, table_docs = collect_table_docs(
        config_path=config_path,
        include_empty=args.include_empty,
        sample_size=args.sample_size,
    )
    report = render_report(
        settings,
        table_docs,
        include_types=args.include_types,
        include_empty=args.include_empty,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"文档已生成：{output_path}")
    print(f"表数量：{len(table_docs)}")


if __name__ == "__main__":
    main()
