"""预测线达线指标落库：写 tb_fraction_bar，并按考试重算 tb_score_indicator。"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.agent.education.line_reach import (
    _LINE_SUFFIX_LABEL,
    _WIDE_LINE_RE,
    filter_fraction_bars,
    line_code_of,
    normalize_fraction_bars,
    pick_col,
    rows_as_dicts,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LINE_CATALOG",
    "agg_rows_to_indicator_rows",
    "bars_to_wide_row",
    "ensure_table",
    "exam_batch_id_from_bars",
    "list_fraction_bars",
    "recompute_exams",
    "recompute_if_bars_exist",
    "upsert_fraction_bar_and_recompute",
]

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OV_COL_HINTS = [
    "zf6m",
    "zf4m",
    "zf3m",
    "zf",
    "total",
    "total_score",
    "dq",
    "district",
    "qx",
    "xx",
    "school_id",
    "school",
    "exam_name",
    "exam",
    "ksmc",
    "xkkm",
    "xkqk",
    "track",
    "xkfx",
]
_INDICATOR_COLS = [
    "exam_name",
    "exam_batch_id",
    "track",
    "district",
    "school_id",
    "school_name",
    "line_code",
    "line_name",
    "threshold",
    "candidates",
    "reached_count",
    "reach_rate",
]
_INDICATOR_CONFLICT = ("exam_name", "track", "district", "school_id", "line_name")
LINE_CATALOG: list[dict[str, str]] = [
    {"line_code": code, "line_name": name} for code, name in _LINE_SUFFIX_LABEL.items()
]

_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tb_score_indicator (
    id              BIGSERIAL PRIMARY KEY,
    exam_name       VARCHAR(128) NOT NULL,
    exam_batch_id   BIGINT,
    track           VARCHAR(32)  NOT NULL DEFAULT '',
    district        VARCHAR(64)  NOT NULL DEFAULT '',
    school_id       VARCHAR(128) NOT NULL DEFAULT '',
    school_name     VARCHAR(128) NOT NULL DEFAULT '',
    line_code       VARCHAR(32)  NOT NULL DEFAULT '',
    line_name       VARCHAR(64)  NOT NULL,
    threshold       NUMERIC(8, 2),
    candidates      INTEGER NOT NULL DEFAULT 0,
    reached_count   INTEGER NOT NULL DEFAULT 0,
    reach_rate      NUMERIC(8, 4),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_score_indicator UNIQUE (exam_name, track, district, school_id, line_name)
)
"""
_ENSURE_INDEX_SQL = [
    "ALTER TABLE tb_score_indicator ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT",
    "CREATE INDEX IF NOT EXISTS idx_score_indicator_exam ON tb_score_indicator (exam_name)",
    "CREATE INDEX IF NOT EXISTS idx_score_indicator_batch ON tb_score_indicator (exam_batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_score_indicator_district ON tb_score_indicator (district)",
    "CREATE INDEX IF NOT EXISTS idx_score_indicator_school ON tb_score_indicator (school_id)",
]
_ENSURE_COMMENTS_SQL = [
    "COMMENT ON TABLE tb_score_indicator IS "
    "'预测线达线预计算：一行=一场考试×选科×学校×线种。区县/全市聚合须 SUM 后重算率，禁止 AVG(reach_rate)'",
    "COMMENT ON COLUMN tb_score_indicator.exam_name IS '考试名称，与 tb_exam_batch.batch_name 一致'",
    "COMMENT ON COLUMN tb_score_indicator.exam_batch_id IS '考试批次ID，关联 tb_exam_batch.id，与 exam_name 对应'",
    "COMMENT ON COLUMN tb_score_indicator.track IS '选科方向：物理类 / 历史类'",
    "COMMENT ON COLUMN tb_score_indicator.district IS '区县'",
    "COMMENT ON COLUMN tb_score_indicator.school_id IS '脱敏校码，与 overview.xx、权限 school_id 对齐'",
    "COMMENT ON COLUMN tb_score_indicator.school_name IS '学校展示名'",
    "COMMENT ON COLUMN tb_score_indicator.line_code IS '线种代码 tz/bk/ty/ms/yy/211/985/qb/nd'",
    "COMMENT ON COLUMN tb_score_indicator.line_name IS '线种名称'",
    "COMMENT ON COLUMN tb_score_indicator.threshold IS '该选科该线预测分数线'",
    "COMMENT ON COLUMN tb_score_indicator.candidates IS '该校该选科参考人数'",
    "COMMENT ON COLUMN tb_score_indicator.reached_count IS '达线人数（总分≥threshold）'",
    "COMMENT ON COLUMN tb_score_indicator.reach_rate IS '学校粒度达线率 0-100；区县/全市禁止对本列求平均'",
]


