"""导入学科知识点编码 Excel 到 edu 库 tb_knowledge。

用途
----
1. 为 tb_knowledge 补齐/新增编码字段：module_no、chapter_no、secondary、secondary_no、knowledge_no。
2. 清空 tb_knowledge 与 tb_exam_question_knowledge。
3. 读取 Excel 知识点编码表，写入 tb_knowledge。

调用示例
--------
    uv run python scripts/import_knowledge_xlsx.py \
        --file "temp/教科院/知识点/学科知识点编码__新华中学教育统计_扬州市新华中学(高级中学)_20260812104217490053.xlsx" \
        --database-url "postgresql://root:123456@36.213.182.180:5435/edu" \
        --stage 高中 --grade 高一

参数说明
--------
    --file             Excel 文件路径（必须）
    --database-url     目标库连接串；默认从环境变量 DATABASE_URL 读取
    --stage            学段，默认 高中
    --grade            年级，默认 高一
    --dry-run          只预览统计，不写入数据库

Excel 列映射
------------
    学科科目      -> subject
    大概念        -> module
    大概念编码     -> module_no
    重要概念      -> chapter
    重要概念编码   -> chapter_no
    次位概念      -> secondary
    次位概念编码   -> secondary_no
    知识点名称     -> knowledge_name
    知识点编码     -> knowledge_no

依赖
----
    pandas, openpyxl, psycopg2
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import pandas as pd
import psycopg2

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
COLUMNS_TO_ENSURE = {
    "module_no": ("character varying", 16, "大概念编码"),
    "chapter_no": ("character varying", 16, "重要概念编码"),
    "secondary": ("character varying", 128, "次位概念"),
    "secondary_no": ("character varying", 16, "次位概念编码"),
    "knowledge_no": ("character varying", 16, "知识点编码"),
}

EXCEL_COLUMN_MAP = {
    "学科科目": "subject",
    "大概念": "module",
    "大概念编码": "module_no",
    "重要概念": "chapter",
    "重要概念编码": "chapter_no",
    "次位概念": "secondary",
    "次位概念编码": "secondary_no",
    "知识点名称": "knowledge_name",
    "知识点编码": "knowledge_no",
}

# 这些字段在 Excel 中缺失则按空字符串处理
ALLOW_EMPTY = {"secondary", "secondary_no"}


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------
def ensure_columns(conn) -> None:
    """确保 tb_knowledge 包含目标字段；不存在则新增；并校正唯一约束。"""
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tb_knowledge'
    """)
    existing = {row[0] for row in cur.fetchall()}

    for col_name, (data_type, length, comment) in COLUMNS_TO_ENSURE.items():
        if col_name in existing:
            print(f"  [已存在] {col_name}")
            continue
        type_sql = f"{data_type}({length})" if length else data_type
        cur.execute(
            f"ALTER TABLE public.tb_knowledge ADD COLUMN {col_name} {type_sql}"
        )
        cur.execute(
            "COMMENT ON COLUMN public.tb_knowledge.%s IS %%s" % col_name,
            (comment,),
        )
        print(f"  [新增字段] {col_name} {type_sql} COMMENT '{comment}'")

    # 删除 tb_knowledge 除主键外的所有唯一约束/索引
    cur.execute("""
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.tb_knowledge'::regclass
          AND contype = 'u'
    """)
    for row in cur.fetchall():
        cur.execute(f"ALTER TABLE public.tb_knowledge DROP CONSTRAINT {row[0]}")
        print(f"  [已删除] 唯一约束 {row[0]}")

    cur.execute("""
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'tb_knowledge'
          AND indexname != 'tb_knowledge_pkey'
    """)
    for row in cur.fetchall():
        cur.execute(f"DROP INDEX IF EXISTS public.{row[0]}")
        print(f"  [已删除] 索引 {row[0]}")

    # 重建以 knowledge_no 为唯一键的约束
    cur.execute("""
        ALTER TABLE public.tb_knowledge
        ADD CONSTRAINT uk_knowledge_no UNIQUE (knowledge_no)
    """)
    print("  [已创建] 唯一约束 uk_knowledge_no (knowledge_no)")

    conn.commit()


