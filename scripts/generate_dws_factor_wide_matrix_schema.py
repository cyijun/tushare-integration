from __future__ import annotations

import argparse
import csv
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MAPPING = ROOT_DIR / "docs" / "prd" / "factor_mapping_readable.csv"
DEFAULT_OUTPUT = ROOT_DIR / "tushare_integration" / "schema" / "dws" / "dws_stock_factor_wide_matrix.yaml"


def _load_factors(path: Path) -> list[tuple[str, str]]:
    factors = []
    seen = set()
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            factor_id = row["factor_id"].strip()
            if factor_id and factor_id not in seen:
                seen.add(factor_id)
                factors.append((factor_id, row.get("name", "").strip() or factor_id))
    return factors


def _append_column(lines: list[str], name: str, data_type: str, comment: str, length: str | None = None, nullable: bool = False) -> None:
    lines.append(f"    - name: {name}")
    lines.append(f"      data_type: {data_type}")
    if length:
        lines.append(f"      length: {length}")
    if nullable:
        lines.append("      nullable: true")
    lines.append(f"      comment: {comment}")


def render_schema(factors: list[tuple[str, str]]) -> str:
    lines = [
        "id: 3002",
        "name: dws_stock_factor_wide_matrix",
        "comment: 股票因子矩阵宽表汇总层",
        "builder: stock_factor_wide_matrix",
        "schema:",
        "  comment: 股票因子矩阵宽表汇总层",
        "  primary_key: []",
        "  partition_key:",
        "    - toYear(available_trade_date)",
        "  indexes:",
        "    - name: dws_stock_factor_wide_matrix_idx",
        "      columns:",
        "        - instrument_id",
        "        - trade_date",
        "  columns:",
    ]
    for name, data_type, length, comment in [
        ("instrument_id", "str", "64", "统一证券ID"),
        ("instrument_type", "str", "32", "证券类型"),
        ("exchange", "str", "32", "交易所"),
        ("source_code", "str", "64", "源侧证券代码"),
        ("event_date", "date", None, "业务归属日期"),
        ("trade_date", "date", None, "交易日期"),
        ("available_trade_date", "date", None, "最早可用交易日"),
    ]:
        _append_column(lines, name, data_type, comment, length=length)

    for factor_id, factor_name in factors:
        _append_column(lines, factor_id, "float", factor_name, nullable=True)

    for name, data_type, length, comment, nullable in [
        ("factor_errors_json", "json", None, "因子计算错误JSON载荷，按factor_id索引", True),
        ("factor_count", "int", None, "本批映射因子数量", False),
        ("build_time", "datetime", None, "汇总构建时间", False),
        ("source", "str", "32", "来源系统", False),
        ("source_table", "str", "255", "来源表", False),
        ("source_batch_id", "str", "512", "来源批次ID", False),
        ("source_record_hash", "str", "32", "来源记录哈希", False),
    ]:
        _append_column(lines, name, data_type, comment, length=length, nullable=nullable)

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate dws_stock_factor_wide_matrix schema from factor CSV.")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    factors = _load_factors(args.mapping)
    args.output.write_text(render_schema(factors), encoding="utf-8")
    print(f"wrote {args.output} with {len(factors)} factor columns")


if __name__ == "__main__":
    main()
