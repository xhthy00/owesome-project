"""导入高三模考成绩宽表到 edu.public.tb_score_overview。

用途
----
将「教科院」提供的原始高三模考 Excel（宽表格式，每行一名学生一次考试）
导入到分析库 edu.public.tb_score_overview，供后续报告/分析使用。

调用示例
--------
    uv run python scripts/import_score_overview.py \
        --file "temp/教科院/高三4次模拟数据/四次模考成绩/1月高三期末成绩.xlsx" \
        --exam-name "2026届高三1月期末" \
        --jc 2026 \
        --stage 高中

参数说明
--------
    --file          原始 Excel 文件路径（必须）
    --exam-name     考试名称，写入 tb_score_overview.exam_name（必须）
    --jc            届次，默认 2026
    --stage         学段，默认 "高中"；用于生成学校匿名编码
    --database-url  目标库连接串，默认从环境变量 DATABASE_URL 读取
    --dry-run       只打印预览统计，不写入数据库

Excel 列与目标表字段映射
------------------------
    KSH     -> ksh          考生号（字符串，主键一部分）
    SFZH    -> sfzh         身份证号（明文）
    XM      -> xm           姓名（明文）
    XX      -> xx           学校名称，如 A01扬州中学
    XH      -> xh           校内序号
    XSXZ    -> xsxz         学生性质
    XXLB    -> xxlb         学校类别
    DQ      -> dq           地区
    QH      -> qh           区号（转字符串）
    XKKM    -> xkkm         选考科目
    XKQK    -> xkqk         选考情况（转字符串）
    ZF3M    -> zf3m         3 门总分
    ZF4M    -> zf4m         4 门总分
    ZF6M    -> zf6m         6 门总分
    YW      -> yw           语文
    YWZW    -> ywzw         语文作文
    SX      -> sx           数学
    SXKG    -> sxkg         数学客观
    YY      -> yy           英语
    YYZW    -> yyzw         英语作文
    RY      -> ry           日语
    RYKG    -> rykg         日语客观
    RYZW    -> ryzw         日语作文
    WL      -> wl           物理
    LS      -> ls           历史
    HX      -> hx           化学
    SW      -> sw           生物
    ZZ      -> zz           政治
    DL      -> dl           地理
    HXZH    -> hxzh         化学综合/转换
    HXDJ    -> hxdj         化学等级
    SWZH    -> swzh         生物综合/转换
    SWDJ    -> swdj         生物等级
    ZZZH    -> zzzh         政治综合/转换
    ZZDJ    -> zzdj         政治等级
    DLZH    -> dlzh         地理综合/转换
    DLDJ    -> dldj         地理等级
    ZKCJ    -> zkcj         综合成绩/总分

固定/派生字段
-------------
    exam_name   -> 命令行传入
    jc          -> 命令行传入
    bj          -> 高三(ksh[3:5]去前导零)班
    create_time -> NOW()
    update_time -> NOW()
    anon_stu_id -> {school_id}_{sha256(school_id:sfzh)[:8].upper()}

匿名编码规则
------------
1. 学校匿名编码 school_id：
   school_id = 学段前缀 + HMAC-SHA256(secret, 学校完整名称).hex[12:20].upper()
   其中 secret = "yz_edu_k1"，学段前缀：小学 XX_ / 初中 CZ_ / 高中 GZ_
   学校完整名称使用 Excel 中的 XX 字段原值，例如 "A01扬州中学"。

2. 学生匿名编码 anon_stu_id：
   anon_suffix = SHA256(school_id + ":" + sfzh).hexdigest()[:8].upper()
   anon_stu_id = school_id + "_" + anon_suffix

   注意：离线端学生匿名编码的输入是学生身份证号（sfzh），不是考生号（ksh）。

重复策略
--------
使用 UPSERT：ON CONFLICT (ksh, exam_name) DO UPDATE SET ...
冲突时覆盖更新所有字段。

依赖
----
    pandas, openpyxl, psycopg2
    本项目使用 uv 管理依赖，通常已经具备。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values


# ---------------------------------------------------------------------------
# 常量：与离线端学校匿名编码规则保持一致
# ---------------------------------------------------------------------------

# 学校匿名编码的混淆密钥，必须与离线端 schoolCipher.ts 中的 DEFAULT_SECRET 一致
SCHOOL_CIPHER_SECRET = "yz_edu_k1"

# HMAC 摘要中取 hex 的起始位置与长度，与离线端 schoolCipher.ts 一致
SCHOOL_HEX_START = 12
SCHOOL_HEX_LEN = 8

# 学段前缀映射
STAGE_PREFIX = {
    "小学": "XX_",
    "初中": "CZ_",
    "高中": "GZ_",
}

# Excel 列 -> tb_score_overview 字段的映射
COLUMN_MAPPING: dict[str, str] = {
    "KSH": "ksh",
    "SFZH": "sfzh",
    "XM": "xm",
    "XX": "xx",
    "XH": "xh",
    "XSXZ": "xsxz",
    "XXLB": "xxlb",
    "DQ": "dq",
    "QH": "qh",
    "XKKM": "xkkm",
    "XKQK": "xkqk",
    "ZF3M": "zf3m",
    "ZF4M": "zf4m",
    "ZF6M": "zf6m",
    "YW": "yw",
    "YWZW": "ywzw",
    "SX": "sx",
    "SXKG": "sxkg",
    "YY": "yy",
    "YYZW": "yyzw",
    "RY": "ry",
    "RYKG": "rykg",
    "RYZW": "ryzw",
    "WL": "wl",
    "LS": "ls",
    "HX": "hx",
    "SW": "sw",
    "ZZ": "zz",
    "DL": "dl",
    "HXZH": "hxzh",
    "HXDJ": "hxdj",
    "SWZH": "swzh",
    "SWDJ": "swdj",
    "ZZZH": "zzzh",
    "ZZDJ": "zzdj",
    "DLZH": "dlzh",
    "DLDJ": "dldj",
    "ZKCJ": "zkcj",
}

# 数值型字段，导入时统一转为 float/None
NUMERIC_COLUMNS: set[str] = {
    "xh",
    "zf3m",
    "zf4m",
    "zf6m",
    "yw",
    "ywzw",
    "sx",
    "sxkg",
    "yy",
    "yyzw",
    "ry",
    "rykg",
    "ryzw",
    "wl",
    "ls",
    "hx",
    "sw",
    "zz",
    "dl",
    "hxzh",
    "swzh",
    "zzzh",
    "dlzh",
    "zkcj",
}

# 字符串型字段，导入时统一转为 str/None
STRING_COLUMNS: set[str] = {
    "ksh",
    "sfzh",
    "xm",
    "xx",
    "xsxz",
    "xxlb",
    "dq",
    "qh",
    "xkkm",
    "xkqk",
    "hxdj",
    "swdj",
    "zzdj",
    "dldj",
}


# ---------------------------------------------------------------------------
# 匿名编码函数
# ---------------------------------------------------------------------------


def encode_school_name(name: str, stage: str, secret: str = SCHOOL_CIPHER_SECRET) -> str:
    """生成学校匿名编码，与离线端 encodeSchoolName 规则保持一致。

    Args:
        name: 学校完整名称，例如 "A01扬州中学"。
        stage: 学段，例如 "高中"。
        secret: 混淆密钥。

    Returns:
        学校匿名编码，例如 "GZ_F57E7326"。
    """
    prefix = STAGE_PREFIX.get(stage, "")
    digest = hmac.new(
        secret.encode("utf-8"),
        name.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    hex_part = digest[SCHOOL_HEX_START : SCHOOL_HEX_START + SCHOOL_HEX_LEN].upper()
    return f"{prefix}{hex_part}"


def generate_anon_stu_id(school_id: str, sfzh: str) -> str:
    """生成学生匿名编码，与离线端匿名规则保持一致。

    注意：离线端实际使用 sfzh（身份证号）作为哈希输入，而不是 ksh（考生号）。

    Args:
        school_id: 学校匿名编码，例如 "GZ_F57E7326"。
        sfzh: 学生身份证号。

    Returns:
        学生匿名编码，例如 "GZ_F57E7326_70486573"。
    """
    suffix = hashlib.sha256(f"{school_id}:{sfzh}".encode("utf-8")).hexdigest()[:8].upper()
    return f"{school_id}_{suffix}"


# ---------------------------------------------------------------------------
# 数据清洗与转换
# ---------------------------------------------------------------------------


def parse_class_from_ksh(ksh: str) -> str:
    """从考生号 ksh 中解析班级。

    规则：取 ksh 字符串的第 4、5 位（从 1 开始计数，即索引 3、4），
    组合后去掉前导零，拼接为 "高三(XX)班"。

    Args:
        ksh: 考生号字符串，例如 "501121350646"。

    Returns:
        班级字符串，例如 "高三(12)班"。
    """
    ksh_str = str(ksh or "").strip()
    if len(ksh_str) < 5:
        return ""
    class_no = ksh_str[3:5].lstrip("0") or "0"
    return f"高三({class_no})班"


def to_numeric_or_none(value: Any) -> float | None:
    """将单元格值转为 float；空值或无法解析时返回 None。"""
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def to_string_or_none(value: Any) -> str | None:
    """将单元格值转为字符串；空值返回 None。"""
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    return s if s else None


def prepare_dataframe(file_path: str, exam_name: str, jc: int, stage: str) -> pd.DataFrame:
    """读取 Excel 并转换为符合目标表结构的 DataFrame。

    Args:
        file_path: Excel 文件路径。
        exam_name: 考试名称。
        jc: 届次。
        stage: 学段，用于生成学校匿名编码。

    Returns:
        清洗后的 DataFrame。
    """
    df = pd.read_excel(file_path, sheet_name=0)

    # 统一列名为大写，便于匹配
    df.columns = [str(c).strip().upper() for c in df.columns]

    # 检查必要列
    required_excel_cols = {"KSH", "SFZH", "XM", "XX"}
    missing = required_excel_cols - set(df.columns)
    if missing:
        raise ValueError(f"Excel 缺少必要列: {missing}")

    # 生成派生字段
    df["exam_name"] = exam_name
    df["jc"] = jc
    df["bj"] = df["KSH"].apply(parse_class_from_ksh)

    # 生成学校匿名编码与学生匿名编码
    # 注意：必须先有 school_id 才能生成 anon_stu_id
    df["school_id_for_anon"] = df["XX"].apply(lambda x: encode_school_name(str(x or ""), stage))
    df["anon_stu_id"] = df.apply(
        lambda row: generate_anon_stu_id(
            str(row["school_id_for_anon"]),
            str(row["SFZH"] or ""),
        ),
        axis=1,
    )

    # 构建目标表字段映射
    target_df = pd.DataFrame()
    target_df["exam_name"] = df["exam_name"]
    target_df["jc"] = df["jc"]
    target_df["ksh"] = df["KSH"].astype(str)
    target_df["sfzh"] = df["SFZH"].astype(str)
    target_df["xm"] = df["XM"].astype(str)
    target_df["bj"] = df["bj"]
    target_df["xx"] = df["XX"].astype(str)
    target_df["xh"] = df["XH"].apply(to_numeric_or_none)
    target_df["xsxz"] = df["XSXZ"].apply(to_string_or_none)
    target_df["xxlb"] = df["XXLB"].apply(to_string_or_none)
    target_df["dq"] = df["DQ"].apply(to_string_or_none)
    target_df["qh"] = df["QH"].apply(lambda x: to_string_or_none(x))
    target_df["xkkm"] = df["XKKM"].apply(to_string_or_none)
    target_df["xkqk"] = df["XKQK"].apply(lambda x: to_string_or_none(x))

    for excel_col, target_col in COLUMN_MAPPING.items():
        if target_col in target_df.columns:
            continue
        if target_col in NUMERIC_COLUMNS:
            target_df[target_col] = df[excel_col].apply(to_numeric_or_none)
        elif target_col in STRING_COLUMNS:
            target_df[target_col] = df[excel_col].apply(to_string_or_none)
        else:
            # 默认按原样保留
            target_df[target_col] = df[excel_col]

    target_df["anon_stu_id"] = df["anon_stu_id"]

    return target_df


# ---------------------------------------------------------------------------
# 数据库写入
# ---------------------------------------------------------------------------


def build_upsert_sql(columns: list[str]) -> str:
    """构造 PostgreSQL UPSERT 语句。

    使用 psycopg2.extras.execute_values 要求的 %s 占位符模板：
        INSERT INTO ... VALUES %s ON CONFLICT ... DO UPDATE SET ...
    execute_values 会自动将 %s 展开为多组值。
    """
    col_list = ", ".join(f'"{c}"' for c in columns)
    updates = ", ".join(
        f'"{c}" = EXCLUDED."{c}"' for c in columns if c not in ("ksh", "exam_name")
    )
    return (
        f'INSERT INTO public.tb_score_overview ({col_list}) VALUES %s '
        f"ON CONFLICT (ksh, exam_name) DO UPDATE SET {updates}"
    )


def import_to_database(
    df: pd.DataFrame,
    database_url: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """将 DataFrame 写入 tb_score_overview。

    Args:
        df: 清洗后的 DataFrame。
        database_url: PostgreSQL 连接串。
        dry_run: 为 True 时只统计不写入。

    Returns:
        导入统计字典。
    """
    columns = list(df.columns)
    rows = [
        tuple(
            None if (isinstance(v, float) and pd.isna(v)) else v for v in row
        )
        for row in df.itertuples(index=False, name=None)
    ]

    result = {
        "total_rows": len(df),
        "inserted_or_updated": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        print(f"[DRY RUN] 预计导入 {len(df)} 行")
        return result

    sql = build_upsert_sql(columns)
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)
            # execute_values 在 UPSERT 且分页时，cur.rowcount 只反映最后一批，
            # 因此以实际传入行数作为统计口径。
            result["inserted_or_updated"] = len(rows)
        conn.commit()

    return result


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导入高三模考宽表到 edu.public.tb_score_overview",
    )
    parser.add_argument("--file", required=True, help="原始 Excel 文件路径")
    parser.add_argument("--exam-name", required=True, help="考试名称，例如 2026届高三1月期末")
    parser.add_argument("--jc", type=int, default=2026, help="届次，默认 2026")
    parser.add_argument("--stage", default="高中", help="学段，默认 高中")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="目标库连接串；默认从环境变量 DATABASE_URL 读取",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    args = parser.parse_args()

    if not args.database_url:
        print("错误：未提供 --database-url，也未设置环境变量 DATABASE_URL", file=sys.stderr)
        return 1

    if not Path(args.file).is_file():
        print(f"错误：文件不存在 {args.file}", file=sys.stderr)
        return 1

    print(f"读取文件: {args.file}")
    df = prepare_dataframe(args.file, args.exam_name, args.jc, args.stage)

    print(f"共 {len(df)} 行待导入")
    print(f"考试名称: {args.exam_name}")
    print(f"届次: {args.jc}")
    print(f"学段: {args.stage}")
    print("\n前 3 行预览:")
    print(df.head(3).to_string(index=False))

    result = import_to_database(df, args.database_url, dry_run=args.dry_run)

    if args.dry_run:
        print("\n[DRY RUN] 未写入数据库")
    else:
        print(f"\n导入完成: {result['inserted_or_updated']} 行已插入或更新")

    return 0


if __name__ == "__main__":
    sys.exit(main())
