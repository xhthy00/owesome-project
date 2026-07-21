"""读取 temp/学校信息.csv，为每所学校生成不可逆短 token 作为 id 列。

用法：
    uv run python scripts/encrypt_school_names.py

在原 csv 基础上追加一列 ``id``（= 校名混淆 token），其余列保持不变。
输出仍写回 temp/学校信息.csv（UTF-8 无 BOM，CRLF 行尾，与原文件一致）。
token 同时作为 tb_school.id 与 tb_school.name（数据库为测试数据可随时清空，
外键 tb_score.school_id 同步存同一 token 即可一致）。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.education.school_cipher import encode_school_name  # noqa: E402

CSV_PATH = ROOT / "temp" / "学校信息.csv"


def main() -> None:
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "id" not in fieldnames:
        fieldnames = fieldnames + ["id"]

    for row in rows:
        name = (row.get("学校名称") or "").strip()
        stage = (row.get("学段") or "").strip()
        row["id"] = encode_school_name(name, stage=stage) if name else ""

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"已写回 {CSV_PATH}，共 {len(rows)} 所学校，新增 id 列")
    print("前 10 行预览：")
    for row in rows[:10]:
        print(f"  {row['学校名称']:<28s} -> {row['id']}")


if __name__ == "__main__":
    main()
