"""教育 SQL 护栏：致命项拦截，其余告警。"""

from __future__ import annotations

import re
from typing import Sequence

try:
    import sqlglot
    from sqlglot import exp as _sg_exp
except Exception:  # pragma: no cover - 运行环境缺 sqlglot 时跳过 AST 规则
    sqlglot = None  # type: ignore[assignment]
    _sg_exp = None  # type: ignore[assignment]

_AVG_REACH = re.compile(r"\bAVG\s*\(\s*reach_rate\s*\)", re.IGNORECASE)
_MONTH_DISTRICT = re.compile(r"'月[\u4e00-\u9fff]{1,6}区'")
_SUBJECT_COLS = (
    "ywzw", "sxkg", "yyzw", "hxzh", "swzh", "zzzh", "dlzh",
    "yw", "sx", "yy", "wl", "hx", "sw", "ls", "zz", "dl",
)
_SUBJECT_COL_ALT = "|".join(_SUBJECT_COLS)
_SUBJECT_STAT = re.compile(
    r"\b(?:AVG|MEDIAN|MIN|MAX|SUM|STDDEV(?:_SAMP|_POP)?|STDEV(?:_SAMP|_POP)?|"
    r"VAR(?:_SAMP|_POP)?|VARIANCE)\s*\(\s*(?:[\w]+\.)?(?P<col>"
    + _SUBJECT_COL_ALT
    + r")\s*\)",
    re.IGNORECASE,
)
_PERCENTILE_SUBJECT = re.compile(
    r"PERCENTILE_\w+\s*\([^)]*\)\s*WITHIN\s+GROUP\s*\(\s*ORDER\s+BY\s+"
    r"(?:[\w]+\.)?(?P<col>" + _SUBJECT_COL_ALT + r")\b",
    re.IGNORECASE,
)


def _subject_stat_excludes_zero(sql: str, col: str, match_end: int) -> bool:
    """聚合后有 FILTER (WHERE col>0)，或整句有 AND/WHERE col>0。"""
    tail = sql[match_end : match_end + 180]
    col_re = re.escape(col)
    if re.search(rf"FILTER\s*\(\s*WHERE\s+[\w.]*{col_re}\s*>\s*0", tail, re.I):
        return True
    if re.search(rf"\b(?:AND|WHERE)\s+[\w.]*{col_re}\s*>\s*0", sql, re.I):
        return True
    return False