def _ident(name: str) -> str:
    raw = str(name or "").strip()
    if not _SAFE_IDENT.fullmatch(raw):
        raise ValueError(f"非法列名: {name}")
    return raw


def _quote(name: str, db_type: str) -> str:
    ident = _ident(name)
    return f"`{ident}`" if db_type == "mysql" else ident


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _track_prefix(track: str) -> str:
    t = _str(track)
    if "物理" in t or t.lower() == "wl":
        return "wl"
    if "历史" in t or "史" in t or "历" in t or t.lower() == "ls":
        return "ls"
    return ""


def _col_lower_map(columns: list[str]) -> dict[str, str]:
    return {str(c).lower(): str(c) for c in columns if str(c).strip()}


def _exam_col(columns: list[str]) -> str | None:
    lower = _col_lower_map(columns)
    for key in ("exam_name", "exam", "ksmc"):
        if key in lower:
            return lower[key]
    return None


def _wide_column_for(columns: list[str], track: str, line_code: str) -> str | None:
    prefix = _track_prefix(track)
    code = _str(line_code).lower()
    if not prefix or not code:
        return None
    lower = _col_lower_map(columns)
    for cand in (f"{prefix}_score_{code}", f"{prefix}_socre_{code}"):
        if cand in lower:
            return lower[cand]
    return None