def truncate_tables(conn) -> None:
    """清空 tb_knowledge 与 tb_exam_question_knowledge。"""
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE public.tb_exam_question_knowledge")
    cur.execute("TRUNCATE TABLE public.tb_knowledge RESTART IDENTITY CASCADE")
    conn.commit()
    print("  [已清空] tb_knowledge, tb_exam_question_knowledge")


def insert_knowledge(conn, rows: list[dict[str, Any]]) -> int:
    """批量插入知识点行，返回写入数量。"""
    if not rows:
        return 0

    cur = conn.cursor()
    cols = [
        "stage", "grade", "subject", "module", "module_no",
        "chapter", "chapter_no", "secondary", "secondary_no",
        "knowledge_name", "knowledge_no",
    ]
    params = [tuple(row[c] for c in cols) for row in rows]
    sql = (
        "INSERT INTO public.tb_knowledge ("
        "stage, grade, subject, module, module_no, chapter, chapter_no, "
        "secondary, secondary_no, knowledge_name, knowledge_no"
        ") VALUES %s"
    )
    from psycopg2.extras import execute_values
    execute_values(cur, sql, params, page_size=500)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Excel 处理
# ---------------------------------------------------------------------------
def read_knowledge_excel(path: str, stage: str, grade: str) -> list[dict[str, Any]]:
    """读取 Excel 并转换为 tb_knowledge 行列表。"""
    df = pd.read_excel(path, sheet_name=0)

    # 列名校正：去除首尾空格
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in EXCEL_COLUMN_MAP if c not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必要列: {missing}")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    duplicates = 0

    for _, r in df.iterrows():
        record: dict[str, Any] = {"stage": stage, "grade": grade}
        for cn, key in EXCEL_COLUMN_MAP.items():
            val = r[cn]
            if pd.isna(val):
                sval = ""
            else:
                sval = str(val).strip()
                # 编码类字段转为字符串，去掉末尾 .0
                if key.endswith("_no") and sval.endswith(".0"):
                    sval = sval[:-2]
            record[key] = sval

        # 必填校验
        empty_required = [
            key for key in EXCEL_COLUMN_MAP.values()
            if not record[key] and key not in ALLOW_EMPTY
        ]
        if empty_required:
            raise ValueError(f"行存在必填字段为空: {empty_required}, 数据: {record}")

        # 去重：以 knowledge_no 为唯一键（与数据库唯一约束一致）
        sig = record["knowledge_no"]
        if sig in seen:
            duplicates += 1
            continue
        seen.add(sig)

        rows.append(record)

    if duplicates:
        print(f"  [去重] 跳过重复行: {duplicates}")
    return rows


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="导入知识点编码 Excel 到 edu 库")
    parser.add_argument("--file", required=True, help="知识点编码 Excel 路径")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="目标库连接串；默认从环境变量 DATABASE_URL 读取",
    )
    parser.add_argument("--stage", default="高中", help="学段，默认 高中")
    parser.add_argument("--grade", default="高一", help="年级，默认 高一")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    args = parser.parse_args()

    if not args.database_url:
        print("错误：未提供 --database-url，也未设置环境变量 DATABASE_URL", file=sys.stderr)
        return 1

    print(f"读取 Excel: {args.file}")
    rows = read_knowledge_excel(args.file, args.stage, args.grade)
    print(f"解析行数: {len(rows)}")

    print("\n=== 预览前 5 行 ===")
    for row in rows[:5]:
        print(
            f"  {row['subject']} | {row['module']}({row['module_no']}) | "
            f"{row['chapter']}({row['chapter_no']}) | {row['knowledge_name']}({row['knowledge_no']})"
        )

    if args.dry_run:
        print("\n[DRY RUN] 未写入数据库")
        return 0

    print("\n连接数据库并执行变更...")
    with psycopg2.connect(args.database_url) as conn:
        ensure_columns(conn)
        truncate_tables(conn)
        count = insert_knowledge(conn, rows)

    print(f"\n导入完成: 共写入 {count} 条知识点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
