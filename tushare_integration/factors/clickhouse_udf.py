from __future__ import annotations

import json
import sys

from tushare_integration.factors.engine import FactorEngine, compute_factor_rows


def _arg(row: dict, name: str, index: int):
    return row.get(name) or row.get(f"argument_{index}") or row.get(f"c{index}")


def _handle(row: dict, engine: FactorEngine) -> list[list]:
    field_names_json = _arg(row, "field_names_json", 1)
    rows_json = _arg(row, "rows_json", 2)
    if field_names_json is None or rows_json is None:
        raise ValueError(f"missing UDF arguments in row keys={sorted(row.keys())}")
    return compute_factor_rows(json.loads(field_names_json), json.loads(rows_json), engine=engine)


def main() -> None:
    engine = FactorEngine()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        result = _handle(row, engine)
        sys.stdout.write(json.dumps({"result": result}, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