def _line_catalog_for_columns(columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in LINE_CATALOG:
        code = item["line_code"]
        wl = _wide_column_for(columns, "物理类", code)
        ls = _wide_column_for(columns, "历史类", code)
        if not wl and not ls:
            continue
        out.append(
            {
                "line_code": code,
                "line_name": item["line_name"],
                "wl_column": wl,
                "ls_column": ls,
            }
        )
    return out


def _batch_id_col(columns: list[str]) -> str | None:
    return _col_lower_map(columns).get("exam_batch_id")


def _int_id(value: Any) -> int | None:
    n = _num(value)
    if n is None:
        return None
    return int(n)


def bars_to_wide_row(
    exam_name: str,
    lines: list[dict[str, Any]],
    columns: list[str],
    exam_batch_id: int | None = None,
) -> dict[str, Any]:
    """把录入的线种列表压成 tb_fraction_bar 宽表一行（只含库中真实存在的列）。"""
    exam_col = _exam_col(columns)
    if not exam_col:
        raise ValueError("tb_fraction_bar 缺少考试列")
    row: dict[str, Any] = {exam_col: _str(exam_name)}
    batch_col = _batch_id_col(columns)
    if batch_col and exam_batch_id is not None:
        row[batch_col] = int(exam_batch_id)
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        col = _wide_column_for(columns, _str(line.get("track")), _str(line.get("line_code")))
        if not col:
            continue
        row[col] = _num(line.get("threshold"))
    return row


def exam_batch_id_from_bars(
    exam_name: str,
    bar_rows: list[dict[str, Any]] | None,
) -> int | None:
    """从分数线宽表行取出与 exam_name 对应的 exam_batch_id。"""
    exam = _str(exam_name)
    if not exam:
        return None
    for row in bar_rows or []:
        if not isinstance(row, dict):
            continue
        name = _str(pick_col(row, "exam_name", "exam", "ksmc"))
        if name != exam:
            continue
        found = _int_id(pick_col(row, "exam_batch_id"))
        if found is not None:
            return found
    return None


def _lookup_exam_batch_id(session, exam_name: str) -> int | None:
    exam = _str(exam_name)
    if not exam:
        return None
    ok, rows, _ = _query_dicts(
        session,
        "SELECT id FROM tb_exam_batch WHERE batch_name = %s ORDER BY id LIMIT 1",
        (exam,),
    )
    if not ok or not rows:
        return None
    return _int_id(pick_col(rows[0], "id"))


def agg_rows_to_indicator_rows(
    agg_rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    exam_name: str,
    exam_batch_id: int | None = None,
) -> list[dict[str, Any]]:
    """学校×选科聚合行 + 分数线 → tb_score_indicator 长表行。"""
    exam = _str(exam_name)
    batch_id = _int_id(exam_batch_id)
    out: list[dict[str, Any]] = []
    for row in agg_rows or []:
        if not isinstance(row, dict):
            continue
        track = _str(row.get("track"))
        district = _str(row.get("district")) or "未知区县"
        school = _str(row.get("school_name") or row.get("school_id")) or "未知学校"
        candidates = int(_num(row.get("candidates")) or 0)
        for i, bar in enumerate(bars or []):
            bar_track = _str(bar.get("track"))
            if bar_track and track and bar_track != track:
                continue
            line_name = _str(bar.get("line_name"))
            if not line_name:
                continue
            hit = int(_num(row.get(f"r{i}")) or 0)
            rate = round(hit * 100.0 / candidates, 4) if candidates else 0.0
            out.append(
                {
                    "exam_name": exam,
                    "exam_batch_id": batch_id,
                    "track": track or bar_track,
                    "district": district,
                    "school_id": school,
                    "school_name": school,
                    "line_code": line_code_of(bar),
                    "line_name": line_name,
                    "threshold": _num(bar.get("threshold")),
                    "candidates": candidates,
                    "reached_count": hit,
                    "reach_rate": rate,
                }
            )
    return out


def _query_dicts(session, sql: str, params: tuple | list | None = None) -> tuple[bool, list[dict[str, Any]], list[str]]:
    ok, _msg, result = session.execute_query(sql, params)
    if not ok or not isinstance(result, dict):
        return False, [], []
    cols = [str(c) for c in (result.get("columns") or [])]
    return True, rows_as_dicts(cols, result.get("rows") or []), cols


def _probe_columns(session, table: str) -> list[str]:
    ok, _rows, cols = _query_dicts(session, f"SELECT * FROM {_ident(table)} LIMIT 0")
    return cols if ok else []


def ensure_table(session, db_type: str) -> None:
    if db_type != "pg":
        raise ValueError("tb_score_indicator 仅支持 PostgreSQL")
    ok, msg, _ = session.execute_write(_ENSURE_TABLE_SQL)
    if not ok:
        raise RuntimeError(msg or "创建 tb_score_indicator 失败")
    for sql in _ENSURE_INDEX_SQL + _ENSURE_COMMENTS_SQL:
        ok, msg, _ = session.execute_write(sql)
        if not ok:
            logger.warning("ensure_table extra SQL failed: %s", msg)


def _resolve_exam_batch(session, exam_batch_id: int) -> str:
    ok, rows, _ = _query_dicts(
        session,
        "SELECT batch_name FROM tb_exam_batch WHERE id = %s LIMIT 1",
        (int(exam_batch_id),),
    )
    if not ok:
        raise RuntimeError("查询 tb_exam_batch 失败")
    if not rows:
        raise ValueError("考试批次不存在")
    name = _str(pick_col(rows[0], "batch_name", "exam_name", "name"))
    if not name:
        raise ValueError("考试批次名称为空")
    return name


def _load_exam_batches(session) -> list[dict[str, Any]]:
    ok, rows, _ = _query_dicts(
        session,
        "SELECT id, batch_name FROM tb_exam_batch ORDER BY id DESC LIMIT 500",
    )
    if not ok:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        bid = _int_id(pick_col(row, "id"))
        name = _str(pick_col(row, "batch_name", "exam_name", "name"))
        if bid is None or not name:
            continue
        out.append({"id": bid, "batch_name": name})
    return out


def _upsert_wide_fraction_bar(session, db_type: str, exam_name: str, wide: dict[str, Any], columns: list[str]) -> str:
    exam_col = _exam_col(columns)
    if not exam_col:
        raise ValueError("tb_fraction_bar 缺少考试列")
    exam = _str(exam_name)
    batch_col = _batch_id_col(columns)
    batch_id = _int_id(wide.get(batch_col)) if batch_col else None
    where_col, where_val = exam_col, exam
    if batch_col and batch_id is not None:
        where_col, where_val = batch_col, batch_id
    ok, existing, _ = _query_dicts(
        session,
        f"SELECT {_quote(exam_col, db_type)} FROM tb_fraction_bar "
        f"WHERE {_quote(where_col, db_type)} = %s LIMIT 1",
        (where_val,),
    )
    if not ok:
        raise RuntimeError("查询 tb_fraction_bar 失败")
    if not existing and where_col != exam_col:
        ok, existing, _ = _query_dicts(
            session,
            f"SELECT {_quote(exam_col, db_type)} FROM tb_fraction_bar "
            f"WHERE {_quote(exam_col, db_type)} = %s LIMIT 1",
            (exam,),
        )
        if not ok:
            raise RuntimeError("查询 tb_fraction_bar 失败")
        if existing:
            where_col, where_val = exam_col, exam
    set_cols = [c for c in wide if c != where_col]
    if existing:
        if not set_cols:
            return "updated"
        sets = ", ".join(f"{_quote(c, db_type)} = %s" for c in set_cols)
        params = tuple(wide[c] for c in set_cols) + (where_val,)
        ok, msg, _ = session.execute_write(
            f"UPDATE tb_fraction_bar SET {sets} WHERE {_quote(where_col, db_type)} = %s",
            params,
        )
        if not ok:
            raise RuntimeError(msg or "更新 tb_fraction_bar 失败")
        return "updated"
    insert_cols = list(wide.keys())
    placeholders = ", ".join(["%s"] * len(insert_cols))
    col_sql = ", ".join(_quote(c, db_type) for c in insert_cols)
    params = tuple(wide[c] for c in insert_cols)
    ok, msg, _ = session.execute_write(
        f"INSERT INTO tb_fraction_bar ({col_sql}) VALUES ({placeholders})",
        params,
    )
    if not ok:
        raise RuntimeError(msg or "写入 tb_fraction_bar 失败")
    return "inserted"


def _load_bar_rows(session) -> list[dict[str, Any]]:
    ok, rows, _ = _query_dicts(session, "SELECT * FROM tb_fraction_bar LIMIT 500")
    return rows if ok else []


def _indicator_write_cols(session) -> list[str]:
    probed = _col_lower_map(_probe_columns(session, "tb_score_indicator"))
    return [c for c in _INDICATOR_COLS if c.lower() in probed]


def _recompute_one(
    session,
    db_type: str,
    exam_name: str,
    bar_rows: list[dict[str, Any]] | None = None,
    exam_batch_id: int | None = None,
) -> dict[str, Any]:
    from src.agent.education.api import _overview_agg_sql

    exam = _str(exam_name)
    ensure_table(session, db_type)
    raw_bars = bar_rows if bar_rows is not None else _load_bar_rows(session)
    batch_id = _int_id(exam_batch_id) or exam_batch_id_from_bars(exam, raw_bars)
    if batch_id is None:
        batch_id = _lookup_exam_batch_id(session, exam)
    bars = filter_fraction_bars(normalize_fraction_bars(raw_bars), exam_name=exam, track="")
    ov_cols = list(_OV_COL_HINTS)
    agg_sql = _overview_agg_sql(ov_cols, bars, exam_name=exam, track="")
    agg_rows: list[dict[str, Any]] = []
    if agg_sql:
        ok, agg_rows, _ = _query_dicts(session, agg_sql)
        if not ok:
            probed = _probe_columns(session, "tb_score_overview")
            if probed:
                agg_sql = _overview_agg_sql(probed, bars, exam_name=exam, track="")
                if agg_sql:
                    ok, agg_rows, _ = _query_dicts(session, agg_sql)
                    if not ok:
                        agg_rows = []
    indicator_rows = agg_rows_to_indicator_rows(
        agg_rows, bars, exam_name=exam, exam_batch_id=batch_id
    )
    ok, msg, _ = session.execute_write(
        "DELETE FROM tb_score_indicator WHERE exam_name = %s",
        (exam,),
    )
    if not ok:
        raise RuntimeError(msg or "清理 tb_score_indicator 失败")
    write_cols = _indicator_write_cols(session)
    if indicator_rows and write_cols:
        param_rows = [
            tuple(row.get(c) for c in write_cols) for row in indicator_rows
        ]
        ok, msg, _ = session.execute_upsert_batch(
            "tb_score_indicator",
            write_cols,
            _INDICATOR_CONFLICT,
            param_rows,
        )
        if not ok:
            raise RuntimeError(msg or "写入 tb_score_indicator 失败")
    return {
        "exam_name": exam,
        "exam_batch_id": batch_id,
        "bar_count": len(bars),
        "indicator_rows": len(indicator_rows),
        "empty_scores": not indicator_rows,
    }


def list_fraction_bars(db_type: str, config: dict[str, Any]) -> dict[str, Any]:
    from src.datasource.db.db import WriteDbSession

    with WriteDbSession(db_type, config) as session:
        columns = _probe_columns(session, "tb_fraction_bar")
        rows = _load_bar_rows(session)
        batches = _load_exam_batches(session)
    catalog = _line_catalog_for_columns(columns)
    exams: list[dict[str, Any]] = []
    for row in rows:
        exam = _str(pick_col(row, "exam_name", "exam", "ksmc"))
        if not exam:
            continue
        lines: list[dict[str, Any]] = []
        for col, val in row.items():
            m = _WIDE_LINE_RE.match(str(col).strip())
            if not m:
                continue
            thr = _num(val)
            code = m.group(2).lower()
            lines.append(
                {
                    "track": "物理类" if m.group(1).lower() == "wl" else "历史类",
                    "line_code": code,
                    "line_name": _LINE_SUFFIX_LABEL.get(code, code),
                    "threshold": thr,
                    "column": str(col),
                }
            )
        exams.append(
            {
                "exam_name": exam,
                "exam_batch_id": _int_id(pick_col(row, "exam_batch_id")),
                "lines": lines,
            }
        )
    exams.sort(key=lambda x: str(x.get("exam_name") or ""))
    return {
        "columns": columns,
        "line_catalog": catalog,
        "exams": exams,
        "batches": batches,
    }


def upsert_fraction_bar_and_recompute(
    db_type: str,
    config: dict[str, Any],
    *,
    exam_name: str = "",
    lines: list[dict[str, Any]],
    exam_batch_id: int | None = None,
) -> dict[str, Any]:
    from src.datasource.db.db import WriteDbSession

    with WriteDbSession(db_type, config) as session:
        exam = _str(exam_name)
        batch_id = _int_id(exam_batch_id)
        if batch_id is not None:
            exam = _resolve_exam_batch(session, batch_id)
        if not exam:
            raise ValueError("请选择考试")
        columns = _probe_columns(session, "tb_fraction_bar")
        if not columns:
            raise RuntimeError("无法读取 tb_fraction_bar 列")
        wide = bars_to_wide_row(exam, lines, columns, exam_batch_id=batch_id)
        action = _upsert_wide_fraction_bar(session, db_type, exam, wide, columns)
        stats = _recompute_one(session, db_type, exam, exam_batch_id=batch_id)
        session.commit()
    stats["fraction_bar"] = action
    stats["exam_batch_id"] = batch_id
    return stats


def recompute_exams(
    db_type: str,
    config: dict[str, Any],
    exam_names: list[str] | None = None,
) -> dict[str, Any]:
    from src.datasource.db.db import WriteDbSession

    with WriteDbSession(db_type, config) as session:
        bar_rows = _load_bar_rows(session)
        bars_all = normalize_fraction_bars(bar_rows)
        available = sorted({_str(b.get("exam_name")) for b in bars_all if b.get("exam_name")})
        targets = [_str(x) for x in (exam_names or []) if _str(x)]
        if not targets:
            targets = available
        results: list[dict[str, Any]] = []
        skipped: list[str] = []
        for exam in targets:
            if exam not in available:
                skipped.append(exam)
                continue
            results.append(_recompute_one(session, db_type, exam, bar_rows=bar_rows))
        session.commit()
    return {
        "exams": results,
        "skipped": skipped,
        "indicator_rows": sum(int(x.get("indicator_rows") or 0) for x in results),
    }


def recompute_if_bars_exist(
    db_type: str,
    config: dict[str, Any],
    exam_names: list[str] | set[str],
) -> dict[str, Any] | None:
    names = [_str(x) for x in exam_names if _str(x)]
    if not names:
        return None
    return recompute_exams(db_type, config, names)
