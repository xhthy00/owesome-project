"""全市各科贡献分落库：一行=一场考试×选科×线种。

粒度与 ``tb_score_indicator``（一校一线）不同：贡献分是全市切线画像，
不按学校/区县拆行。科目列对齐基础分析表「14高三各科贡献分」。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src.agent.education.line_reach import pick_col, rows_as_dicts

__all__ = [
    "ENSURE_TABLE_SQL",
    "SUBJECT_COLS",
    "UNIQUE_KEYS",
    "WRITE_COLS",
    "attach_focus_lines",
    "discover_contrib_xlsx",
    "ensure_table",
    "ensure_table_on_datasource",
    "folder_exam_hint",
    "import_contribution_from_dir",
    "parse_contribution_sheet",
    "parse_contribution_xlsx",
    "resolve_exam_name",
]

logger = logging.getLogger(__name__)

UNIQUE_KEYS: tuple[str, ...] = ("exam_name", "track", "line_name")

SUBJECT_COLS: tuple[str, ...] = (
    "yw",
    "sx",
    "yy",
    "wl",
    "ls",
    "hxzh",
    "swzh",
    "zzzh",
    "dlzh",
)

WRITE_COLS: tuple[str, ...] = (
    "exam_name",
    "exam_batch_id",
    "track",
    "line_code",
    "line_name",
    "threshold",
    "reached_count",
    *SUBJECT_COLS,
    "source",
    "updated_at",
)

_EMPTY_MARKS = {"——", "—", "--", "-", "/", "－", "–"}
_FOLDER_EXAM_HINTS: tuple[tuple[str, str], ...] = (
    ("11月", "11月"),
    ("5月", "5月"),
    ("3月", "3月"),
    ("1月", "1月"),
)
_KNOWN_EXAMS = {
    "11月": "2026届高三11月期中",
    "1月": "2026届高三1月期末",
    "3月": "2026届高三3月",
    "5月": "2026届高三5月模拟",
}
_FOCUS_FALLBACK: tuple[tuple[str, str], ...] = (
    ("211", "211线"),
    ("tz", "特控线"),
    ("bk", "本科线"),
)
_FOCUS_CODES = {code for code, _name in _FOCUS_FALLBACK}
_FOCUS_NAME = {code: name for code, name in _FOCUS_FALLBACK}

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tb_score_contribution (
    id              BIGSERIAL PRIMARY KEY,
    exam_name       VARCHAR(128) NOT NULL,
    exam_batch_id   BIGINT,
    track           VARCHAR(32)  NOT NULL DEFAULT '',
    line_code       VARCHAR(32)  NOT NULL DEFAULT '',
    line_name       VARCHAR(64)  NOT NULL,
    threshold       NUMERIC(8, 2),
    reached_count   INTEGER NOT NULL DEFAULT 0,
    yw              NUMERIC(8, 2),
    sx              NUMERIC(8, 2),
    yy              NUMERIC(8, 2),
    wl              NUMERIC(8, 2),
    ls              NUMERIC(8, 2),
    hxzh            NUMERIC(8, 2),
    swzh            NUMERIC(8, 2),
    zzzh            NUMERIC(8, 2),
    dlzh            NUMERIC(8, 2),
    source          VARCHAR(16)  NOT NULL DEFAULT 'import',
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_score_contribution UNIQUE (exam_name, track, line_name)
)
"""
_ENSURE_INDEX_SQL = [
    "ALTER TABLE tb_score_contribution ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT",
    "ALTER TABLE tb_score_contribution ADD COLUMN IF NOT EXISTS source VARCHAR(16) "
    "NOT NULL DEFAULT 'import'",
    "CREATE INDEX IF NOT EXISTS idx_score_contribution_exam ON tb_score_contribution (exam_name)",
    "CREATE INDEX IF NOT EXISTS idx_score_contribution_batch ON tb_score_contribution (exam_batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_score_contribution_line ON tb_score_contribution (line_name)",
]
_ENSURE_COMMENTS_SQL = [
    "COMMENT ON TABLE tb_score_contribution IS "
    "'全市各科贡献分：一行=一场考试×选科×线种。无学校/区县粒度。"
    "科目 NULL 表示该选科不考（Excel「——」）。权威来自基础分析表导入，禁止现算覆盖'",
    "COMMENT ON COLUMN tb_score_contribution.exam_name IS "
    "'考试名称，与 tb_exam_batch.batch_name / tb_fraction_bar.exam_name 一致'",
    "COMMENT ON COLUMN tb_score_contribution.exam_batch_id IS "
    "'考试批次ID，关联 tb_exam_batch.id'",
    "COMMENT ON COLUMN tb_score_contribution.track IS '选科方向：物理类 / 历史类'",
    "COMMENT ON COLUMN tb_score_contribution.line_code IS "
    "'线种代码 tz/bk/211；特招线=特控线 tz'",
    "COMMENT ON COLUMN tb_score_contribution.line_name IS "
    "'线种名称：特控线/本科线/211线'",
    "COMMENT ON COLUMN tb_score_contribution.threshold IS "
    "'该选科该线预测分数线，对应 Excel ZF6 贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.reached_count IS "
    "'全市该选科达线人数（总分≥threshold）'",
    "COMMENT ON COLUMN tb_score_contribution.yw IS '语文贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.sx IS '数学贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.yy IS '英语贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.wl IS '物理贡献分；历史类为空'",
    "COMMENT ON COLUMN tb_score_contribution.ls IS '历史贡献分；物理类为空'",
    "COMMENT ON COLUMN tb_score_contribution.hxzh IS '化学转换分贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.swzh IS '生物转换分贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.zzzh IS '政治转换分贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.dlzh IS '地理转换分贡献分'",
    "COMMENT ON COLUMN tb_score_contribution.source IS "
    "'import=基础分析表导入；computed=系统现算回填'",
    "COMMENT ON COLUMN tb_score_contribution.updated_at IS '最近一次写入时间'",
]