_EQ_LIT = re.compile(
    r"(?:exam_name|district|dq|school_name|s_name|xx|bj|class_name)\s*=\s*'([^']+)'",
    re.IGNORECASE,
)
#: 化/生/政/地必须用转换分。只拦独立标识符 hx/sw/zz/dl（含 t.hx），
#: 不拦 hxzh，也不拦别名 rk_hx、CTE hx_rk、ssw（尾缀碰巧是 sw）。
_RAW_ELECTIVE_COL = re.compile(
    r"(?<![\w])(?:(?P<alias>[\w]+)\.)?(?P<col>hx|sw|zz|dl)(?![\w])",
    re.IGNORECASE,
)
_SQL_STR_LIT = re.compile(r"'(?:''|[^'])*'")
#: 未选考 rank 为 NULL 时用 999… 哨兵再与 Top-N 比较，会把全体未选考算成弱科。
_WEAK_RANK_SENTINEL = re.compile(
    r"(?:COALESCE|IFNULL)\s*\(\s*[^,]+,\s*9{3,}\s*\)\s*(?:>|>=)\s*\d+",
    re.IGNORECASE | re.DOTALL,
)
#: RANK 的第一排序列是选考分。未排除 0 分时，未选考会排到榜尾并被当成「名次之外」。
_ELECTIVE_RANK_OVER = re.compile(
    r"\b(?:RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\(\s*"
    r"(?:PARTITION\s+BY\s+[\w.,\s]+)?ORDER\s+BY\s+"
    r"(?:[\w.]+\.)?(?P<col>hxzh|swzh|zzzh|dlzh|ls|wl)\b",
    re.IGNORECASE | re.DOTALL,
)
_WEAK_RANK_SENTINEL_MSG = (
    "检测到用 COALESCE/IFNULL(名次, 999…) > N 把未选考（名次为 NULL）算成弱科。"
    "弱科判定须该科分数>0 且名次>N；禁止哨兵值填充未选考。"
)
_RANK_PARTITION_BY_BJ = re.compile(
    r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(\s*\)\s*OVER\s*\("
    r"[^)]*PARTITION\s+BY\s+[^)]*\bbj\b",
    re.IGNORECASE | re.DOTALL,
)
_COUNT_DISTINCT_BJ = re.compile(
    r"COUNT\s*\(\s*DISTINCT\s+(?:[\w]+\.)?bj\s*\)",
    re.IGNORECASE,
)
_RANK_FN = re.compile(r"\b(?:RANK|DENSE_RANK)\s*\(", re.IGNORECASE)
_SCOPE_COL = re.compile(
    r"\b(?:xx|school_name|s_name|school_code|bj|class_name)\b",
    re.IGNORECASE,
)
_SCOPE_PRED = re.compile(
    r"\b(?:(?:[\w]+\.)?(?:xx|school_name|s_name|school_code|bj|class_name))\s*"
    r"(?:LIKE|=|IN\s*\(|ILIKE)",
    re.IGNORECASE,
)
_RANK_RESULT_COL = re.compile(
    # rank_yw / yw_rank：\brank\b 不匹配下划线词中段，须显式 rank_ / _rank
    r"排名|校排|班排|名次|city_rank|全市第|rank_|_rank|\brank\b|rk_|_rk\b",
    re.IGNORECASE,
)
_RANK_POOL_SHRINK_MSG = (
    "检测到全市排名窗口与目标校/班过滤写在同一层，或仅用 EXISTS 代替外层过滤："
    "WHERE 会先把参赛池收成 1 行/校，RANK 必然全是第 1，或结果表混入他校第 1 行。"
    "必须先对全市 GROUP BY xx（班级再加 bj）做 RANK()/COUNT(*) OVER()，"
    "再在外层 WHERE xx/bj 过滤目标；禁止同层 WHERE 校/班，禁止只用 EXISTS 判断目标校是否存在。"
)
_SUBJECT_RANK_NULL_POOL_MSG = (
    "检测到学科全市排名把「该科均分为空」的学校算进参赛池："
    "Postgres 下 ORDER BY 均分 DESC 时 NULL 会占掉前列，名次与分母都会偏。"
    "必须按科先 WHERE 该科均分 IS NOT NULL（或 >0）再 RANK()/COUNT(*) OVER()；"
    "推荐 ORDER BY 均分 DESC NULLS LAST；"
    "禁止 PARTITION BY 学科/subject 时带着空均分行一起排。"
)
_SUBJECT_MUTUAL_RANK_MSG = (
    "检测到把「各科全市均分」UNION 后做 RANK()/COUNT(*) OVER()："
    "这是科目互比，参赛校数会变成科目数（常为 8/9），不是全市学校排名。"
    "优势/薄弱学科必须先 GROUP BY xx 算各校各科均分，再按科 RANK()/COUNT(*) OVER()，"
    "外层再 WHERE 目标校；禁止对无学校维度的学科 UNION 行排名。"
)
_CROSS_SUBJECT_MIXED_POOL_MSG = (
    "检测到各校各科均分先 UNION 成「一校一科一行」，再整体 RANK() 且未 PARTITION BY 学科："
    "参赛池会变成「学科数×学校数」（常为 200+），名次不是该科全市校排。"
    "必须按科分别 RANK()/COUNT(*) OVER()（每个 UNION 分支内排，或 PARTITION BY 学科），"
    "外层再 WHERE 目标校。"
)
_RUNNING_COUNT_AS_POOL_MSG = (
    "检测到 COUNT(*) OVER (ORDER BY …) 当作参赛校数："
    "这是按名次累加的行号/累计数，会让「参赛校数」≈当前名次（如 2/2、7/7），不是全市学校总数。"
    "参赛校数必须用 COUNT(*) OVER ()（空窗口），与 RANK() OVER (ORDER BY 均分 DESC) 分开写。"
)
_COUNT_STAR_OVER_ORDER = re.compile(
    r"COUNT\s*\(\s*\*\s*\)\s*OVER\s*\(\s*ORDER\s+BY\b",
    re.IGNORECASE,
)
_SUBJECT_NAME_LIT = (
    r"语文|数学|英语|物理|化学|生物|历史|政治|地理|"
    r"化学综合|生物综合|政治综合|地理综合"
)
_CITYWIDE_SUBJECT_AVG_BRANCH = re.compile(
    rf"SELECT\s+'(?:{_SUBJECT_NAME_LIT})'\s+AS\s+\w+\s*,\s*"
    rf".{{0,400}}?\bAVG\s*\("
    rf".{{0,500}}?\bFROM\s+tb_score_overview\b"
    rf".{{0,500}}?(?:UNION\s+ALL|\))",
    re.IGNORECASE | re.DOTALL,
)
_PARTITION_BY_SUBJECT = re.compile(
    r"PARTITION\s+BY\s+[^\)]*\b(?:学科|subject|subject_name)\b",
    re.IGNORECASE | re.DOTALL,
)
_WHERE_EXCLUDES_NULL_AVG = re.compile(
    r"\bIS\s+NOT\s+NULL\b|\bNOT\s+\w+(\.\w+)?\s+IS\s+NULL\b|>\s*0",
    re.IGNORECASE,
)


def _strip_exists_clauses(where_sql: str) -> str:
    """去掉 EXISTS(...)，便于判断是否还有直接的校/班谓词。"""
    s = where_sql or ""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(
            r"\bEXISTS\s*\((?:[^()]|\([^()]*\))*\)",
            " ",
            s,
            flags=re.IGNORECASE,
        )
    return s


def _where_has_direct_scope_pred(where_sql: str) -> bool:
    return bool(_SCOPE_PRED.search(_strip_exists_clauses(where_sql)))


def _where_only_exists_scope(where_sql: str) -> bool:
    if not where_sql or not re.search(r"\bEXISTS\s*\(", where_sql, re.I):
        return False
    if _where_has_direct_scope_pred(where_sql):
        return False
    return bool(_SCOPE_COL.search(where_sql))


def _select_projection_sql(sel: object) -> str:
    exprs = getattr(sel, "expressions", None) or []
    return " ".join(e.sql(dialect="postgres") for e in exprs)


def _select_where_sql(sel: object) -> str:
    wh = sel.args.get("where") if hasattr(sel, "args") else None
    if wh is None:
        return ""
    return wh.sql(dialect="postgres")


def _select_has_rank_in_projection(sel: object) -> bool:
    return bool(_RANK_FN.search(_select_projection_sql(sel)))


def _cte_names_with_rank(ast: object) -> set[str]:
    out: set[str] = set()
    if _sg_exp is None:
        return out
    for cte in ast.find_all(_sg_exp.CTE):  # type: ignore[union-attr]
        alias = cte.alias_or_name
        body_sql = cte.this.sql(dialect="postgres") if cte.this is not None else ""
        if alias and _RANK_FN.search(body_sql):
            out.add(str(alias).lower())
    return out


def _from_mentions_ranked_cte(sel: object, ranked_ctes: set[str]) -> bool:
    if not ranked_ctes or _sg_exp is None:
        return False
    for table in sel.find_all(_sg_exp.Table):  # type: ignore[union-attr]
        name = (table.name or "").lower()
        if name in ranked_ctes:
            return True
    return False


def _load_sqlglot():
    """运行时再取 sqlglot，避免模块 import 失败后一直短路。"""
    global sqlglot, _sg_exp
    if sqlglot is not None and _sg_exp is not None:
        return sqlglot, _sg_exp
    try:
        import sqlglot as _sg
        from sqlglot import exp as _exp

        sqlglot = _sg
        _sg_exp = _exp
        return sqlglot, _sg_exp
    except Exception:
        return None, None


def _outermost_select_sql(sql: str) -> str:
    """取最外层 SELECT 文本（WITH 时取最后一个 ) SELECT 起）。"""
    s = sql or ""
    matches = list(re.finditer(r"\)\s*(SELECT)\b", s, re.IGNORECASE))
    if matches:
        return s[matches[-1].start(1) :]
    return s


def _outer_select_same_level_rank_and_school_filter(sql: str) -> bool:
    """最外层同时有 RANK() 与校/班 WHERE → 参赛池已被收成目标校。"""
    if "tb_score_overview" not in (sql or "").lower():
        return False
    outer = _outermost_select_sql(sql)
    if not _RANK_FN.search(outer):
        return False
    outer_no_filter = re.sub(
        r"FILTER\s*\(\s*WHERE[^)]*\)", " ", outer, flags=re.IGNORECASE
    )
    if not re.search(r"\bWHERE\b", outer_no_filter, re.I):
        return False
    return _where_has_direct_scope_pred(outer_no_filter)


def _rank_pool_shrunk_by_target_filter(sql: str) -> bool:
    """同层 RANK+校/班 WHERE，或对排名结果仅用 EXISTS 冒充目标校过滤。"""
    if "tb_score_overview" not in (sql or "").lower():
        return False
    if not _RANK_FN.search(sql or ""):
        return False
    # 不依赖 sqlglot：外层 SELECT 同时含 RANK 与校/班过滤
    if _outer_select_same_level_rank_and_school_filter(sql):
        return True
    # 不依赖 sqlglot：外层 FROM ranked… 且只用 EXISTS 判断目标校是否存在
    if _outer_exists_only_school_filter_on_ranked(sql):
        return True
    sg, sg_exp = _load_sqlglot()
    if sg is None or sg_exp is None:
        return False
    try:
        ast = sg.parse_one(sql, read="postgres")
    except Exception:
        return False
    ranked_ctes = _cte_names_with_rank(ast)
    for sel in ast.find_all(sg_exp.Select):
        where_sql = _select_where_sql(sel)
        if _select_has_rank_in_projection(sel) and _where_has_direct_scope_pred(where_sql):
            return True
        if not _where_only_exists_scope(where_sql):
            continue
        proj = _select_projection_sql(sel)
        if _RANK_RESULT_COL.search(proj) or _from_mentions_ranked_cte(sel, ranked_ctes):
            return True
    return False


def _outer_exists_only_school_filter_on_ranked(sql: str) -> bool:
    """外层从排名 CTE 取数，却只用 EXISTS(目标校) —— 会漏出全市榜。"""
    outer = _outermost_select_sql(sql)
    if not re.search(r"\bEXISTS\s*\(", outer, re.I):
        return False
    if not (
        re.search(r"\bFROM\s+(?:ranked|t2|city_rank|school_rank)\b", outer, re.I)
        or (_RANK_RESULT_COL.search(outer) and re.search(r"\bFROM\s+\w+", outer, re.I))
    ):
        return False
    # 去掉 EXISTS 后不应再有直接的校/班谓词
    # 先去掉 FILTER(WHERE…) 避免误伤
    outer_no_filter = re.sub(
        r"FILTER\s*\(\s*WHERE[^)]*\)", " ", outer, flags=re.IGNORECASE
    )
    m = re.search(r"\bWHERE\b([\s\S]+?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", outer_no_filter, re.I)
    where_sql = m.group(1) if m else ""
    if _where_has_direct_scope_pred(where_sql):
        return False
    if not _where_only_exists_scope(where_sql):
        # EXISTS 内用 school_code / xx 等
        if re.search(r"\bEXISTS\s*\(", where_sql, re.I) and _SCOPE_PRED.search(where_sql):
            return True
        return False
    return True


def looks_like_unfiltered_city_leaderboard(
    sql: str,
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
) -> bool:
    """执行后兜底：本意查一校，却返回全市榜（行数远超学科数、名次从 1 排到很多）。"""
    if "tb_score_overview" not in (sql or "").lower():
        return False
    if not _RANK_FN.search(sql or ""):
        return False
    # 典型：外层 EXISTS 冒充校过滤
    if not re.search(r"\bEXISTS\s*\(", sql or "", re.I):
        return False
    if not _SCOPE_PRED.search(sql or ""):
        return False
    row_list = [list(r) for r in (rows or []) if r is not None]
    if len(row_list) < 20:
        return False
    cols = [str(c or "") for c in (columns or [])]
    rank_idxs = [i for i, c in enumerate(cols) if _is_rank_result_column(c)]
    if not rank_idxs:
        return False
    rank_vals: list[int] = []
    for row in row_list:
        for i in rank_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    rank_vals.append(v)
    if not rank_vals:
        return False
    # 全市榜特征：既有第 1 也有较后名次
    return min(rank_vals) == 1 and max(rank_vals) >= 5


def _cell_as_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _is_rank_result_column(name: str) -> bool:
    c = str(name or "")
    key = re.sub(r"[\s\-]+", "_", c).lower()
    if _RANK_RESULT_COL.search(c) or _RANK_RESULT_COL.search(key):
        return True
    # yw_rank / rank_yw / rk_yw 等英文别名
    return bool(re.search(r"(^|_)(rank|rk)(_|$)", key) or re.search(r"(^|_)(rank|rk)_[a-z0-9]+$", key))


def _is_city_pool_column(name: str) -> bool:
    c = str(name or "")
    key = re.sub(r"[\s\-]+", "_", c).lower()
    if "参赛" in c or "学校数" in c or "班级数" in c:
        return True
    return bool(
        re.search(
            r"n_school|n_class|total_schools|total_classes|school_count|总校数|参赛校",
            key,
        )
    )


def _is_window_count_column(name: str) -> bool:
    """同层过滤后 COUNT(*) OVER 常被误标为「参考人数」且恒为 1。"""
    c = str(name or "")
    key = re.sub(r"[\s\-]+", "_", c).lower()
    if "参考人数" in c or "窗口" in c:
        return True
    return bool(re.search(r"ref_count|window_n|over_cnt", key))


def looks_like_collapsed_city_rank_result(
    sql: str,
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
) -> bool:
    """执行后兜底：同层校过滤导致排名全是第 1。

    覆盖：宽表 1 行多列 rank_yw=1；竖表多行 全市排名=1；
    SQL 仍是同层 RANK+WHERE 却漏过预检时。
    """
    if not _RANK_FN.search(sql or ""):
        return False
    if "tb_score_overview" not in (sql or "").lower():
        return False
    # 无目标校/班意图时，全市榜单也可能出现多列第 1（不同学校），不在此拦
    if not _SCOPE_PRED.search(sql or "") and not re.search(r"\bEXISTS\s*\(", sql or "", re.I):
        return False
    row_list = [list(r) for r in (rows or []) if r is not None]
    if not row_list:
        return False
    cols = [str(c or "") for c in (columns or [])]
    if not cols:
        return False
    rank_idxs: list[int] = []
    pool_idxs: list[int] = []
    win_idxs: list[int] = []
    for i, c in enumerate(cols):
        if _is_rank_result_column(c):
            rank_idxs.append(i)
        if _is_city_pool_column(c):
            pool_idxs.append(i)
        if _is_window_count_column(c):
            win_idxs.append(i)
    if not rank_idxs:
        return False

    all_rank_vals: list[int] = []
    all_pool_vals: list[int] = []
    all_win_vals: list[int] = []
    for row in row_list:
        for i in rank_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    all_rank_vals.append(v)
        for i in pool_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    all_pool_vals.append(v)
        for i in win_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    all_win_vals.append(v)

    if not all_rank_vals or not all(v == 1 for v in all_rank_vals):
        return False

    # SQL 仍是同层塌缩写法：只要结果排名全是 1 就拦（预检漏网兜底）
    if _rank_pool_shrunk_by_target_filter(sql):
        return True

    # 宽表：1 行、≥2 个排名列全是 1
    if len(row_list) == 1 and len(all_rank_vals) >= 2:
        return True

    # 参赛池列声称 >1，但窗口行数/参考人数全是 1 → 典型同层过滤
    if (
        all_pool_vals
        and any(v > 1 for v in all_pool_vals)
        and all_win_vals
        and all(v == 1 for v in all_win_vals)
    ):
        return True

    # 参赛池列=1 且排名全 1
    if all_pool_vals and any(v == 1 for v in all_pool_vals):
        return True

    return False


def looks_like_subject_mutual_rank_as_city_schools(
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
) -> bool:
    """执行后兜底：8~12 行学科表，参赛校数=行数且名次恰为 1..N → 科目互比。"""
    row_list = [list(r) for r in (rows or []) if r is not None]
    n = len(row_list)
    if n < 7 or n > 12:
        return False
    cols = [str(c or "") for c in (columns or [])]
    if not cols:
        return False
    pool_idxs = [i for i, c in enumerate(cols) if _is_city_pool_column(c)]
    rank_idxs = [i for i, c in enumerate(cols) if _is_rank_result_column(c)]
    if not pool_idxs or not rank_idxs:
        return False
    pool_vals: list[int] = []
    rank_vals: list[int] = []
    for row in row_list:
        for i in pool_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    pool_vals.append(v)
        for i in rank_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    rank_vals.append(v)
    if not pool_vals or not rank_vals:
        return False
    # 参赛「校数」恒等于学科行数
    if not all(v == n for v in pool_vals):
        return False
    # 名次恰好覆盖 1..N（科目互排的典型形态）
    if sorted(rank_vals) != list(range(1, n + 1)):
        return False
    return True


def _subject_rank_keeps_null_avgs(sql: str) -> bool:
    """PARTITION BY 学科 的全市排名未剔空均分 → NULL 占前列、分母偏大。"""
    if "tb_score_overview" not in (sql or "").lower():
        return False
    if not _RANK_FN.search(sql or ""):
        return False
    sg, sg_exp = _load_sqlglot()
    if sg is not None and sg_exp is not None:
        try:
            ast = sg.parse_one(sql, read="postgres")
        except Exception:
            ast = None
        if ast is not None:
            for sel in ast.find_all(sg_exp.Select):
                proj = _select_projection_sql(sel)
                if not _RANK_FN.search(proj):
                    continue
                if not _PARTITION_BY_SUBJECT.search(proj):
                    continue
                where_sql = _select_where_sql(sel)
                if _WHERE_EXCLUDES_NULL_AVG.search(where_sql):
                    continue
                return True
    # 解析失败时的窄规则：PARTITION BY 学科 + ORDER BY DESC 且无 NULLS LAST
    for m in re.finditer(
        r"\b(?:RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\(([^)]*)\)",
        sql or "",
        re.IGNORECASE | re.DOTALL,
    ):
        body = m.group(1) or ""
        if not _PARTITION_BY_SUBJECT.search(body):
            continue
        if re.search(r"ORDER\s+BY\s+.+\bDESC\b", body, re.I) and not re.search(
            r"NULLS\s+LAST", body, re.I
        ):
            return True
    return False


def _citywide_subject_avg_union_then_rank(sql: str) -> bool:
    """各科全市均分 UNION（无 GROUP BY xx）后再 RANK → 科目互比冒充校排。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not _RANK_FN.search(s):
        return False
    for m in _CITYWIDE_SUBJECT_AVG_BRANCH.finditer(s):
        chunk = m.group(0) or ""
        if re.search(r"\bGROUP\s+BY\b", chunk, re.I) and re.search(
            r"\bGROUP\s+BY\b[\s\S]{0,80}\bxx\b", chunk, re.I
        ):
            continue
        return True
    return False


_SUBJECT_LIT = re.compile(
    r"'(?:语文|数学|英语|物理|化学|生物|政治|历史|地理)'",
)


def _rank_on_unpivoted_subjects_without_partition(sql: str) -> bool:
    """一校一科 UNION 后整体 RANK 且无 PARTITION BY 学科 → 分母≈学科数×校数。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\bUNION\s+ALL\b", s, re.I):
        return False
    unpivot_ctes: list[str] = []
    for m in re.finditer(
        r"\b(\w+)\s+AS\s*\(\s*(SELECT\b[\s\S]*?)\)\s*(?:,|(?:SELECT)\b)",
        s,
        re.IGNORECASE,
    ):
        name, body = m.group(1), m.group(2) or ""
        if not re.search(r"\bUNION\s+ALL\b", body, re.I):
            continue
        if len(_SUBJECT_LIT.findall(body)) < 2:
            continue
        if not re.search(r"\bxx\b", body, re.I):
            continue
        # 各分支内已 RANK 的正确写法：跳过
        if _RANK_FN.search(body):
            continue
        unpivot_ctes.append(name.lower())
    if not unpivot_ctes:
        return False
    for m in re.finditer(
        r"\b(?:RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\(([^)]*)\)",
        s,
        re.IGNORECASE | re.DOTALL,
    ):
        win = m.group(1) or ""
        if _PARTITION_BY_SUBJECT.search(win):
            continue
        if not re.search(r"ORDER\s+BY", win, re.I):
            continue
        tail = s[m.end() : m.end() + 500]
        for cte in unpivot_ctes:
            if re.search(rf"\bFROM\s+{re.escape(cte)}\b", tail, re.I):
                return True
    return False


def looks_like_running_count_as_school_pool(
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
) -> bool:
    """执行后兜底：多数行「参赛校数≈全市排名」且池很小 → 累计 COUNT 冒充分母。"""
    row_list = [list(r) for r in (rows or []) if r is not None]
    if len(row_list) < 4:
        return False
    cols = [str(c or "") for c in (columns or [])]
    pool_idxs = [i for i, c in enumerate(cols) if _is_city_pool_column(c)]
    rank_idxs = [i for i, c in enumerate(cols) if _is_rank_result_column(c)]
    if not pool_idxs or not rank_idxs:
        return False
    eq = 0
    pairs = 0
    for row in row_list:
        rk = None
        pl = None
        for i in rank_idxs:
            if i < len(row):
                rk = _cell_as_int(row[i])
                if rk is not None:
                    break
        for i in pool_idxs:
            if i < len(row):
                pl = _cell_as_int(row[i])
                if pl is not None:
                    break
        if rk is None or pl is None:
            continue
        pairs += 1
        if pl == rk and pl <= 15:
            eq += 1
    return pairs >= 4 and eq >= max(3, (pairs * 2) // 3)


def looks_like_cross_subject_mixed_pool(
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
) -> bool:
    """执行后兜底：6~12 科行且参赛池≥80（≈学科×校）→ 跨科混池排名。"""
    row_list = [list(r) for r in (rows or []) if r is not None]
    n = len(row_list)
    if n < 6 or n > 12:
        return False
    cols = [str(c or "") for c in (columns or [])]
    if not cols:
        return False
    has_subject = any(re.search(r"学科|subject", c, re.I) for c in cols)
    if not has_subject:
        return False
    pool_idxs = [i for i, c in enumerate(cols) if _is_city_pool_column(c)]
    if not pool_idxs:
        return False
    pool_vals: list[int] = []
    for row in row_list:
        for i in pool_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    pool_vals.append(v)
    if not pool_vals:
        return False
    if len(set(pool_vals)) != 1:
        return False
    p = pool_vals[0]
    return p >= max(80, n * 15)


def _literal_matches_bound(val: str, allowed: str) -> bool:
    """班名全半角括号视为同一字面量；其它字面量仍按原文包含关系。"""
    if val == allowed or val in allowed or allowed in val:
        return True
    from src.agent.education.query_parse import normalize_class_spans

    folded_val = normalize_class_spans(val)
    folded_allowed = normalize_class_spans(allowed)
    if not folded_val or (folded_val == val and folded_allowed == allowed):
        return False
    return (
        folded_val == folded_allowed
        or folded_val in folded_allowed
        or folded_allowed in folded_val
    )


def _unbound_literals(sql: str, bound: Sequence[str] | None) -> list[str]:
    allowed = [str(x).strip() for x in (bound or []) if str(x).strip()]
    if not allowed:
        return []
    bad: list[str] = []
    for lit in _EQ_LIT.findall(sql or ""):
        val = str(lit).strip()
        if not val:
            continue
        if any(_literal_matches_bound(val, a) for a in allowed):
            continue
        if val not in bad:
            bad.append(val)
    return bad


def lint_edu_sql_blocks(
    sql: str,
    bound_literals: Sequence[str] | None = None,
    question: str | None = None,
) -> list[str]:
    """致命项：不得执行。"""
    s = sql or ""
    blocks: list[str] = []
    if _AVG_REACH.search(s):
        blocks.append(
            "检测到 AVG(reach_rate)：区县/全市达线率须 "
            "SUM(reached_count)/SUM(candidates) 重算，禁止对 reach_rate 求平均。"
        )
    if _MONTH_DISTRICT.search(s):
        blocks.append(
            "检测到疑似把「N月」拼进区县的字面量（如 '月广陵区'）："
            "请改为真实区县名或 district LIKE '%广陵%'，并先 peek_edu_filter_values。"
        )
    missing: list[str] = []
    for m in list(_SUBJECT_STAT.finditer(s)) + list(_PERCENTILE_SUBJECT.finditer(s)):
        col = (m.group("col") or "").lower()
        if col and col not in missing and not _subject_stat_excludes_zero(s, col, m.end()):
            missing.append(col)
    if missing:
        cols = "、".join(missing)
        blocks.append(
            f"检测到对 {cols} 的分数统计未排除 0 分：未选考/缺考在宽表为 0，"
            "计入会把选考科均分拉低、标准差/均衡性失真。"
            "凡均分/标准差/方差/中位/最值/分数段，须 AGG(col) FILTER (WHERE col > 0)，"
            "并同时给出 COUNT(*) FILTER (WHERE col > 0) 作为该科参考人数。"
        )
    if _weak_rank_uses_sentinel(s):
        blocks.append(_WEAK_RANK_SENTINEL_MSG)
    zero_rank_cols = _elective_rank_includes_zero(s)
    if zero_rank_cols:
        cols = "、".join(zero_rank_cols)
        blocks.append(
            f"检测到对 {cols} 做 RANK 未排除 0 分：未选考不得进该科排名池。"
            "须 WHERE col>0 后再 RANK；筛弱科须 col>0 AND 名次>N，"
            "禁止把未选考当成名次之外。"
        )
    if _overview_rank_missing_enrolled(s):
        blocks.append(
            "检测到全市班级/学校排名未排除市报生：tb_score_overview 须 "
            "AND xsxz='在籍生'，否则往届/市报虚拟班会挤占名次。"
        )
    if _class_rank_partitioned_by_name(s):
        blocks.append(
            "检测到 RANK() OVER (PARTITION BY bj)：这是各校同名班互比，不是全市班级排名。"
            "必须 GROUP BY xx,bj 后 RANK() OVER (ORDER BY 均分 DESC)，禁止 PARTITION BY bj；"
            "目标校/班只允许在排名完成后再 WHERE 过滤。"
        )
    if _rank_pool_shrunk_by_target_filter(s):
        school_scope = False
        try:
            from src.agent.education.query_parse import is_class_rank_school_scope

            school_scope = is_class_rank_school_scope(question or "")
        except Exception:
            school_scope = False
        if not school_scope:
            blocks.append(_RANK_POOL_SHRINK_MSG)
    if _subject_rank_keeps_null_avgs(s):
        blocks.append(_SUBJECT_RANK_NULL_POOL_MSG)
    if _citywide_subject_avg_union_then_rank(s):
        blocks.append(_SUBJECT_MUTUAL_RANK_MSG)
    if _rank_on_unpivoted_subjects_without_partition(s):
        blocks.append(_CROSS_SUBJECT_MIXED_POOL_MSG)
    if _RANK_FN.search(s) and _COUNT_STAR_OVER_ORDER.search(s):
        blocks.append(_RUNNING_COUNT_AS_POOL_MSG)
    if _count_distinct_class_name_as_city_n(s):
        blocks.append(
            "检测到 COUNT(DISTINCT bj)：班名（高三(1)班…）全市重复，这是班名种类不是全市班级数。"
            "全市班级数用 COUNT(*) OVER ()（对 xx,bj 分组结果），禁止 COUNT(DISTINCT bj)。"
        )
    raw_electives = _raw_elective_cols(s)
    if raw_electives:
        cols = "、".join(raw_electives)
        blocks.append(
            f"检测到使用卷面列 {cols}：化学/生物/政治/地理对外展示必须用转换分 "
            "hxzh/swzh/zzzh/dlzh（含 SELECT/ORDER BY/MAX/MIN/AVG），禁止 hx/sw/zz/dl。"
        )
    if _school_column_aliased_as_class(s):
        blocks.append(
            "检测到把 xx（学校）别名为 class_name/班级：班级横向对比必须用 bj。"
            "SELECT bj AS class_name，GROUP BY xx, bj；禁止 SELECT xx AS class_name，禁止只 GROUP BY xx。"
        )
    if _class_alias_grouped_by_school_only(s):
        blocks.append(
            "检测到 SELECT 班级别名却只 GROUP BY xx：会把全校塌成一行学校均分。"
            "各班对比必须 SELECT bj AS class_name，GROUP BY xx, bj。"
        )
    if _class_rank_missing_track_split(s, question):
        blocks.append(
            "班级全市/全校六门或单科排名默认只出区分文理的一个名次："
            "按物理类（xkkm LIKE '物%'）或历史类（LIKE '史%' OR LIKE '历%'）分轨，"
            "禁止不区分选考的混排，禁止再 UNION「不区分选考」。"
        )
    if _class_rank_outer_target_without_neighborhood(s, question):
        blocks.append(
            "班级全市排名查询结果须含对照班：第1名 + 目标班名次前后各3名（标记本班）。"
            "用目标校+班定位窗口（BETWEEN），禁止外层 WHERE 只留下目标班一行。"
        )
    if _nth_class_lookup_missing_asked_rank(s, question):
        blocks.append(
            "全市第N是哪个班须外层 WHERE 排名=N（并列则多行）；"
            "倒数第K须 RANK DESC（第1=最好）后 WHERE 排名=参赛班数-K+1。"
            "禁止 BETWEEN 对照窗口，禁止 ORDER BY 均分 ASC（会把正数第K当成倒数），"
            "禁止输出全市榜，禁止外层 WHERE 已知校+班。"
        )
    if _class_rank_outer_bj_without_school(s):
        blocks.append(
            "班级全市排名外层必须同时过滤学校（xx/学校）与班级（bj/班级），"
            "禁止只滤班级（同名班会串校）。"
        )
    if _class_rank_pool_unwashed(s, question):
        blocks.append(
            "班级全市排名池须洗掉小班与其他校：xxlb NOT LIKE '%其他%'（中专/职校），"
            "整班参考人数 HAVING COUNT(*) >= 10。"
        )
    if _class_rank_missing_school_type_filter(s, question):
        blocks.append(
            "跨校学校/班级排名与优势薄弱学科对照池必须 xxlb LIKE '%引领/支撑/发展%'："
            "校长/老师默认同层次（问句写全市仍是该类，不是全市所有普高）；"
            "禁止落到其他层次学校/班级。禁止只用排除其他校的全市普通高中混排。"
            "问句写不限校类/全市普通高中才放开。全校/校内班排不套校类。"
        )
    if _subject_class_rank_keeps_empty_avg(s, question):
        blocks.append(
            "班级单科全市排名须把该科有效人数为 0 的班排除出池："
            "HAVING COUNT(*) FILTER (WHERE col > 0) >= 3，或 RANK() OVER (ORDER BY 均分 DESC NULLS LAST)；"
            "禁止缺考/无分班排到第1。"
        )
    unbound = _unbound_literals(s, bound_literals)
    if unbound:
        blocks.append(
            "WHERE 字面量不在本轮已绑定集合："
            + "、".join(unbound)
            + "。必须使用已确认的考试/学校/班级/区县。"
        )
    return blocks


def lint_edu_sql(
    sql: str,
    bound_literals: Sequence[str] | None = None,
    question: str | None = None,
) -> list[str]:
    """兼容旧接口：返回全部护栏文案（含致命项）。"""
    return lint_edu_sql_blocks(sql, bound_literals, question=question)


def _excludes_shibao(sql: str) -> bool:
    s = sql or ""
    if re.search(r"xsxz\s*=\s*'在籍生'", s, re.I):
        return True
    if re.search(r"xsxz[^\n]{0,60}市报", s):
        return True
    return False


def _overview_rank_missing_enrolled(sql: str) -> bool:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    ranked = bool(re.search(r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(", s, re.I))
    by_class = bool(re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I))
    if not ranked and not by_class:
        return False
    return not _excludes_shibao(s)


def _class_rank_partitioned_by_name(sql: str) -> bool:
    """班级聚合后再按班名分区排名 → 同名班跨校互比。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I):
        return False
    return bool(_RANK_PARTITION_BY_BJ.search(s))


def _count_distinct_class_name_as_city_n(sql: str) -> bool:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    return bool(_COUNT_DISTINCT_BJ.search(s))


def _weak_rank_uses_sentinel(sql: str) -> bool:
    return bool(_WEAK_RANK_SENTINEL.search(sql or ""))


def _elective_rank_includes_zero(sql: str) -> list[str]:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return []
    bad: list[str] = []
    for m in _ELECTIVE_RANK_OVER.finditer(s):
        col = (m.group("col") or "").lower()
        if col and col not in bad and not _subject_stat_excludes_zero(s, col, m.end()):
            bad.append(col)
    return bad


def _raw_elective_cols(sql: str) -> list[str]:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return []
    scanned = _SQL_STR_LIT.sub("''", s)
    out: list[str] = []
    for m in _RAW_ELECTIVE_COL.finditer(scanned):
        col = (m.group("col") or "").lower()
        if col and col not in out:
            out.append(col)
    return out


def _school_column_aliased_as_class(sql: str) -> bool:
    """xx 是校名。别成班级再 GROUP BY xx，各班对比会塌成一行学校均分。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    return bool(
        re.search(r"\bxx\s+AS\s+(?:class_name|班级|bj)\b", s, re.I)
    )


def _is_zf6m_class_city_rank_sql(sql: str) -> bool:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(", s, re.I):
        return False
    if not re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I):
        return False
    return bool(re.search(r"\bzf6m\b", s, re.I))


_SUBJECT_RANK_COL = re.compile(
    r"\b(?:yw|sx|yy|wl|ls|hxzh|swzh|zzzh|dlzh)\b",
    re.IGNORECASE,
)


def _is_subject_class_city_rank_sql(sql: str) -> bool:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(", s, re.I):
        return False
    if not re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I):
        return False
    return bool(_SUBJECT_RANK_COL.search(s))


def _class_rank_sql_kind(sql: str, question: str | None) -> str:
    """点名班排或全市第N找班：zf6m / subject / 空。"""
    try:
        from src.agent.education.query_parse import (
            class_city_rank_answer_mode,
            is_citywide_nth_class_lookup_query,
            is_class_subject_city_rank_query,
        )
    except Exception:
        return ""
    q = question or ""
    if is_class_subject_city_rank_query(q) and _is_subject_class_city_rank_sql(sql):
        return "subject"
    if class_city_rank_answer_mode(q) and _is_zf6m_class_city_rank_sql(sql):
        return "zf6m"
    if is_citywide_nth_class_lookup_query(q):
        if _is_subject_class_city_rank_sql(sql):
            return "subject"
        if _is_zf6m_class_city_rank_sql(sql):
            return "zf6m"
    return ""


def _subject_class_rank_keeps_empty_avg(sql: str, question: str | None) -> bool:
    """单科班级排名未排除无分班，DESC 默认 NULLS FIRST 会把缺考班排第1。"""
    if _class_rank_sql_kind(sql, question) != "subject":
        return False
    if not _is_subject_class_city_rank_sql(sql):
        return False
    s = sql or ""
    if re.search(r"NULLS\s+LAST", s, re.I):
        return False
    if re.search(
        r"HAVING[\s\S]{0,280}(?:IS\s+NOT\s+NULL|FILTER[\s\S]{0,80}>\s*0[\s\S]{0,40}>=\s*[1-9])",
        s,
        re.I,
    ):
        return False
    return True


def _class_rank_missing_track_split(sql: str, question: str | None) -> bool:
    """默认/点名文理的班排：必须按物理类或历史类分轨，禁止混排或不区分选考。"""
    try:
        from src.agent.education.query_parse import class_city_rank_answer_mode
    except Exception:
        return False
    mode = class_city_rank_answer_mode(question or "")
    if mode not in {"track", "physics", "history"}:
        return False
    if not (
        _is_zf6m_class_city_rank_sql(sql) or _is_subject_class_city_rank_sql(sql)
    ):
        return False
    s = sql or ""
    if "不区分选考" in s:
        return True
    has_phy = bool(re.search(r"xkkm[^\n]{0,80}物", s, re.I) or ("物理类" in s))
    has_his = bool(
        re.search(r"xkkm[^\n]{0,80}(?:史|历)", s, re.I) or ("历史类" in s)
    )
    if mode == "physics":
        return not has_phy
    if mode == "history":
        return not has_his
    return not (has_phy or has_his)


def _class_rank_sql_for_question(sql: str, question: str | None) -> bool:
    """当前问句是班级全市/单科排名，且 SQL 也是对应排名语句。"""
    try:
        from src.agent.education.query_parse import (
            class_city_rank_answer_mode,
            is_class_subject_city_rank_query,
        )
    except Exception:
        return False
    q = question or ""
    if is_class_subject_city_rank_query(q):
        return _is_subject_class_city_rank_sql(sql)
    if class_city_rank_answer_mode(q):
        return _is_zf6m_class_city_rank_sql(sql)
    return False


def _edu_scope_from_constraints() -> dict | None:
    try:
        from src.agent.education.query_frame import active_constraints
    except Exception:
        return None
    edu = (active_constraints() or {}).get("edu_scope")
    return edu if isinstance(edu, dict) else None


def _is_peer_rank_sql(sql: str, question: str | None) -> bool:
    if _class_rank_sql_for_question(sql, question):
        return True
    if _class_rank_sql_kind(sql, question):
        return True
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\b(?:RANK|DENSE_RANK)\s*\(", s, re.I):
        return False
    try:
        from src.agent.education.query_parse import is_peer_rank_query
    except Exception:
        return False
    return is_peer_rank_query(question or "")


def _class_rank_missing_school_type_filter(sql: str, question: str | None) -> bool:
    """点名校类或校长/老师默认同层次，但排名 SQL 没有正向 xxlb 校类过滤。"""
    try:
        from src.agent.education.query_parse import (
            extract_school_type_target,
            peer_rank_type_required,
            rank_pool_school_type,
            wants_unrestricted_school_type_rank,
        )
    except Exception:
        return False
    q = question or ""
    if wants_unrestricted_school_type_rank(q):
        return False
    edu = _edu_scope_from_constraints()
    st = rank_pool_school_type(q, edu) or extract_school_type_target(q)
    if not st and not peer_rank_type_required(q, edu):
        return False
    if not _is_peer_rank_sql(sql, question):
        return False
    s = sql or ""
    if st:
        token = re.escape(st)
        if re.search(rf"xxlb\s+not\s+like\s*['\"]%[^\n'\"]*{token}", s, re.I):
            return True
        if re.search(rf"xxlb\s+like\s*['\"]%[^\n'\"]*{token}", s, re.I):
            return False
        if re.search(rf"xxlb\s*=\s*['\"][^'\"]*{token}", s, re.I):
            return False
        return True
    if re.search(r"xxlb\s+like\s*['\"]%[^\n'\"]*(?:引领|支撑|发展)", s, re.I):
        return False
    if re.search(r"xxlb\s*=\s*['\"][^'\"]*(?:引领|支撑|发展)", s, re.I):
        return False
    return True


def _class_rank_pool_unwashed(sql: str, question: str | None) -> bool:
    """班级全市排名未排除其他校/整班不足 10 人。全校班排不套用全市洗净。"""
    try:
        from src.agent.education.query_parse import is_class_rank_school_scope
    except Exception:
        return False
    if is_class_rank_school_scope(question or ""):
        return False
    if not _class_rank_sql_kind(sql, question):
        return False
    s = sql or ""
    has_other = bool(re.search(r"xxlb[^\n;]{0,80}其他", s, re.I))
    has_min10 = bool(
        re.search(
            r"HAVING[\s\S]{0,240}COUNT\s*\([^)]*\)(?:\s*FILTER\s*\([^)]*\))?\s*>=\s*10",
            s,
            re.I,
        )
    )
    return not (has_other and has_min10)


def _class_rank_outer_target_without_neighborhood(sql: str, question: str | None) -> bool:
    """目标班过滤后没有名次窗口 → 摘要只剩一行，看不到前后对照。"""
    try:
        from src.agent.education.query_parse import is_citywide_nth_class_lookup_query
    except Exception:
        return False
    q = question or ""
    if is_citywide_nth_class_lookup_query(q):
        return False
    edu = _edu_scope_from_constraints()
    role = str((edu or {}).get("edu_role") or "").strip()
    if role in {"school_admin", "teacher"}:
        return False
    if not _class_rank_sql_kind(sql, q):
        return False
    s = sql or ""
    if re.search(r"\bBETWEEN\b", s, re.I):
        return False
    has_bj = bool(re.search(r"\b(?:bj|班级)\s*(?:=|LIKE)\s*'", s, re.I))
    has_xx = bool(re.search(r"\b(?:xx|学校)\s*(?:=|LIKE)\s*'", s, re.I))
    return has_bj and has_xx


def _class_rank_outer_bj_without_school(sql: str) -> bool:
    """GROUP BY xx,bj 后 RANK，外层只滤班级不滤学校 → 同名班串校。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(", s, re.I):
        return False
    if not re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I):
        return False
    has_bj = bool(re.search(r"\b(?:bj|班级)\s*(?:=|LIKE)\s*", s, re.I))
    has_xx = bool(re.search(r"\b(?:xx|学校)\s*(?:=|LIKE)\s*", s, re.I))
    return has_bj and not has_xx


_RANK_EQ_LEFT = (
    r"(?:[\w.]+\.)?(?:rk|rnk|city_rank|class_rank|rank_no|排名|名次|全市排名|pos|rank)"
)


def _sql_filters_rank_eq(sql: str, n: int) -> bool:
    s = sql or ""
    return bool(re.search(rf"{_RANK_EQ_LEFT}\s*=\s*{n}\b", s, re.I))


def _rank_window_is_asc(sql: str) -> bool:
    """RANK() OVER (ORDER BY … ASC) 会让高分班拿到大号名次。"""
    s = re.sub(r"FILTER\s*\(\s*WHERE[^)]*\)", " ", sql or "", flags=re.I)
    return bool(
        re.search(
            r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(\s*\)\s*OVER\s*\("
            r"[^)]*ORDER\s+BY[\s\S]{0,80}\bASC\b",
            s,
            re.I,
        )
    )


def _sql_filters_from_end_rank(sql: str, k: int) -> bool:
    """DESC 排名下倒数第K：排名 = 总数-K+1。"""
    s = sql or ""
    off = k - 1
    if off == 0:
        # 倒数第一：总数-K+1 化简成 排名=总数，减号从 SQL 文本里消失，纯列名
        # 等值就是唯一正确形态，必须放行；右侧限定非数字字面量（那是正数名次
        # 的过滤，写错名次仍会被拦/被结果行名次复核拦下）。
        return bool(re.search(rf"{_RANK_EQ_LEFT}\s*=\s*(?!\d)\w+", s, re.I))
    if re.search(rf"{_RANK_EQ_LEFT}\s*=\s*\w+\s*-\s*{off}\b", s, re.I):
        return True
    if re.search(rf"{_RANK_EQ_LEFT}\s*=\s*\w+\s*-\s*{k}\s*\+\s*1", s, re.I):
        return True
    return bool(
        re.search(
            rf"{_RANK_EQ_LEFT}\s*=\s*COUNT\s*\(\s*\*\s*\)\s*OVER\s*\(\s*\)\s*-\s*{off}\b",
            s,
            re.I,
        )
    )


def _nth_class_lookup_missing_asked_rank(sql: str, question: str | None) -> bool:
    """全市第N找班却输出对照窗口或全市榜，未按名次过滤。"""
    try:
        from src.agent.education.query_parse import (
            extract_asked_class_rank,
            is_citywide_nth_class_lookup_query,
        )
    except Exception:
        return False
    q = question or ""
    if not is_citywide_nth_class_lookup_query(q):
        return False
    if not _class_rank_sql_kind(sql, q):
        return False
    s = sql or ""
    if re.search(r"\bBETWEEN\b", s, re.I):
        return True
    has_bj = bool(re.search(r"\b(?:bj|班级)\s*(?:=|LIKE)\s*'", s, re.I))
    has_xx = bool(re.search(r"\b(?:xx|学校)\s*(?:=|LIKE)\s*'", s, re.I))
    if has_bj and has_xx:
        return True
    asked = extract_asked_class_rank(q)
    if not asked:
        return False
    kind, n = asked
    if kind == "from_end":
        if _rank_window_is_asc(s):
            return True
        if _sql_filters_rank_eq(s, n):
            return True
        if _sql_filters_from_end_rank(s, n):
            return False
        return True
    stripped = re.sub(r"FILTER\s*\(\s*WHERE[^)]*\)", " ", s, flags=re.I)
    if re.search(r"ORDER\s+BY[\s\S]{0,80}\bASC\b", stripped, re.I):
        return True
    if _sql_filters_rank_eq(s, n):
        return False
    return True


_ELECTIVE_SUBJECTS = frozenset({"化学", "生物", "政治", "地理"})
_ELECTIVE_HOMEROOM_POOL = 300


def looks_like_nth_class_lookup_homeroom_pool(
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
    question: str | None,
) -> bool:
    """再选科把行政班洗净数（约 300+）当成参赛班数。"""
    try:
        from src.agent.education.orchestrator import _extract_subject
        from src.agent.education.query_parse import is_citywide_nth_class_lookup_query
    except Exception:
        return False
    q = question or ""
    if not is_citywide_nth_class_lookup_query(q):
        return False
    subject = (_extract_subject(q) or "").strip()
    if subject not in _ELECTIVE_SUBJECTS:
        return False
    cols = [str(c or "") for c in (columns or [])]
    pool_idxs = [i for i, c in enumerate(cols) if _is_city_pool_column(c)]
    if not pool_idxs:
        return False
    for row in rows or []:
        if row is None:
            continue
        for i in pool_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None and v >= _ELECTIVE_HOMEROOM_POOL:
                    return True
    return False


def looks_like_nth_class_lookup_wrong_rank(
    columns: Sequence[object] | None,
    rows: Sequence[Sequence[object]] | None,
    question: str | None,
) -> bool:
    """结果行的名次不是用户要的第N（对照窗口/串班）。"""
    try:
        from src.agent.education.query_parse import (
            extract_asked_class_rank,
            is_citywide_nth_class_lookup_query,
        )
    except Exception:
        return False
    q = question or ""
    if not is_citywide_nth_class_lookup_query(q):
        return False
    asked = extract_asked_class_rank(q)
    if not asked:
        return False
    row_list = [list(r) for r in (rows or []) if r is not None]
    if not row_list:
        return False
    cols = [str(c or "") for c in (columns or [])]
    rank_idxs = [i for i, c in enumerate(cols) if _is_rank_result_column(c)]
    kind, n = asked
    expected = n
    if kind == "from_end":
        pool_idxs = [i for i, c in enumerate(cols) if _is_city_pool_column(c)]
        pool_vals: list[int] = []
        for row in row_list:
            for i in pool_idxs:
                if i < len(row):
                    v = _cell_as_int(row[i])
                    if v is not None:
                        pool_vals.append(v)
        if not pool_vals:
            return len(row_list) >= 5
        expected = max(pool_vals) - n + 1
        if expected < 1:
            return True
    if not rank_idxs:
        return len(row_list) >= 5
    ranks: list[int] = []
    for row in row_list:
        for i in rank_idxs:
            if i < len(row):
                v = _cell_as_int(row[i])
                if v is not None:
                    ranks.append(v)
    if not ranks:
        return len(row_list) >= 5
    return any(r != expected for r in ranks) or expected not in ranks


def _class_alias_grouped_by_school_only(sql: str) -> bool:
    """SELECT 了 class_name/班级 却只按学校分组。"""
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    if not re.search(r"\bAS\s+(?:class_name|班级)\b", s, re.I):
        return False
    if not re.search(r"GROUP\s+BY\s+", s, re.I):
        return False
    grouped_bj = bool(re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I))
    grouped_xx = bool(re.search(r"GROUP\s+BY\s+[^;]*\bxx\b", s, re.I))
    return grouped_xx and not grouped_bj


def format_lint_warnings(warnings: list[str]) -> str:
    if not warnings:
        return ""
    lines = ["【SQL lint 警告】请改写后重试："]
    lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines) + "\n"


def format_lint_blocks(blocks: list[str]) -> str:
    if not blocks:
        return ""
    lines = ["【SQL lint 拦截】禁止执行，请按下列口径改写后再 execute_sql："]
    lines.extend(f"- {w}" for w in blocks)
    return "\n".join(lines) + "\n"


__all__ = [
    "format_lint_blocks",
    "format_lint_warnings",
    "lint_edu_sql",
    "lint_edu_sql_blocks",
    "looks_like_collapsed_city_rank_result",
    "looks_like_cross_subject_mixed_pool",
    "looks_like_nth_class_lookup_homeroom_pool",
    "looks_like_nth_class_lookup_wrong_rank",
    "looks_like_running_count_as_school_pool",
    "looks_like_subject_mutual_rank_as_city_schools",
    "looks_like_unfiltered_city_leaderboard",
]
