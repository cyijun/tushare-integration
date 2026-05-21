from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MAPPING_CSV = ROOT_DIR / "docs" / "prd" / "factor_mapping_readable.csv"
FIELD_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _as_series(x) -> pd.Series:
    if isinstance(x, pd.Series):
        return x
    return pd.Series(x)


def op_Ref(x: pd.Series, n: int) -> pd.Series:
    return x.shift(int(n))


def op_Mean(x: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n <= 1:
        return x.astype(float)
    return x.rolling(n, min_periods=1).mean()


def op_Sum(x: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n <= 1:
        return x.astype(float)
    return x.rolling(n, min_periods=1).sum()


def op_Std(x: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n <= 1:
        return pd.Series(np.zeros(len(x)), index=x.index)
    return x.rolling(n, min_periods=2).std(ddof=0)


def op_Max(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=1).max()


def op_Min(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=1).min()


def op_Quantile(x: pd.Series, n: int, q: float) -> pd.Series:
    return x.rolling(int(n), min_periods=1).quantile(float(q))


def op_Rank(x: pd.Series, n: int) -> pd.Series:
    n = int(n)
    return x.rolling(n, min_periods=1).apply(
        lambda a: (np.sum(a <= a[-1]) - 1) / max(len(a) - 1, 1),
        raw=True,
    )


def op_IdxMax(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=1).apply(lambda a: float(np.argmax(a)), raw=True)


def op_IdxMin(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=1).apply(lambda a: float(np.argmin(a)), raw=True)


def op_Abs(x: pd.Series) -> pd.Series:
    return x.abs()


def op_Log(x: pd.Series) -> pd.Series:
    values = np.asarray(x, dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    np.log(values, out=out, where=values > 0)
    return pd.Series(out, index=x.index if isinstance(x, pd.Series) else None)


def op_Greater(a, b) -> pd.Series:
    a = _as_series(a)
    if np.isscalar(b):
        return pd.Series(np.maximum(a.to_numpy(dtype=float), b), index=a.index)
    b = _as_series(b)
    return a.where(a >= b, b)


def op_Less(a, b) -> pd.Series:
    a = _as_series(a)
    if np.isscalar(b):
        return pd.Series(np.minimum(a.to_numpy(dtype=float), b), index=a.index)
    b = _as_series(b)
    return a.where(a <= b, b)


def op_If(cond, a, b) -> pd.Series:
    if isinstance(cond, pd.Series):
        condition = cond.astype(bool)
        index = cond.index
    else:
        condition = np.asarray(cond, dtype=bool)
        index = a.index if isinstance(a, pd.Series) else b.index if isinstance(b, pd.Series) else None
    a_values = a.to_numpy() if isinstance(a, pd.Series) else a
    b_values = b.to_numpy() if isinstance(b, pd.Series) else b
    return pd.Series(np.where(condition, a_values, b_values), index=index)


def op_Corr(a: pd.Series, b: pd.Series, n: int) -> pd.Series:
    return a.rolling(int(n), min_periods=2).corr(b)


def op_Cov(a: pd.Series, b: pd.Series, n: int) -> pd.Series:
    return a.rolling(int(n), min_periods=2).cov(b)


def op_Skew(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=3).skew()


def op_Kurt(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=4).kurt()


def op_EMA(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=int(n), adjust=False, min_periods=1).mean()


def _rolling_regression(y_window: np.ndarray, kind: str) -> float:
    n = len(y_window)
    if n < 2 or np.isnan(y_window).any():
        return np.nan
    t = np.arange(n, dtype=float)
    t_mean = t.mean()
    y_mean = y_window.mean()
    dt = t - t_mean
    dy = y_window - y_mean
    denom = np.dot(dt, dt)
    if denom == 0:
        return np.nan
    slope = np.dot(dt, dy) / denom
    if kind == "slope":
        return float(slope)
    intercept = y_mean - slope * t_mean
    y_hat = intercept + slope * t
    if kind == "resi":
        return float(y_window[-1] - y_hat[-1])
    if kind == "rsquare":
        ss_tot = np.dot(dy, dy)
        if ss_tot == 0:
            return np.nan
        ss_res = np.dot(y_window - y_hat, y_window - y_hat)
        return float(1.0 - ss_res / ss_tot)
    raise ValueError(kind)


def op_Slope(x: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n <= 1:
        return pd.Series(np.zeros(len(x)), index=x.index)
    return x.rolling(n, min_periods=2).apply(lambda a: _rolling_regression(a, "slope"), raw=True)


def op_Resi(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=2).apply(lambda a: _rolling_regression(a, "resi"), raw=True)


def op_Rsquare(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(int(n), min_periods=2).apply(lambda a: _rolling_regression(a, "rsquare"), raw=True)


OPERATORS = {
    "Ref": op_Ref,
    "Mean": op_Mean,
    "Sum": op_Sum,
    "Std": op_Std,
    "Max": op_Max,
    "Min": op_Min,
    "Quantile": op_Quantile,
    "Rank": op_Rank,
    "IdxMax": op_IdxMax,
    "IdxMin": op_IdxMin,
    "Abs": op_Abs,
    "Log": op_Log,
    "Greater": op_Greater,
    "Less": op_Less,
    "If": op_If,
    "Corr": op_Corr,
    "Cov": op_Cov,
    "Skew": op_Skew,
    "Kurt": op_Kurt,
    "EMA": op_EMA,
    "Slope": op_Slope,
    "Resi": op_Resi,
    "Rsquare": op_Rsquare,
}

ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Compare,
    ast.BoolOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Subscript,
    ast.Index,
)


def extract_fields(expr: str) -> set[str]:
    return set(FIELD_RE.findall(expr))


def _normalize_expression(expr: str) -> str:
    return FIELD_RE.sub(r'_F["\1"]', expr)


def _validate_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError(f"expression contains disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in OPERATORS:
                raise ValueError(f"unknown operator: {ast.dump(node.func)}")


@lru_cache(maxsize=4096)
def _compile_expression(expr: str):
    tree = ast.parse(_normalize_expression(expr), mode="eval")
    _validate_ast(tree)
    return compile(tree, "<factor-expr>", "eval")


def _default_mapping_csv() -> Path:
    env_path = os.environ.get("TUSHARE_FACTOR_MAPPING_CSV")
    if env_path:
        return Path(env_path)
    candidates = [
        DEFAULT_MAPPING_CSV,
        Path.cwd() / "factor_mapping_readable.csv",
        Path(__file__).resolve().parent / "factor_mapping_readable.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return DEFAULT_MAPPING_CSV


class FactorEngine:
    def __init__(self, mapping_csv: str | Path | None = None, mapping_df: pd.DataFrame | None = None):
        if mapping_df is not None:
            mapping = mapping_df.copy()
        else:
            mapping = pd.read_csv(mapping_csv or _default_mapping_csv())
        missing = {"factor_id", "expression"} - set(mapping.columns)
        if missing:
            raise ValueError(f"factor mapping missing columns: {sorted(missing)}")
        self._mapping = mapping.drop_duplicates(subset=["factor_id"]).reset_index(drop=True)
        self._by_id = {row["factor_id"]: row.to_dict() for _, row in self._mapping.iterrows()}
        mapping_text = self._mapping[["factor_id", "expression"]].to_csv(index=False)
        self.mapping_hash = hashlib.md5(mapping_text.encode("utf-8")).hexdigest()

    def list_factors(self) -> pd.DataFrame:
        return self._mapping.copy()

    def factor_ids(self) -> list[str]:
        return list(self._by_id.keys())

    def expression(self, factor_id: str) -> str:
        return self._by_id[factor_id]["expression"]

    def required_fields(self, factor_ids: Iterable[str] | None = None) -> set[str]:
        ids = list(factor_ids) if factor_ids is not None else self.factor_ids()
        fields: set[str] = set()
        for factor_id in ids:
            fields |= extract_fields(self.expression(factor_id))
        return fields

    def _evaluate(self, expr: str, fields: dict[str, pd.Series]) -> pd.Series:
        code = _compile_expression(expr)
        result = eval(code, {"__builtins__": {}, "_F": fields, **OPERATORS}, {})
        if np.isscalar(result):
            any_series = next(iter(fields.values()))
            result = pd.Series(np.full(len(any_series), float(result)), index=any_series.index)
        elif not isinstance(result, pd.Series):
            any_series = next(iter(fields.values()))
            result = pd.Series(np.asarray(result, dtype=float), index=any_series.index)
        return result.astype(float)

    @staticmethod
    def _prepare_fields(df: pd.DataFrame, needed: set[str]) -> dict[str, pd.Series]:
        aliases = {
            "volume": "vol",
            "vwap": "avg_price",
            "turnover": "turnover_rate_f",
        }
        fields: dict[str, pd.Series] = {}
        for name in needed:
            column = name if name in df.columns else aliases.get(name, name)
            if column not in df.columns:
                fields[name] = pd.Series(np.full(len(df), np.nan), index=df.index)
            else:
                fields[name] = pd.to_numeric(df[column], errors="coerce").astype(float)
        return fields

    def compute(self, df: pd.DataFrame, factor_ids: Iterable[str] | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
        ids = list(factor_ids) if factor_ids is not None else self.factor_ids()
        fields = self._prepare_fields(df, self.required_fields(ids))
        output: dict[str, pd.Series] = {}
        errors: dict[str, str] = {}
        for factor_id in ids:
            try:
                output[factor_id] = self._evaluate(self.expression(factor_id), fields)
            except Exception as exc:  # noqa: BLE001
                output[factor_id] = pd.Series(np.full(len(df), np.nan), index=df.index)
                errors[factor_id] = repr(exc)
        return pd.DataFrame(output, index=df.index), errors


def _clean_json_value(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return value


def _json_dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _record_hash(source_record_hash: str, mapping_hash: str, factor_values_json: str) -> str:
    raw = f"{source_record_hash}|{mapping_hash}|{factor_values_json}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def compute_factor_rows(field_names: list[str], rows: list[list], engine: FactorEngine | None = None) -> list[list]:
    if not rows:
        return []
    engine = engine or FactorEngine()
    prefix_columns = ["trade_date", "event_date", "available_trade_date", "source_batch_id", "source_record_hash"]
    expected_width = len(prefix_columns) + len(field_names)
    records = []
    for row in rows:
        if len(row) != expected_width:
            raise ValueError(f"row width {len(row)} does not match expected width {expected_width}")
        records.append(dict(zip(prefix_columns + field_names, row, strict=True)))

    df = pd.DataFrame(records)
    factors, errors = engine.compute(df)
    factor_ids = engine.factor_ids()

    output = []
    for index, record in enumerate(records):
        values = {factor_id: _clean_json_value(factors.at[index, factor_id]) for factor_id in factor_ids}
        value_payload = {
            "mapping_hash": engine.mapping_hash,
            "values": values,
        }
        errors_payload = {
            "mapping_hash": engine.mapping_hash,
            "errors": errors,
        }
        values_json = _json_dumps(value_payload)
        errors_json = _json_dumps(errors_payload)
        output.append(
            [
                record["event_date"],
                record["trade_date"],
                record["available_trade_date"],
                values_json,
                errors_json,
                len(factor_ids),
                record["source_batch_id"] or "",
                _record_hash(str(record["source_record_hash"] or ""), engine.mapping_hash, values_json),
            ]
        )
    return output