def ensure_table(session, db_type: str) -> None:
    if db_type != "pg":
        raise ValueError("tb_score_contribution 仅支持 PostgreSQL")
    ok, msg, _ = session.execute_write(ENSURE_TABLE_SQL)
    if not ok:
        raise RuntimeError(msg or "创建 tb_score_contribution 失败")
    for sql in _ENSURE_INDEX_SQL + _ENSURE_COMMENTS_SQL:
        ok, msg, _ = session.execute_write(sql)
        if not ok:
            logger.warning("ensure tb_score_contribution extra SQL failed: %s", msg)


def ensure_table_on_datasource(db_type: str, config: dict[str, Any]) -> None:
    from src.datasource.db.db import WriteDbSession

    with WriteDbSession(db_type, config) as session:
        ensure_table(session, db_type)
        session.commit()


def folder_exam_hint(folder_name: str) -> str:
    text = str(folder_name or "")
    for token, key in _FOLDER_EXAM_HINTS:
        if token in text:
            return key
    return ""


def resolve_exam_name(hint: str, batch_names: Sequence[str]) -> str:
    key = str(hint or "").strip()
    if not key:
        return ""
    hits: list[str] = []
    for name in batch_names:
        n = str(name or "").strip()
        if not n or key not in n:
            continue
        if key == "1月" and "11月" in n:
            continue
        hits.append(n)
    if not hits:
        return _KNOWN_EXAMS.get(key, "")
    if len(hits) == 1:
        return hits[0]
    gao = [n for n in hits if "高三" in n]
    return (gao or hits)[0]


def discover_contrib_xlsx(root: str | Path) -> list[tuple[Path, Path]]:
    base = Path(root)
    out: list[tuple[Path, Path]] = []
    if not base.is_dir():
        return out
    for folder in sorted(base.iterdir(), key=lambda p: p.name):
        if not folder.is_dir():
            continue
        hits = [
            p
            for p in folder.iterdir()
            if p.is_file()
            and p.suffix.lower() == ".xlsx"
            and p.name.startswith("14")
            and not p.name.startswith("~$")
        ]
        hits.sort(key=lambda p: p.name)
        for path in hits:
            out.append((folder, path))
    return out


def parse_contribution_sheet(rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    track = ""
    out: list[dict[str, Any]] = []
    for raw in rows:
        cells = list(raw) if raw else []
        while len(cells) < 11:
            cells.append(None)
        header = _header_track(cells[0])
        if header and _cell_num(cells[0]) is None:
            track = header
            continue
        if not track:
            continue
        reached = _cell_int(cells[0])
        threshold = _cell_num(cells[1])
        if reached is None or threshold is None:
            continue
        rec: dict[str, Any] = {
            "track": track,
            "reached_count": reached,
            "threshold": threshold,
        }
        for i, col in enumerate(SUBJECT_COLS):
            rec[col] = _cell_num(cells[i + 2])
        out.append(rec)
    return out


def parse_contribution_xlsx(path: str | Path) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb.active
        rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    finally:
        wb.close()
    return parse_contribution_sheet(rows)


def attach_focus_lines(
    rows: Sequence[dict[str, Any]],
    bars: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    bar_by_track: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        track = str(bar.get("track") or "")
        code = _focus_code(str(bar.get("line_code") or ""), str(bar.get("line_name") or ""))
        if code not in _FOCUS_CODES:
            continue
        item = dict(bar)
        item["line_code"] = code
        item["line_name"] = _FOCUS_NAME[code]
        bar_by_track.setdefault(track, []).append(item)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("track") or ""), []).append(dict(row))

    out: list[dict[str, Any]] = []
    for track, group in grouped.items():
        used: set[str] = set()
        for rec in group:
            matched = _match_bar(rec.get("threshold"), bar_by_track.get(track) or [])
            if matched:
                rec["line_code"] = matched["line_code"]
                rec["line_name"] = matched["line_name"]
                used.add(str(matched["line_code"]))
        leftover = [rec for rec in group if rec.get("line_code") not in _FOCUS_CODES]
        leftover.sort(key=lambda r: float(r.get("threshold") or 0), reverse=True)
        unused = [pair for pair in _FOCUS_FALLBACK if pair[0] not in used]
        for rec, (code, name) in zip(leftover, unused):
            rec["line_code"] = code
            rec["line_name"] = name
        for rec in group:
            if rec.get("line_code") in _FOCUS_CODES:
                rec["line_name"] = _FOCUS_NAME[str(rec["line_code"])]
                out.append(rec)
    return out


