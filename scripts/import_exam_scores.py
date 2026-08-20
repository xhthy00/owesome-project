"""导入高三模考「总分 + 小题分」到 edu 教育分析库。

用途
----
将教科院提供的某次高三模考的两类 Excel 导入 edu 库：

1. **总分宽表**（`四次模考成绩/XX月XX成绩.xlsx`）→ `tb_score`（每生每科一行总分）
2. **各科小题分文件**（`小题分/XX月小题分/小题分(科目).xls`）→ `tb_exam`、`tb_exam_question`、`tb_score_detail`

同时根据小题分文件表头自动生成该科试卷（`tb_exam`）与题目（`tb_exam_question`）。
可选地从对应试卷 PDF 提取题干与子问文本，写入 ``tb_exam_question.content``。

调用示例
--------
    uv run python scripts/import_exam_scores.py \
        --score-file "temp/教科院/高三4次模拟数据/四次模考成绩/1月高三期末成绩.xlsx" \
        --detail-dir "temp/教科院/高三4次模拟数据/小题分/1月小题分" \
        --paper-dir "temp/教科院/高三4次模拟数据/试卷PDF" \
        --exam-name "2026届高三1月期末" \
        --exam-time 2026-01-23 \
        --database-url "postgresql://root:123456@36.213.182.180:5435/edu"

参数说明
--------
    --score-file       总分宽表 Excel 路径（必须）
    --detail-dir       各科小题分文件目录（必须，含 小题分(科目).xls）
    --paper-dir        各科试卷 PDF 目录（含 数学.pdf / 物理.pdf 等；可选）
    --exam-name        考试名称前缀，如 "2026届高三1月期末"（必须）
    --exam-time        考试日期，格式 YYYY-MM-DD（必须）
    --jc               届次，默认 2026
    --database-url     目标库连接串，默认从环境变量 DATABASE_URL 读取
    --dry-run          只预览统计，不写入数据库
    --exclude-score    个案排除某生某科总分及小题分，格式 SFZH:科目，可重复

字段映射与口径
-------------
- 总分表每行一名学生一次考试，科目列见下；`tb_score.score` = 总分表科目列值。
- 小题分文件：学号=SFZH、考号=KSH、第3行表头定义题目列。
- 表头每个列 = 一道题（含子题），由 ``parse_questions_from_headers`` 解析；
  题型推断：单选→单选题、多选→多选题、其余→解答题。
- ``tb_score_detail``：每生每题一行，score=该题得分，question_score=该题满分。
- ``tb_exam_question.content``：若提供 PDF，则从 PDF 提取题干+子问；否则填 "暂无"。
- 学生匿名编码：anon_stu_id = SHA256(school_id:sfzh)[:8]（学校 HMAC 规则同 import_score_overview.py）。
- 班级：高三(ksh[3:5]去前导零)班。
- 学校以总分表为准；市报（D06-D12）学生按 sfzh 并入小题分文件的真实高中。
- 试卷满分：语数英 150，其余 100。

依赖
----
    pandas, xlrd, openpyxl, psycopg2  （uv 管理）
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# 让脚本可直接被 `python scripts/import_exam_scores.py` 运行（无需安装包）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.agent.education.paper_parser import (  # noqa: E402
    PaperParser,
    build_question_content,
)
from src.agent.education.question_parser import (  # noqa: E402
    parse_questions_from_headers,
)

# ---------------------------------------------------------------------------
# 常量：与离线端/import_score_overview 保持一致
# ---------------------------------------------------------------------------
SCHOOL_CIPHER_SECRET = "yz_edu_k1"
STAGE_PREFIX = "高中"
SCORE_COLUMNS = {
    "语文": "YW",
    "数学": "SX",
    "英语": "YY",
    "物理": "WL",
    "化学": "HX",
    "生物": "SW",
    "历史": "LS",
    "政治": "ZZ",
    "地理": "DL",
}
# 试卷满分：语数英 150，其余 100
FULL_SCORE = {"语文": 150, "数学": 150, "英语": 150}
# 报刊号学校：这些学校的学生按 sfzh 并入小题分文件的真实高中
NEWSPAPER_SCHOOLS = {
    "D06市直市报", "D07宝应市报", "D08高邮市报",
    "D09仪征市报", "D10江都市报", "D11邗江市报", "D12广陵市报",
}

# 小题分文件学校名 → 正式校名 归一化映射。
# 小题分文件（如 11月）的学校名带区域前缀（如 "市直-C01扬州市一中"），
# 与总分表的正式校名（如 "C01扬州市一中"）编码出不同 school_id，导致同校分裂。
# 这里统一归一到正式校名（以总分表/11月/1月 正式名为准）。
PREFIXED_REGIONS = ("市直-", "广陵-", "邗江-", "江都-", "宝应-", "高邮-", "仪征-")
# 特例：同一学校的不同写法（11月总分表用简称、小题分用前缀简称、1月用全称）
SCHOOL_NAME_ALIASES = {
    "D05扬新东方": "D05扬州新东方",
}


def normalize_school_name(name: str) -> str:
    """把小题分文件的学校名归一化为正式校名（去区域前缀、别名统一）。"""
    n = str(name or "").strip()
    for prefix in PREFIXED_REGIONS:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    return SCHOOL_NAME_ALIASES.get(n, n)


# ---------------------------------------------------------------------------
# 匿名编码（与 import_score_overview.py 一致）
# ---------------------------------------------------------------------------
def encode_school_name(name: str) -> str:
    digest = hmac.new(
        SCHOOL_CIPHER_SECRET.encode("utf-8"),
        str(name or "").strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    hex_part = digest[12:20].upper()
    return f"GZ_{hex_part}"


def generate_anon_stu_id(school_id: str, sfzh: str) -> str:
    suffix = hashlib.sha256(f"{school_id}:{sfzh}".encode("utf-8")).hexdigest()[:8].upper()
    return f"{school_id}_{suffix}"


def parse_class_from_ksh(ksh: str) -> str:
    k = str(ksh or "").strip()
    if len(k) < 5:
        return ""
    class_no = k[3:5].lstrip("0") or "0"
    return f"高三({class_no})班"


# ---------------------------------------------------------------------------
# 数值工具
# ---------------------------------------------------------------------------
def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def norm_id(value: Any) -> str:
    """归一化学号/考号：去除末尾 .0。"""
    s = to_str(value)
    if s.endswith(".0"):
        s = s[:-2]
    return s


# ---------------------------------------------------------------------------
# 读取小题分文件：表头 + 数据
# ---------------------------------------------------------------------------


def read_detail_file(path: str) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """读取一个小题分文件。

    返回:
        (题目定义列表, 原始 DataFrame)

    题目定义每项: ``{question_no, score, question_type, col_idx, main_no, is_sub}``
    （由 ``parse_questions_from_headers`` 直接产出，再补一个 ``score`` 别名
    以便与历史代码兼容）。
    """
    df_raw = pd.read_excel(path, sheet_name=0, header=None)
    # 前3行为表头：row0=学科名, row1=区域, row2=题目定义
    hdr = df_raw.iloc[2].tolist()
    data = df_raw.iloc[3:].copy()
    data.columns = range(data.shape[1])

    parsed = parse_questions_from_headers(hdr)
    # 兼容下游：补一个 score 别名（== question_score）
    questions: list[dict[str, Any]] = []
    for q in parsed:
        q["score"] = q["question_score"]
        questions.append(q)
    return questions, data


# ---------------------------------------------------------------------------
# 读取总分宽表
# ---------------------------------------------------------------------------
def read_score_summary(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0)
    df["SFZH"] = df["SFZH"].apply(norm_id)
    df["KSH"] = df["KSH"].apply(norm_id)
    df["XX"] = df["XX"].apply(to_str)
    df["XM"] = df["XM"].apply(to_str)
    return df


# ---------------------------------------------------------------------------
# 主流程：生成 试卷/题目
# ---------------------------------------------------------------------------
def build_exams_and_questions(
    detail_dir: str,
    exam_name_prefix: str,
    exam_time: str,
    paper_dir: str = "",
) -> list[dict[str, Any]]:
    """从小题分目录生成各科试卷与题目定义。

    若提供 ``paper_dir``，则按 ``paper_dir/<科目名>.pdf`` 加载 PDF 解析器，
    并在每题定义里填入 ``content``（题干+子问文本）。无 PDF 时 ``content="暂无"``。
    """
    # 预加载各科 PDF 解析器
    paper_parsers: dict[str, PaperParser] = {}
    if paper_dir:
        if not os.path.isdir(paper_dir):
            print(f"  [警告] paper_dir 不存在: {paper_dir}，content 将填 \"暂无\"")
        else:
            for f in sorted(glob.glob(os.path.join(paper_dir, "*.pdf"))):
                subject_name = Path(f).stem  # e.g. "数学"
                try:
                    parser = PaperParser()
                    parser.load_pdf(f)
                except Exception as e:  # noqa: BLE001
                    print(f"  [警告] PDF 解析失败 {f}: {e}")
                    continue
                paper_parsers[subject_name] = parser
                print(f"  [PDF] 已加载 {subject_name}（{len(parser.questions)} 题）")
    else:
        print("  [PDF] 未提供 paper_dir，content 将填 \"暂无\"")

    exams: list[dict[str, Any]] = []
    files = glob.glob(os.path.join(detail_dir, "小题分(*).xls")) + glob.glob(
        os.path.join(detail_dir, "小题分(*).xlsx")
    )
    if not files:
        raise ValueError(f"目录中未找到小题分文件: {detail_dir}")

    seen_subjects: set[str] = set()
    for f in sorted(files):
        subject = Path(f).name.split("(")[1].split(")")[0]
        if subject not in SCORE_COLUMNS:
            print(f"  [跳过] 未知科目: {subject}")
            continue
        if subject in seen_subjects:
            print(f"  [跳过] 重复科目: {subject}")
            continue
        seen_subjects.add(subject)

        questions, _ = read_detail_file(f)
        if not questions:
            print(f"  [警告] {subject} 无有效题目列，跳过")
            continue

        # 从 PDF 填充 content（无 PDF 时填 "暂无"）
        parser = paper_parsers.get(subject)
        for q in questions:
            q["content"] = build_question_content(parser, q["question_no"], q.get("main_no"))

        full = FULL_SCORE.get(subject, 100)
        exam_name = f"{exam_name_prefix}{subject}试卷"
        exams.append(
            {
                "subject": subject,
                "exam_name": exam_name,
                "exam_time": exam_time,
                "exam_score": full,
                "questions": questions,
            }
        )
        print(f"  [试卷] {exam_name} 满分={full} 题目={len(questions)}")
    return exams


# ---------------------------------------------------------------------------
# 主流程：生成 总分/小题分 行
# ---------------------------------------------------------------------------
def build_score_rows(
    score_file: str,
    detail_dir: str,
    exams: list[dict[str, Any]],
    exclude_scores: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """生成 tb_score 与 tb_score_detail 的行。

    返回 (score_rows, detail_rows, warnings)。
    score_row: {exam_id, student_id, school_id, class, score, subject_name, exam_score, exam_time}
    detail_row: {exam_id, student_id, question_no, question_id, score, question_score, class}
    exclude_scores: {(sfzh, 科目)} —— 排除这些学生的对应科目总分（不写 tb_score，
        同时该科也不生成小题分，用于「总分有分但该科无小题分录入」的个案）。
    """
    sum_df = read_score_summary(score_file)

    # sfzh -> 总分表行信息（学校）
    sum_records: dict[str, dict[str, Any]] = {}
    for r in sum_df.itertuples():
        sfzh = str(r.SFZH)
        if sfzh in sum_records:
            print(f"  [警告] 总分表重复 sfzh: {sfzh}")
            continue
        sum_records[sfzh] = {
            "xx": str(r.XX),
            "ksh": str(r.KSH),
            "xm": str(r.XM),
            "scores": {subj: to_float(getattr(r, col)) for subj, col in SCORE_COLUMNS.items()},
        }

    warnings: list[str] = []
    score_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    # 小题分文件索引：sfzh -> {subject: (school_name, df_row)}
    detail_by_sfzh: dict[str, dict[str, tuple[str, pd.Series]]] = {}

    for exam in exams:
        subject = exam["subject"]
        f = os.path.join(detail_dir, f"小题分({subject}).xls")
        if not os.path.exists(f):
            f = os.path.join(detail_dir, f"小题分({subject}).xlsx")
        questions, data = read_detail_file(f)
        if not questions:
            continue

        for _, row in data.iterrows():
            sfzh = norm_id(row.iloc[0])
            if not sfzh:
                continue
            school_in_detail = normalize_school_name(to_str(row.iloc[3]))
            detail_by_sfzh.setdefault(sfzh, {})[subject] = (school_in_detail, row)

    # 市报学生的真实高中：取其在小题分文件中出现过的学校（跨科一致，已验证）。
    # 若某科没有小题分，则仍用总分表（市报）学校，保证该生不分裂。
    real_school_of: dict[str, str] = {}
    for sfzh, rec in sum_records.items():
        if rec["xx"] in NEWSPAPER_SCHOOLS:
            detail_schools = {info[0] for info in detail_by_sfzh.get(sfzh, {}).values()}
            if detail_schools:
                real_school_of[sfzh] = sorted(detail_schools)[0]
            else:
                real_school_of[sfzh] = rec["xx"]  # 无任何小题分 → 保留市报学校

    def school_for(sfzh: str, rec: dict[str, Any]) -> str:
        """返回该学生的学校名称（市报学生并入真实高中，全部统一为正式校名）。"""
        if rec["xx"] in NEWSPAPER_SCHOOLS:
            return normalize_school_name(real_school_of.get(sfzh, rec["xx"]))
        return normalize_school_name(rec["xx"])

    # ---- 生成 tb_score（总分）----
    for sfzh, rec in sum_records.items():
        school_name = school_for(sfzh, rec)
        school_id = encode_school_name(school_name)
        student_id = generate_anon_stu_id(school_id, sfzh)
        for subject, score in rec["scores"].items():
            if score is None or score <= 0:
                continue
            if exclude_scores and (sfzh, subject) in exclude_scores:
                continue  # 个案排除：该生该科不写总分
            exam = next(e for e in exams if e["subject"] == subject)
            score_rows.append(
                {
                    "exam_id": None,  # 由导入时填充
                    "student_id": student_id,
                    "school_id": school_id,
                    "class": parse_class_from_ksh(rec["ksh"]),
                    "score": float(score),
                    "subject_name": subject,
                    "exam_score": exam["exam_score"],
                    "exam_time": exam["exam_time"],
                    "sfzh": sfzh,
                    "xx": school_name,
                }
            )

    # ---- 生成 tb_score_detail（小题分）----
    for exam in exams:
        subject = exam["subject"]
        f = os.path.join(detail_dir, f"小题分({subject}).xls")
        if not os.path.exists(f):
            f = os.path.join(detail_dir, f"小题分({subject}).xlsx")
        questions_raw, data = read_detail_file(f)
        questions = exam["questions"]  # 已带 col_idx
        if not questions:
            continue

        for _, row in data.iterrows():
            sfzh = norm_id(row.iloc[0])
            if not sfzh:
                continue
            if exclude_scores and (sfzh, subject) in exclude_scores:
                continue  # 个案排除：该生该科不写小题分
            rec = sum_records.get(sfzh)
            # 学生身份：sfzh 是否在总分表？不在则按小题分文件学校（如往届生）
            if rec is not None:
                school_name = school_for(sfzh, rec)
                ksh = rec["ksh"]
            else:
                school_name = normalize_school_name(to_str(row.iloc[3]))
                ksh = to_str(row.iloc[1])

            school_id = encode_school_name(school_name)
            student_id = generate_anon_stu_id(school_id, sfzh)
            class_name = parse_class_from_ksh(ksh)

            for q in questions:
                # 每题只有一个 col_idx：直接取该列值；空列视为 0 分并跳过
                col_idx = q["col_idx"]
                raw = row.iloc[col_idx] if col_idx < len(row) else None
                v = to_float(raw)
                if v is None:
                    continue
                score = round(v, 2)
                detail_rows.append(
                    {
                        "exam_id": None,
                        "student_id": student_id,
                        "question_no": q["question_no"],
                        "question_id": None,
                        "score": score,
                        "question_score": q["score"],
                        "class": class_name,
                        "sfzh": sfzh,
                        "xx": school_name,
                        "subject_name": subject,
                    }
                )

    return score_rows, detail_rows, warnings


# ---------------------------------------------------------------------------
# 数据库写入
# ---------------------------------------------------------------------------
def upsert_exam_batch(conn, batch_name: str) -> int:
    """查找或新建 tb_exam_batch；返回 batch_id。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM tb_exam_batch WHERE batch_name = %s",
        (batch_name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO tb_exam_batch (batch_name) VALUES (%s) RETURNING id",
        (batch_name,),
    )
    return cur.fetchone()[0]


def delete_existing_exam_data(conn, exam_ids: list[int]) -> None:
    """删除旧题目和旧小题分明细（按 exam_id 列表）。

    不在此处 commit，由调用方统一事务控制（upsert_exams 失败时可回滚）。
    """
    if not exam_ids:
        return
    cur = conn.cursor()
    cur.execute("DELETE FROM tb_score_detail WHERE exam_id = ANY(%s)", (exam_ids,))
    cur.execute("DELETE FROM tb_exam_question WHERE exam_id = ANY(%s)", (exam_ids,))


def upsert_exams(
    conn,
    exams: list[dict[str, Any]],
    exam_batch_id: int,
) -> dict[str, int]:
    """写入 tb_exam；返回 {subject: exam_id}。

    匹配规则（按优先级）：
    1. 同 ``exam_batch_id`` + 同 ``subject`` → 复用已有 exam，更新 exam_time/exam_score/exam_name
    2. 否则按 ``exam_name`` 完全匹配 → 复用并绑定到 batch
    3. 都没有 → 新建 exam

    题目（tb_exam_question）按 (exam_id, question_no) UPSERT，
    ``content`` 来自 ``q["content"]``（已由 PDF 解析器填充或默认为 "暂无"）。
    """
    subject_to_id: dict[str, int] = {}
    cur = conn.cursor()
    for exam in exams:
        # 优先按 batch_id + subject 匹配
        cur.execute(
            """SELECT id, exam_name FROM tb_exam
               WHERE exam_batch_id = %s AND subject = %s""",
            (exam_batch_id, exam["subject"]),
        )
        row = cur.fetchone()
        if row:
            exam_id = row[0]
            cur.execute(
                """UPDATE tb_exam
                   SET exam_time=%s, subject=%s, exam_score=%s, exam_name=%s,
                       exam_batch_id=%s
                   WHERE id=%s""",
                (
                    exam["exam_time"],
                    exam["subject"],
                    exam["exam_score"],
                    exam["exam_name"],
                    exam_batch_id,
                    exam_id,
                ),
            )
        else:
            # 退化：按 exam_name 完全匹配
            cur.execute(
                "SELECT id FROM tb_exam WHERE exam_name = %s",
                (exam["exam_name"],),
            )
            row = cur.fetchone()
            if row:
                exam_id = row[0]
                cur.execute(
                    """UPDATE tb_exam
                       SET exam_time=%s, subject=%s, exam_score=%s, exam_batch_id=%s
                       WHERE id=%s""",
                    (
                        exam["exam_time"],
                        exam["subject"],
                        exam["exam_score"],
                        exam_batch_id,
                        exam_id,
                    ),
                )
            else:
                # 新建
                cur.execute(
                    """INSERT INTO tb_exam
                       (exam_name, exam_time, subject, exam_score, exam_type, exam_batch_id)
                       VALUES (%s, %s, %s, %s, 'city', %s) RETURNING id""",
                    (
                        exam["exam_name"],
                        exam["exam_time"],
                        exam["subject"],
                        exam["exam_score"],
                        exam_batch_id,
                    ),
                )
                exam_id = cur.fetchone()[0]
        subject_to_id[exam["subject"]] = exam_id

        # 写入题目（UPSERT by exam_id+question_no）
        for q in exam["questions"]:
            content = q.get("content") or "暂无"
            cur.execute(
                """SELECT id FROM tb_exam_question WHERE exam_id=%s AND question_no=%s""",
                (exam_id, q["question_no"]),
            )
            qrow = cur.fetchone()
            if qrow:
                cur.execute(
                    """UPDATE tb_exam_question
                       SET question_score=%s, question_type=%s, content=%s
                       WHERE id=%s""",
                    (q["score"], q["question_type"], content, qrow[0]),
                )
                qid = qrow[0]
            else:
                cur.execute(
                    """INSERT INTO tb_exam_question
                       (exam_name, question_no, content, question_score, exam_id, question_type)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (exam["exam_name"], q["question_no"], content, q["score"], exam_id, q["question_type"]),
                )
                qid = cur.fetchone()[0]
            q["question_id"] = qid
    conn.commit()
    return subject_to_id


def upsert_scores_and_details(
    conn,
    subject_to_id: dict[str, int],
    score_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
) -> dict[str, int]:
    """UPSERT 写入 tb_score 与 tb_score_detail。"""
    cur = conn.cursor()

    # 填 exam_id：score_rows 按 subject_name、detail_rows 按 subject_name 映射
    for row in score_rows:
        row["exam_id"] = subject_to_id[row["subject_name"]]
    _fill_detail_exam_ids(detail_rows, subject_to_id)

    # ---- tb_score UPSERT ----
    score_cols = ["exam_id", "student_id", "school_id", "class", "score", "subject_name", "exam_score", "exam_time"]
    score_params = [
        tuple(
            row[c] if c != "exam_time" else (row["exam_time"] or None)
            for c in score_cols
        )
        for row in score_rows
    ]
    score_sql = (
        "INSERT INTO public.tb_score (exam_id, student_id, school_id, class, score, subject_name, exam_score, exam_time) "
        "VALUES %s "
        "ON CONFLICT (exam_id, student_id) DO UPDATE SET "
        "school_id=EXCLUDED.school_id, class=EXCLUDED.class, score=EXCLUDED.score, "
        "subject_name=EXCLUDED.subject_name, exam_score=EXCLUDED.exam_score, exam_time=EXCLUDED.exam_time"
    )
    for i in range(0, len(score_params), 500):
        chunk = score_params[i : i + 500]
        execute_values(cur, score_sql, chunk, page_size=500)

    # ---- tb_score_detail UPSERT ----
    detail_cols = ["exam_id", "student_id", "question_no", "question_id", "score", "question_score", "class"]
    detail_params = [
        tuple(row[c] for c in detail_cols)
        for row in detail_rows
    ]
    detail_sql = (
        "INSERT INTO public.tb_score_detail (exam_id, student_id, question_no, question_id, score, question_score, class) "
        "VALUES %s "
        "ON CONFLICT (exam_id, student_id, question_no) DO UPDATE SET "
        "question_id=EXCLUDED.question_id, score=EXCLUDED.score, question_score=EXCLUDED.question_score, class=EXCLUDED.class"
    )
    for i in range(0, len(detail_params), 500):
        chunk = detail_params[i : i + 500]
        execute_values(cur, detail_sql, chunk, page_size=500)

    conn.commit()
    return {"score_rows": len(score_rows), "detail_rows": len(detail_rows)}


def _fill_detail_exam_ids(detail_rows: list[dict[str, Any]], subject_to_id: dict[str, int]) -> None:
    """由调用方在 build_score_rows 时通过 subject_name 填充 exam_id。"""
    for row in detail_rows:
        if "subject_name" in row and row["subject_name"]:
            row["exam_id"] = subject_to_id.get(row["subject_name"])


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="导入高三模考总分+小题分到 edu 库",
    )
    parser.add_argument("--score-file", required=True, help="总分宽表 Excel 路径")
    parser.add_argument("--detail-dir", required=True, help="各科小题分文件目录")
    parser.add_argument(
        "--paper-dir",
        default="",
        help="各科试卷 PDF 目录（含 数学.pdf / 物理.pdf 等；可选）",
    )
    parser.add_argument("--exam-name", required=True, help="考试名称前缀，如 2026届高三1月期末")
    parser.add_argument("--exam-time", required=True, help="考试日期 YYYY-MM-DD")
    parser.add_argument("--jc", type=int, default=2026, help="届次，默认 2026")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="目标库连接串；默认从环境变量 DATABASE_URL 读取",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    parser.add_argument(
        "--exclude-score",
        action="append",
        default=[],
        metavar="SFZH:科目",
        help="个案排除某生某科总分及小题分（如 261081010509:物理），可重复传入",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("错误：未提供 --database-url，也未设置环境变量 DATABASE_URL", file=sys.stderr)
        return 1

    # 解析排除集 {(sfzh, 科目)}
    exclude_scores: set[tuple[str, str]] = set()
    for item in args.exclude_score:
        if ":" not in item:
            print(f"错误：--exclude-score 格式应为 SFZH:科目，收到: {item}", file=sys.stderr)
            return 1
        sfzh, subject = item.split(":", 1)
        exclude_scores.add((sfzh.strip(), subject.strip()))
    if exclude_scores:
        print(f"个案排除 {len(exclude_scores)} 项: {sorted(exclude_scores)}")

    # 1. 生成试卷与题目定义
    print(f"读取小题分目录: {args.detail_dir}")
    exams = build_exams_and_questions(
        args.detail_dir, args.exam_name, args.exam_time, args.paper_dir
    )
    if not exams:
        print("错误：未生成任何试卷", file=sys.stderr)
        return 1

    # 2. 生成总分与小题分行
    print(f"读取总分宽表: {args.score_file}")
    score_rows, detail_rows, _warnings = build_score_rows(
        args.score_file, args.detail_dir, exams, exclude_scores=exclude_scores
    )
    print(f"\n总分行: {len(score_rows)}  小题分行为: {len(detail_rows)}")

    # 3. 预览
    print("\n=== 预览：各科试卷 ===")
    for e in exams:
        total = round(sum(q["score"] for q in e["questions"]), 2)
        print(f"  {e['exam_name']}: {len(e['questions'])}题, 分值={total}")

    print("\n=== 预览：每科题目抽样（前5题+content前60字符） ===")
    for e in exams:
        print(f"\n  -- {e['subject']} ({len(e['questions'])}题) --")
        for q in e["questions"][:5]:
            content_preview = (q.get("content") or "暂无")[:60].replace("\n", " ")
            print(f"    {q['question_no']} ({q['question_score']}分): {content_preview}...")

    print("\n=== 预览：总分行抽样 ===")
    for r in score_rows[:5]:
        print(f"  {r['student_id']} {r['subject_name']}={r['score']} 班级={r['class']} 学校={r['xx']}")

    if args.dry_run:
        print("\n[DRY RUN] 未写入数据库")
        return 0

    # 4. 写入（统一事务：失败回滚，保证 DELETE 与 INSERT 同生共死）
    print("\n写入数据库...")
    try:
        with psycopg2.connect(args.database_url) as conn:
            # 4a. 找/建批次（tb_exam_batch）
            exam_batch_id = upsert_exam_batch(conn, args.exam_name)
            print(f"  [批次] {args.exam_name} id={exam_batch_id}")

            # 4b. 找该批次下已有 exam_ids（按 exam_batch_id 匹配）
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM tb_exam WHERE exam_batch_id = %s",
                (exam_batch_id,),
            )
            existing_exam_ids = [r[0] for r in cur.fetchall()]
            # 4c. 先删旧题目和旧小题分明细（按现有 exam_id），重做干净
            if existing_exam_ids:
                delete_existing_exam_data(conn, existing_exam_ids)
            # 4d. 再 UPSERT 试卷/题目与总分/小题分
            subject_to_id = upsert_exams(conn, exams, exam_batch_id)
            stats = upsert_scores_and_details(conn, subject_to_id, score_rows, detail_rows)
            conn.commit()
    except Exception:
        # with conn 退出时已自动 rollback，这里再提示一下
        print("\n[错误] 写入失败，已自动回滚；tb_exam_question/tb_score_detail 数据未被破坏", file=sys.stderr)
        raise
    print(f"\n导入完成: 试卷={len(exams)}, 总分={stats['score_rows']}, 小题分={stats['detail_rows']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