def import_contribution_from_dir(
    db_type: str,
    config: dict[str, Any],
    root: str | Path,
) -> dict[str, Any]:
    from src.datasource.db.db import WriteDbSession

    files = discover_contrib_xlsx(root)
    files_out: list[dict[str, Any]] = []
    errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    with WriteDbSession(db_type, config) as session:
        ensure_table(session, db_type)
        batches = _load_exam_batches(session)
        names = [str(item["batch_name"]) for item in batches]
        id_by_name = {str(item["batch_name"]): item["id"] for item in batches}
        for folder, path in files:
            hint = folder_exam_hint(folder.name)
            exam = resolve_exam_name(hint, names)
            info: dict[str, Any] = {
                "folder": folder.name,
                "file": path.name,
                "hint": hint,
                "exam_name": exam,
                "rows": 0,
            }
            if not exam:
                msg = f"{folder.name}: 无法匹配考试名称"
                info["error"] = msg
                errors.append(msg)
                files_out.append(info)
                continue
            try:
                parsed = parse_contribution_xlsx(path)
            except Exception as exc:
                msg = f"{path.name}: 解析失败 {exc}"
                info["error"] = msg
                errors.append(msg)
                files_out.append(info)
                continue
            labeled = attach_focus_lines(parsed, _load_focus_bars(session, exam))
            batch_id = id_by_name.get(exam)
            for rec in labeled:
                rec["exam_name"] = exam
                rec["exam_batch_id"] = batch_id
                rec["source"] = "import"
            info["rows"] = len(labeled)
            files_out.append(info)
            all_rows.extend(labeled)
        written = _upsert_rows(session, all_rows)
        session.commit()
    return {
        "files": files_out,
        "row_count": written,
        "errors": errors,
    }


def _header_track(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "历史" in text or text in {"文", "文科"} or text.startswith("文"):
        return "历史类"
    if "物理" in text or text in {"理", "理科"} or text.startswith("理"):
        return "物理类"
    return ""


def _cell_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in _EMPTY_MARKS or set(text) <= set("—－-–—/ \t"):
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _cell_int(value: Any) -> int | None:
    num = _cell_num(value)
    if num is None:
        return None
    return int(round(num))


def _focus_code(line_code: str, line_name: str) -> str:
    code = str(line_code or "").strip().lower()
    if code in _FOCUS_CODES:
        return code
    name = str(line_name or "")
    if "211" in name:
        return "211"
    if "特控" in name or "特招" in name:
        return "tz"
    if "本科" in name:
        return "bk"
    return code


def _match_bar(threshold: Any, bars: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if threshold is None:
        return None
    best: dict[str, Any] | None = None
    best_gap = 99.0
    for bar in bars:
        raw = bar.get("threshold")
        if raw is None:
            continue
        gap = abs(float(threshold) - float(raw))
        if gap <= 0.51 and gap < best_gap:
            best = bar
            best_gap = gap
    return best


def _query_dicts(
    session, sql: str, params: tuple | list | None = None
) -> tuple[bool, list[dict[str, Any]]]:
    ok, _msg, result = session.execute_query(sql, params)
    if not ok or not isinstance(result, dict):
        return False, []
    cols = [str(c) for c in (result.get("columns") or [])]
    return True, rows_as_dicts(cols, result.get("rows") or [])


def _load_exam_batches(session) -> list[dict[str, Any]]:
    ok, rows = _query_dicts(
        session,
        "SELECT id, batch_name FROM tb_exam_batch ORDER BY id DESC LIMIT 500",
    )
    if not ok:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(pick_col(row, "batch_name", "exam_name", "name") or "").strip()
        raw_id = pick_col(row, "id")
        try:
            batch_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            batch_id = None
        if batch_id is None or not name:
            continue
        out.append({"id": batch_id, "batch_name": name})
    return out


def _load_focus_bars(session, exam_name: str) -> list[dict[str, Any]]:
    ok, rows = _query_dicts(
        session,
        "SELECT DISTINCT track, line_code, line_name, threshold "
        "FROM tb_score_indicator WHERE exam_name = %s AND threshold IS NOT NULL",
        (exam_name,),
    )
    return rows if ok else []


def _upsert_rows(session, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = datetime.now()
    param_rows = []
    for row in rows:
        rec = dict(row)
        rec.setdefault("source", "import")
        rec["updated_at"] = now
        param_rows.append(tuple(rec.get(col) for col in WRITE_COLS))
    ok, msg, _ = session.execute_upsert_batch(
        "tb_score_contribution",
        list(WRITE_COLS),
        UNIQUE_KEYS,
        param_rows,
    )
    if not ok:
        raise RuntimeError(msg or "写入 tb_score_contribution 失败")
    return len(param_rows)
