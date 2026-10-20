"""试卷文件读取与写入。

接受 PDF、Word、图片。有文字层时直接切题；没有文字层时尝试图片识别。
确认后的题目以预览里改过的题号为准，写入 tb_exam / tb_exam_question。
同一批次、同一科目只保留一份试卷。再次导入会覆盖题目，并清除该科成绩和小题分。
成绩宽表不在这里清理。
"""

from __future__ import annotations

import base64
import io
import logging
import re
import unicodedata
from typing import Any

from src.agent.education.paper_outline import SUBJECTS, _tidy_math, build_paper_outline

logger = logging.getLogger(__name__)

__all__ = [
    "ensure_exam_batch_subject_index",
    "extract_paper_text",
    "pdf_has_text_layer",
    "preview_paper_file",
    "save_paper_outline",
]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_MIN_TEXT = 40


def preview_paper_file(
    data: bytes,
    filename: str,
    subject: str,
    on_progress: Any = None,
    reader: str = "auto",
) -> dict[str, Any]:
    if on_progress:
        on_progress(0, 1, "正在读取试卷")
    text, source = extract_paper_text(data, filename, on_progress=on_progress, reader=reader)
    if on_progress and source != "ocr":
        on_progress(1, 1, "正在整理题目")
    outline = build_paper_outline(subject, text)
    detected = _detect_subject(text, filename)
    if detected and detected != subject:
        outline.warnings.insert(0, f"文件更像「{detected}」，当前选择的是「{subject}」")
    payload = outline.as_dict()
    payload["text_source"] = source
    return payload


def _reader_mode(reader: str | None) -> str:
    mode = (reader or "auto").strip().lower()
    if mode not in {"auto", "code", "model"}:
        raise ValueError("识别方式只能是文字提取或图像识别")
    return mode


def extract_paper_text(
    data: bytes,
    filename: str,
    on_progress: Any = None,
    reader: str = "auto",
) -> tuple[str, str]:
    """返回 (正文, text|ocr)。reader 为 code、model 或 auto。"""
    if not data:
        raise ValueError("上传文件为空")
    mode = _reader_mode(reader)
    ext = _ext(filename)
    if ext == ".pdf":
        if mode != "model":
            text = _pdf_text_layer(data)
            if len(text.strip()) >= _MIN_TEXT:
                return text, "text"
            if mode == "code":
                raise ValueError("这份 PDF 没有可复制的文字，请改用图像识别")
        ocr = _ocr_pdf(data, on_progress)
        if len(ocr.strip()) < _MIN_TEXT:
            raise ValueError("这份 PDF 没有可复制的文字，图片识别也没有读出内容")
        return ocr, "ocr"
    if ext == ".docx":
        text = _docx_text(data)
        if len(text.strip()) < _MIN_TEXT:
            raise ValueError("Word 里没有读到足够的正文")
        return text, "text"
    if ext in _IMAGE_EXTS:
        if mode == "code":
            raise ValueError("图片没有文字层，请改用图像识别")
        text = _ocr_image(data, on_progress)
        if len(text.strip()) < _MIN_TEXT:
            raise ValueError("没有从图片中识别出足够的文字")
        return text, "ocr"
    raise ValueError("请上传 PDF、Word（.docx）或图片")


def save_paper_outline(
    db_type: str,
    config: dict[str, Any],
    *,
    exam_batch_id: int,
    subject: str,
    exam_score: float,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """按批次和科目写入试卷。已有同一科目时覆盖题目，并清除该科成绩和小题分。"""
    from datasource.db.db import WriteDbSession

    name = (subject or "").strip()
    if name not in SUBJECTS:
        raise ValueError(f"科目须为 {'、'.join(SUBJECTS)}")
    rows = _normalize_questions(questions)
    if exam_score <= 0:
        raise ValueError("卷面满分必须大于 0")

    with WriteDbSession(db_type, config) as session:
        _ensure_exam_unique(session)
        batch = _one(
            session,
            "SELECT id, batch_name, exam_time FROM tb_exam_batch WHERE id = %s",
            (int(exam_batch_id),),
        )
        if batch is None:
            raise ValueError("考试批次不存在")
        batch_name = str(batch[1] or "").strip()
        exam_time = batch[2]
        exam_name = batch_name
        existing = _all(
            session,
            "SELECT id FROM tb_exam WHERE exam_batch_id = %s AND subject = %s",
            (int(exam_batch_id), name),
        )
        if len(existing) > 1:
            raise ValueError(f"批次中已有多份{name}试卷，请先整理后再导入")
        exam_id = int(existing[0][0]) if existing else None
        updated = exam_id is not None
        cleared_score_count = 0
        cleared_detail_count = 0
        if exam_id is None:
            created = _one(
                session,
                "INSERT INTO tb_exam "
                "(exam_name, exam_time, subject, exam_score, exam_type, exam_batch_id) "
                "VALUES (%s, %s, %s, %s, 'city', %s) RETURNING id",
                (exam_name, exam_time, name, float(exam_score), int(exam_batch_id)),
            )
            if created is None:
                raise ValueError("创建试卷失败")
            exam_id = int(created[0])
        else:
            cleared_detail_count = _delete_count(
                session, "DELETE FROM tb_score_detail WHERE exam_id = %s", (exam_id,)
            )
            cleared_score_count = _delete_count(
                session, "DELETE FROM tb_score WHERE exam_id = %s", (exam_id,)
            )
            _must_write(
                session,
                "UPDATE tb_exam SET exam_name = %s, exam_time = %s, exam_score = %s WHERE id = %s",
                (exam_name, exam_time, float(exam_score), exam_id),
            )
            _replace_questions(session, exam_id)
        for item in rows:
            _must_write(
                session,
                "INSERT INTO tb_exam_question "
                "(exam_name, question_no, content, question_score, exam_id, question_type) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    exam_name,
                    item["question_no"],
                    item["content"] or None,
                    item["question_score"],
                    exam_id,
                    item["question_type"] or None,
                ),
            )
        session.commit()
    return {
        "exam_id": exam_id,
        "exam_name": exam_name,
        "subject": name,
        "question_count": len(rows),
        "updated": updated,
        "cleared_score_count": cleared_score_count,
        "cleared_detail_count": cleared_detail_count,
    }


def _normalize_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not questions:
        raise ValueError("没有可写入的题目")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        qno = str(item.get("question_no") or "").strip()
        if not qno:
            raise ValueError(f"第 {index} 行题号为空")
        if len(qno) > 16:
            raise ValueError(f"题号「{qno}」超过 16 个字符")
        if qno in seen:
            raise ValueError(f"题号「{qno}」重复")
        seen.add(qno)
        try:
            score = float(item.get("question_score") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"题号「{qno}」分值无效") from exc
        if score < 0:
            raise ValueError(f"题号「{qno}」分值不能为负")
        content = str(item.get("content") or "").strip()
        rows.append(
            {
                "question_no": qno,
                "question_type": str(item.get("question_type") or "").strip()[:32],
                "question_score": round(score, 2),
                "content": content[:8000],
            }
        )
    return rows


_EXAM_UNIQUE_INDEX = "uk_exam_batch_subject"


def ensure_exam_batch_subject_index() -> list[str]:
    """服务启动时给 edu 库的 tb_exam 补上批次+科目唯一索引，并单独提交。

    重启不会执行 docs 里的 DDL，也不会进入试卷写入。索引必须在这里自己提交，
    否则写在试卷事务里时，后续失败会把建索引一起回滚。
    """
    from datasource.db.db import WriteDbSession

    notes: list[str] = []
    try:
        targets = _edu_write_targets()
    except Exception as exc:
        logger.exception("读取 edu 连接失败，跳过试卷唯一索引")
        return [f"读取 edu 连接失败: {exc}"]
    if not targets:
        message = "未找到 edu 数据源，未建立 tb_exam 唯一索引"
        logger.warning(message)
        return [message]
    for db_type, config in targets:
        label = f"{config.get('host')}:{config.get('port')}/{config.get('database')}"
        try:
            with WriteDbSession(db_type, config) as session:
                if db_type != "pg":
                    notes.append(f"{label} 不是 PostgreSQL，跳过")
                    continue
                if not _exam_table_schemas(session):
                    notes.append(f"{label} 没有 tb_exam，跳过")
                    continue
                _ensure_exam_unique(session)
            message = f"{label} 已确认唯一索引 {_EXAM_UNIQUE_INDEX}"
            logger.info(message)
            notes.append(message)
        except Exception as exc:
            logger.exception("建立 tb_exam 唯一索引失败: %s", label)
            notes.append(f"{label} 建立唯一索引失败: {exc}")
    return notes


def _edu_write_targets() -> list[tuple[str, dict[str, Any]]]:
    from common.core.config import get_settings
    from common.core.database import get_db_session
    from common.utils.aes import decrypt_conf
    from datasource.crud import crud_datasource
    from src.agent.education.knowledge_admin import parse_edu_database_url

    targets: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str, int, str]] = set()

    def add(db_type: str, config: dict[str, Any]) -> None:
        database = str(config.get("database") or "").strip()
        key = (
            db_type,
            str(config.get("host") or ""),
            int(config.get("port") or 0),
            database.lower(),
        )
        if not database or key in seen:
            return
        seen.add(key)
        targets.append((db_type, config))

    url = (get_settings().database_edu_url or "").strip()
    if url:
        db_type, config = parse_edu_database_url(url)
        add(db_type, config)
    pending: list[tuple[str, dict[str, Any]]] = []
    with get_db_session() as session:
        for ds in crud_datasource.get_datasources(session, skip=0, limit=500):
            raw = getattr(ds, "configuration", None)
            conf = decrypt_conf(raw) if raw else {}
            if str((conf or {}).get("database") or "").strip().lower() != "edu":
                continue
            pending.append((str(getattr(ds, "type", None) or "pg"), dict(conf or {})))
    for db_type, config in pending:
        add(db_type, config)
    return targets


def _exam_table_schemas(session: Any) -> list[str]:
    rows = _all(
        session,
        "SELECT n.nspname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND c.relkind = 'r' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY n.nspname",
        ("tb_exam",),
    )
    schemas: list[str] = []
    for row in rows:
        name = str(row[0] or "")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            schemas.append(name)
    return schemas


def _ensure_exam_unique(session: Any) -> None:
    """同一批次、同一科目只能有一份试卷。建索引后立即提交，避免被后续写入回滚。"""
    if getattr(session, "db_type", "pg") != "pg":
        return
    schemas = _exam_table_schemas(session) or [""]
    for schema in schemas:
        table = f'"{schema}".tb_exam' if schema else "tb_exam"
        if schema:
            found = _one(
                session,
                "SELECT 1 FROM pg_indexes WHERE schemaname = %s AND tablename = %s "
                "AND indexname = %s",
                (schema, "tb_exam", _EXAM_UNIQUE_INDEX),
            )
        else:
            found = _one(
                session,
                "SELECT 1 FROM pg_indexes WHERE tablename = %s AND indexname = %s",
                ("tb_exam", _EXAM_UNIQUE_INDEX),
            )
        if found:
            continue
        duplicates = _all(
            session,
            f"SELECT exam_batch_id, subject, COUNT(*) FROM {table} "
            "WHERE exam_batch_id IS NOT NULL "
            "GROUP BY exam_batch_id, subject HAVING COUNT(*) > 1",
            (),
        )
        if duplicates:
            sample = "、".join(f"{row[1]}(批次{row[0]}×{row[2]})" for row in duplicates[:5])
            raise ValueError(f"同一批次、同一科目存在多份试卷（{sample}），请先整理重复试卷后再导入")
        ok, msg, _data = session.execute_write(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_EXAM_UNIQUE_INDEX} "
            f"ON {table} (exam_batch_id, subject)",
            None,
        )
        if not ok:
            if "duplicat" in (msg or "").lower():
                raise ValueError("同一批次、同一科目存在多份试卷，请先整理重复试卷后再导入")
            raise ValueError(msg or "建立试卷唯一索引失败")
        session.commit()


def _delete_count(session: Any, sql: str, params: tuple) -> int:
    ok, msg, data = session.execute_write(sql, params)
    if not ok:
        raise ValueError(msg or "清除已有成绩失败")
    return int((data or {}).get("row_count") or 0)


def _must_write(session: Any, sql: str, params: tuple) -> None:
    ok, msg, _data = session.execute_write(sql, params)
    if not ok:
        raise ValueError(msg or "写入失败")


def _replace_questions(session: Any, exam_id: int) -> None:
    _must_write(
        session,
        "DELETE FROM tb_exam_question_knowledge WHERE question_id IN "
        "(SELECT id FROM tb_exam_question WHERE exam_id = %s)",
        (exam_id,),
    )
    _must_write(session, "DELETE FROM tb_exam_question WHERE exam_id = %s", (exam_id,))


def _one(session: Any, sql: str, params: tuple) -> tuple | None:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    rows = (data or {}).get("rows") or []
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return tuple(row.values())
    return tuple(row)


def _all(session: Any, sql: str, params: tuple) -> list[tuple]:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    rows = []
    for row in (data or {}).get("rows") or []:
        rows.append(tuple(row.values()) if isinstance(row, dict) else tuple(row))
    return rows


def _detect_subject(text: str, filename: str) -> str | None:
    stem = re.sub(r"\.[^.]+$", "", filename or "")
    for subject in SUBJECTS:
        if subject in stem:
            return subject
    for subject in SUBJECTS:
        if f"{subject}试卷" in text or f"{subject}试题" in text:
            return subject
    return None


def _ext(filename: str) -> str:
    match = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    return match.group(1).lower() if match else ""


# Word 把 Symbol、MT Extra 写进私用区（U+F000 + 字库字节）。阅读器靠嵌入字体画对，纯文本会变成方框或乱序。
_SYMBOL_PUA = {
    0x28: "(",
    0x29: ")",
    0x2B: "+",
    0x2D: "−",
    0x3C: "<",
    0x3D: "=",
    0x3E: ">",
    0x5E: "⊥",
    0x61: "α",
    0x62: "β",
    0x63: "χ",
    0x64: "δ",
    0x65: "ε",
    0x66: "φ",
    0x67: "γ",
    0x68: "η",
    0x69: "ι",
    0x6A: "ϕ",
    0x6B: "κ",
    0x6C: "λ",
    0x6D: "μ",
    0x6E: "ν",
    0x70: "π",
    0x71: "θ",
    0x72: "ρ",
    0x73: "σ",
    0x74: "τ",
    0x75: "υ",
    0x77: "ω",
    0x78: "ξ",
    0x79: "ψ",
    0x7A: "ζ",
    0x7B: "{",
    0x7D: "}",
    0xA2: "′",
    0xA3: "≤",
    0xA5: "∞",
    0xB1: "±",
    0xB3: "≥",
    0xB4: "×",
    0xB5: "∝",
    0xB6: "∂",
    0xB8: "÷",
    0xB9: "≠",
    0xBA: "≡",
    0xBB: "≈",
    0xC6: "∅",
    0xC7: "∩",
    0xC8: "∪",
    0xCE: "∈",
    0xCF: "∉",
    0xD0: "∠",
    0xD1: "∇",
    0xD5: "∏",
    0xD6: "√",
    0xD7: "⋅",
    0xE1: "⟨",
    0xE5: "∑",
    0xF1: "⟩",
}
_EXTRA_PUA = {
    0x49: "∩",
    0x4C: "…",
    0x55: "∪",
    0x72: "⃗",
    0x76: "⃗",
}
_SUPER = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_SUB = str.maketrans(
    "0123456789+-=()aehijklmnoprstuvx",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
)
_SIGN_FOLD = str.maketrans({"－": "-", "−": "-", "﹣": "-"})
_LINE_BAND = 8.0


def _pdf_text_layer(data: bytes) -> str:
    import fitz

    parts: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            parts.append(_page_reading_order(page))
    return "\n".join(parts)


def pdf_has_text_layer(data: bytes) -> bool:
    """文字层是否够直接切题。不够时选择 PDF 应默认打开图像识别。"""
    import fitz

    collected = 0
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            collected += len(_page_reading_order(page).strip())
            if collected >= _MIN_TEXT:
                return True
    return False


def _page_reading_order(page) -> str:
    """按视觉行、从左到右拼文字。公式符号在文字层里往往是逐个乱序存放的。"""
    chars: list[tuple] = []
    bare_serial = 0
    for span in page.get_texttrace():
        font = span.get("font") or ""
        direction = span.get("dir") or (1.0, 0.0)
        if abs(direction[0]) < 0.5:
            continue
        decoded: list[tuple] = []
        for uni, _glyph, _origin, bbox in span["chars"]:
            text = _decode_math_char(uni, font)
            if not text:
                continue
            decoded.append((bbox[0], bbox[1], bbox[2], bbox[3], text))
        span_text = "".join(item[4] for item in decoded)
        bare_id = 0
        if re.fullmatch(r"\d{1,2}", span_text):
            bare_serial += 1
            bare_id = bare_serial
        chars.extend((*item, bare_id) for item in decoded)
    if not chars:
        return page.get_text()
    return _chars_to_text(chars)


def _decode_math_char(code: int, font: str) -> str:
    if code < 32:
        return ""
    if 0xF000 <= code <= 0xF0FF:
        slot = code - 0xF000
        if "Extra" in font:
            return _EXTRA_PUA.get(slot, "")
        if "Symbol" in font:
            return _SYMBOL_PUA.get(slot, "")
    return chr(code)


def _chars_to_text(chars: list[tuple]) -> str:
    glyphs = [_glyph(item) for item in chars]
    if not glyphs:
        return ""
    pieces = _read_with_choice_tables(glyphs)
    return "\n".join(piece for piece in pieces if piece)


def _ymid(char: tuple) -> float:
    return (char[1] + char[3]) / 2


def _read_with_choice_tables(glyphs: list[tuple]) -> list[str]:
    """实验表格左右两列对齐，按行从左到右会把结论插进操作步骤。"""
    header = _choice_table_header(glyphs)
    if header is None:
        return [_glyphs_to_text(glyphs)]
    y_header, y_end, x_cut = header
    before = [char for char in glyphs if _ymid(char) < y_header - 6]
    table = [char for char in glyphs if y_header - 6 <= _ymid(char) < y_end]
    after = [char for char in glyphs if _ymid(char) >= y_end]
    return [
        _glyphs_to_text(before),
        _render_choice_table(table, y_header, x_cut),
        _glyphs_to_text(after),
    ]


def _choice_table_header(glyphs: list[tuple]) -> tuple[float, float, float] | None:
    rows: dict[int, list[tuple]] = {}
    for char in glyphs:
        if char[3] - char[1] < 8:
            continue
        rows.setdefault(round(_ymid(char) / 4), []).append(char)
    header_y = None
    for group in rows.values():
        text = "".join(char[4] for char in sorted(group, key=lambda item: item[0]))
        if "选项" in text and "结论" in text:
            header_y = sum(_ymid(char) for char in group) / len(group)
            break
    if header_y is None:
        return None
    y_end = _next_question_y(glyphs, header_y)
    if y_end is None:
        return None
    body = [char for char in glyphs if header_y + 8 <= _ymid(char) < y_end and char[0] > 125]
    x_cut = _column_cut(body)
    if x_cut is None:
        return None
    return header_y, y_end, x_cut


def _next_question_y(glyphs: list[tuple], y_header: float) -> float | None:
    dots = [char for char in glyphs if char[4] in "．." and _ymid(char) > y_header + 20]
    found = []
    for char in glyphs:
        if _ymid(char) < y_header + 20 or char[0] > 100 or not char[4].isdigit():
            continue
        near = any(_dot_follows(char, dot) for dot in dots)
        if near:
            found.append(_ymid(char))
    if not found:
        return None
    anchor = min(found)
    line = [char for char in glyphs if abs(_ymid(char) - anchor) < 5]
    return min(char[1] for char in line) - 1


def _dot_follows(char: tuple, dot: tuple) -> bool:
    return abs(_ymid(dot) - _ymid(char)) < 6 and -2 <= dot[0] - char[2] < 20


def _column_cut(glyphs: list[tuple]) -> float | None:
    rows: dict[int, list[tuple]] = {}
    for char in glyphs:
        rows.setdefault(round(_ymid(char) / 4), []).append(char)
    cuts = []
    for group in rows.values():
        ordered = sorted(group, key=lambda item: item[0])
        for prev, char in zip(ordered, ordered[1:]):
            if char[0] - prev[2] >= 10 and char[0] > 300:
                cuts.append(char[0])
    if len(cuts) < 3:
        return None
    cuts.sort()
    return cuts[len(cuts) // 2] - 1


def _render_choice_table(glyphs: list[tuple], y_header: float, x_cut: float) -> str:
    letters = [
        char
        for char in glyphs
        if char[4] in "ABCD" and char[0] < 130 and _ymid(char) > y_header + 6
    ]
    letters.sort(key=_ymid)
    if len(letters) < 2:
        return _glyphs_to_text(glyphs)
    header = _glyphs_to_text(
        [char for char in glyphs if _ymid(char) < y_header + 6]
    ).strip()
    bounds = [y_header + 6]
    for prev, char in zip(letters, letters[1:]):
        bounds.append((_ymid(prev) + _ymid(char)) / 2)
    bounds.append(max(_ymid(char) for char in glyphs) + 1)
    rows = []
    for letter, top, bottom in zip(letters, bounds, bounds[1:]):
        cell = [char for char in glyphs if top <= _ymid(char) < bottom and char[0] >= 125]
        operation = _cell_text([char for char in cell if char[0] < x_cut])
        conclusion = _cell_text([char for char in cell if char[0] >= x_cut])
        rows.append(f"{letter[4]}．{operation}")
        if conclusion:
            rows.append(conclusion)
    if header:
        rows.insert(0, header)
    return "\n".join(rows)


def _cell_text(glyphs: list[tuple]) -> str:
    if not glyphs:
        return ""
    return "".join(line.strip() for line in _glyphs_to_text(glyphs).splitlines())


def _glyphs_to_text(glyphs: list[tuple]) -> str:
    ordered = sorted(glyphs, key=lambda item: ((item[1] + item[3]) / 2, item[0]))
    if not ordered:
        return ""
    heights = sorted(char[3] - char[1] for char in ordered)
    typical = heights[len(heights) // 2] or 1.0
    full: list[tuple] = []
    short: list[tuple] = []
    for char in ordered:
        if char[3] - char[1] < typical * 0.8:
            short.append(char)
        else:
            full.append(char)
    lines = _group_band(full, 3.0)
    body_at = [index for index, line in enumerate(lines) if not _line_is_fraction(line["chars"])]
    for char in short:
        mid = (char[1] + char[3]) / 2
        best = None
        best_dy = _LINE_BAND + 0.1
        for index in body_at:
            dy = abs(mid - lines[index]["anchor"])
            if dy < best_dy:
                best = index
                best_dy = dy
        if best is None:
            lines.append({"anchor": mid, "height": char[3] - char[1], "chars": [char]})
        else:
            lines[best]["chars"].append(char)
    lines = _attach_overlays(lines)
    lines = _place_fraction_stacks(lines)
    boxes: list[tuple[float, float, str]] = []
    for line in lines:
        boxes.extend(_line_boxes(line["chars"]))
    rendered = _join_wrapped_lines(boxes)
    return "\n".join(_attach_formula_to_next_question(rendered))


def _group_band(chars: list[tuple], band: float) -> list[dict]:
    lines: list[dict] = []
    for char in chars:
        mid = (char[1] + char[3]) / 2
        height = max(char[3] - char[1], 1.0)
        limit = _LINE_BAND if lines and _overlaps_line(lines[-1]["chars"], char) else band
        if lines and abs(mid - lines[-1]["anchor"]) <= limit:
            line = lines[-1]
            line["chars"].append(char)
            if height >= line["height"] and abs(mid - line["anchor"]) <= 1.2:
                line["height"] = height
                line["anchor"] = mid
        else:
            lines.append({"anchor": mid, "height": height, "chars": [char]})
    return lines


def _overlaps_line(chars: list[tuple], char: tuple) -> bool:
    """叠在已有字形上的是上下标或着重号，横向上落在空隙里的才是另一行。"""
    return any(min(char[2], item[2]) - max(char[0], item[0]) > 0 for item in chars)


def _attach_overlays(lines: list[dict]) -> list[dict]:
    """箭头和着重号叠在汉字上，先并回正文，免得把分子一起带走。"""
    for index, line in enumerate(lines):
        if _has_cjk(line["chars"]) or _looks_like_options(line["chars"]):
            continue
        kept: list[tuple] = []
        for char in line["chars"]:
            if re.fullmatch(r"[0-9A-Za-zπ√]", char[4]):
                kept.append(char)
                continue
            host = _overlay_host(lines, index, char)
            if host is None:
                kept.append(char)
            else:
                host["chars"].append(char)
        line["chars"] = kept
    return [line for line in lines if line["chars"]]


def _looks_like_options(chars: list[tuple]) -> bool:
    text = "".join(char[4] for char in chars)
    return re.search(r"[A-D][．.]", text) is not None


def _has_cjk(chars: list[tuple]) -> bool:
    return any("\u4e00" <= char[4][:1] <= "\u9fff" for char in chars)


def _overlay_host(lines: list[dict], index: int, char: tuple) -> dict | None:
    """只收回真正压在字上的着重号和箭头。下一行的选项、坐标轴不借。"""
    best = None
    best_dy = 8.0
    mid = (char[1] + char[3]) / 2
    for other, line in enumerate(lines):
        if other == index:
            continue
        if not _has_cjk(line["chars"]) and char[4] != "⃗":
            continue
        if not _mark_sits_on(line["chars"], char):
            continue
        dy = abs(line["anchor"] - mid)
        if dy < best_dy:
            best = line
            best_dy = dy
    return best


def _mark_sits_on(chars: list[tuple], char: tuple) -> bool:
    mark_h = char[3] - char[1]
    for item in chars:
        if min(char[2], item[2]) - max(char[0], item[0]) <= 0:
            continue
        host_h = max(item[3] - item[1], 0.1)
        overlap = min(char[3], item[3]) - max(char[1], item[1])
        if char[4] == "⃗" and overlap > 0:
            return True
        if mark_h >= host_h * 0.75:
            continue
        if overlap > 0 or 0 <= char[1] - item[3] <= 3.0:
            return True
    return False


def _line_is_fraction(chars: list[tuple]) -> bool:
    """单独漂着的数字行是分子或分母，不是正文。"""
    if not chars:
        return False
    text = "".join(char[4] for char in chars)
    if re.fullmatch(r"[0-9A-Za-zπ√]+", text) is None:
        return False
    if len(chars) <= 4:
        return True
    ordered = sorted(chars, key=lambda item: item[0])
    return any(ordered[i][0] - ordered[i - 1][2] > 8 for i in range(1, len(ordered)))


def _place_fraction_stacks(lines: list[dict]) -> list[dict]:
    """上下对齐的数字合成 a/b，再按横坐标放回正文。"""
    piece_at = [index for index, line in enumerate(lines) if _line_is_fraction(line["chars"])]
    clusters: list[dict] = []
    for index in piece_at:
        for group in _split_x(lines[index]["chars"], 8.0):
            mid = sum((char[1] + char[3]) / 2 for char in group) / len(group)
            clusters.append({"chars": group, "y": mid, "used": False, "index": index})
    stacks: list[list[dict]] = []
    for cluster in clusters:
        if cluster["used"]:
            continue
        stack = [cluster]
        cluster["used"] = True
        grew = True
        while grew:
            grew = False
            for other in clusters:
                if other["used"]:
                    continue
                close = min(abs(other["y"] - item["y"]) for item in stack) <= 18
                if close and _cluster_overlap(stack, other) > 0.4:
                    stack.append(other)
                    other["used"] = True
                    grew = True
        stacks.append(sorted(stack, key=lambda item: item["y"]))
    consumed = set(piece_at)
    restored: dict[int, list[tuple]] = {index: [] for index in piece_at}
    for stack in stacks:
        token = "/".join(_cluster_text(item) for item in stack)
        y = sum(item["y"] for item in stack) / len(stack)
        x0 = min(char[0] for item in stack for char in item["chars"])
        x1 = max(char[2] for item in stack for char in item["chars"])
        host = _nearest_body(lines, consumed, y, x0)
        if host is not None and len(stack) == 1 and not _formula_near(host["chars"], x0, x1):
            host = None
        if host is None:
            if len(stack) == 1:
                restored[stack[0]["index"]].extend(stack[0]["chars"])
            else:
                lines.append({"anchor": y, "height": 10.0, "chars": [(x0, y, x1, y + 10, token, 0)]})
            continue
        if len(stack) == 1:
            above = stack[0]["y"] < host["anchor"]
            if _append_fraction(host["chars"], x0, x1, token, above):
                continue
            token = f"{token}/" if above else f"/{token}"
        y0 = min(char[1] for item in stack for char in item["chars"])
        y1 = max(char[3] for item in stack for char in item["chars"])
        host["chars"].append((x0, y0, x1, y1, token, 0))
    for index, chars in restored.items():
        if not chars:
            continue
        lines[index]["chars"] = chars
        consumed.discard(index)
    return [line for index, line in enumerate(lines) if index not in consumed]


def _split_x(chars: list[tuple], gap: float) -> list[list[tuple]]:
    ordered = sorted(chars, key=lambda item: item[0])
    groups = [[ordered[0]]]
    for char in ordered[1:]:
        if char[0] - groups[-1][-1][2] > gap:
            groups.append([char])
        else:
            groups[-1].append(char)
    return groups


def _cluster_text(cluster: dict) -> str:
    return "".join(char[4] for char in sorted(cluster["chars"], key=lambda item: item[0]))


def _cluster_overlap(stack: list[dict], other: dict) -> float:
    best = 0.0
    for item in stack:
        best = max(best, _span_overlap(item["chars"], other["chars"]))
    return best


def _span_overlap(left: list[tuple], right: list[tuple]) -> float:
    a0, a1 = min(char[0] for char in left), max(char[2] for char in left)
    b0, b1 = min(char[0] for char in right), max(char[2] for char in right)
    width = max(min(a1 - a0, b1 - b0), 0.1)
    return max(0.0, min(a1, b1) - max(a0, b0)) / width


def _formula_near(chars: list[tuple], x0: float, x1: float) -> bool:
    """旁边有数字或字母才是分式，插在汉字中间的 A、B 是图上的标注。"""
    for char in chars:
        if min(char[2], x1 + 8) - max(char[0], x0 - 8) <= 0:
            continue
        if re.fullmatch(r"[0-9A-Za-zπ√]+", char[4]):
            return True
    return False


def _nearest_body(lines: list[dict], consumed: set[int], y: float, x0: float) -> dict | None:
    best = None
    best_dy = 22.0
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        dy = abs(line["anchor"] - y)
        if dy < best_dy and any(abs((char[0] + char[2]) / 2 - x0) < 36 for char in line["chars"]):
            best = line
            best_dy = dy
    return best


def _append_fraction(chars: list[tuple], x0: float, x1: float, token: str, above: bool) -> bool:
    best = None
    best_ov = 0.0
    for index, char in enumerate(chars):
        overlap = min(char[2], x1) - max(char[0], x0)
        if overlap > best_ov and re.fullmatch(r"[0-9A-Za-zπ√]+", char[4]):
            best = index
            best_ov = overlap
    if best is None:
        return False
    char = chars[best]
    text = f"{token}/{char[4]}" if above else f"{char[4]}/{token}"
    chars[best] = (char[0], char[1], max(char[2], x1), char[3], text, char[5])
    return True


def _attach_formula_to_next_question(rows: list[tuple[float, str]]) -> list[str]:
    """大题号左侧对齐，其上方伸出的公式行属于这道题，不能落到上一题末尾。"""
    output: list[str] = []
    index = 0
    while index < len(rows):
        left, text = rows[index]
        if index + 1 < len(rows):
            next_left, next_text = rows[index + 1]
            matched = re.match(r"(\d{1,2}[．.、])(.*)", next_text, re.S)
            if matched and _is_detached_formula(text) and left > next_left + 30:
                rows[index + 1] = (next_left, matched.group(1) + text + matched.group(2))
                index += 1
                continue
        output.append(text)
        index += 1
    return output


def _is_detached_formula(text: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", text) or re.search(r"[A-D]．", text):
        return False
    return re.search(r"[=<>≤≥+\-−]", text) is not None


def _glyph(item: tuple) -> tuple:
    if len(item) >= 6:
        return item[:6]
    return (*item[:5], 0)


def _line_boxes(chars: list[tuple]) -> list[tuple[float, float, str]]:
    ordered = _dedupe_glyphs(sorted(chars, key=lambda item: item[0]))
    if not ordered:
        return []
    raw: list[tuple[float, float, str, bool]] = []
    for group in _split_wide(ordered, 90.0):
        for text in _render_group(group):
            raw.append((*text, _has_column_gap(group)))
    kept: list[tuple[float, float, str, bool]] = []
    for left, right, text, columned in raw:
        if kept and re.search(r"[\u4e00-\u9fff]", kept[-1][2]) and _is_margin_piece(text):
            continue
        kept.append((left, right, text, columned))
    boxes: list[tuple[float, float, str]] = []
    for left, right, text, columned in kept:
        # 同一行里隔开的多栏不能按普通换行接起来，否则栏尾会粘到下一栏的栏头。
        boxes.append((left, left if columned else right, text))
    return boxes


def _has_column_gap(chars: list[tuple]) -> bool:
    ordered = sorted(chars, key=lambda item: item[0])
    for prev, char in zip(ordered, ordered[1:]):
        if char[0] - prev[2] < 14:
            continue
        if _is_cjk_char(prev[4]) and _is_cjk_char(char[4]):
            return True
    return False


def _is_cjk_char(text: str) -> bool:
    return bool(text) and "\u4e00" <= text[0] <= "\u9fff"


_WRAP_BREAK_RE = re.compile(
    r"^(?:[A-D][．.]|[（(]\s*\d|[（(][一二三四五六七八九十]+[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d{1,2}[．.、]"
    r"|[一二三四五六七八九十]+、|第|单项|多项|非选择|填空|解答|\d{1,2}$)"
)


def _join_wrapped_lines(boxes: list[tuple[float, float, str]]) -> list[tuple[float, str]]:
    """排版换行不是新的一句。满行且下一行仍从同一左缘起笔时，把它们接回去。"""
    joined: list[list] = []
    for left, right, text in boxes:
        text = text.strip()
        if not text:
            continue
        if joined:
            prev_left, prev_right, prev = joined[-1]
            same_edge = abs(left - prev_left) <= 18
            full_line = prev_right - prev_left >= 280
            open_ended = prev[-1:] not in "。！？；.．"
            # 已经写到 D 项的这一行是选项的最后一行，后面的段落是下一组题。
            finished_options = re.search(r"D[．.]", prev) is not None
            if (
                same_edge
                and full_line
                and open_ended
                and not finished_options
                and _WRAP_BREAK_RE.match(text) is None
            ):
                sep = "" if _wrap_without_space(prev, text) else " "
                joined[-1] = [prev_left, max(prev_right, right), prev + sep + text]
                continue
        joined.append([left, right, text])
    return [(item[0], item[2]) for item in joined]


def _wrap_without_space(prev: str, text: str) -> bool:
    left, right = prev[-1:], text[:1]
    cjk = ("\u4e00" <= left <= "\u9fff") or ("\u4e00" <= right <= "\u9fff")
    return cjk or left in "，、：；"


def _split_wide(chars: list[tuple], gap: float) -> list[list[tuple]]:
    groups = [[chars[0]]]
    for char in chars[1:]:
        if char[0] - groups[-1][-1][2] > gap:
            groups.append([char])
        else:
            groups[-1].append(char)
    return groups


def _is_margin_piece(text: str) -> bool:
    """紧挨在中文右边、又隔得很远的字母和刻度是插图，不是题干。"""
    body = text.strip()
    if re.match(r"^[A-D][．.]", body) or re.fullmatch(r"\d{1,2}", body):
        return False
    return re.search(r"[\u4e00-\u9fff]", body) is None


def _render_group(chars: list[tuple]) -> list[tuple[float, float, str]]:
    baseline, body_h = _line_metrics(chars)
    segments: list[list[tuple]] = []
    buf: list[tuple] = []
    bare_buf: list[tuple] = []

    def flush_bare() -> None:
        nonlocal bare_buf
        if bare_buf:
            segments.append(bare_buf)
            bare_buf = []

    for index, char in enumerate(chars):
        if _isolate_bare_number(chars, index, baseline, body_h):
            if buf:
                segments.append(buf)
                buf = []
            bare_buf.append(char)
            continue
        flush_bare()
        buf.append(char)
    flush_bare()
    if buf:
        segments.append(buf)
    boxes: list[tuple[float, float, str]] = []
    for segment in segments:
        text = _line_text(segment, baseline, body_h).strip()
        if not text:
            continue
        left = min(item[0] for item in segment)
        right = max(item[2] for item in segment)
        boxes.append((left, right, text))
    return boxes


def _isolate_bare_number(chars: list[tuple], index: int, baseline: float, body_h: float) -> bool:
    """七选五把题号单独做成一个文字块，和正文同一行。上标数字、带点的题号不拆。"""
    char = chars[index]
    if not char[5]:
        return False
    start = index
    while start > 0 and chars[start - 1][5] == char[5]:
        start -= 1
    end = index
    while end + 1 < len(chars) and chars[end + 1][5] == char[5]:
        end += 1
    run = chars[start : end + 1]
    if any(_is_raised(item, baseline, body_h) for item in run):
        return False
    nxt = chars[end + 1] if end + 1 < len(chars) else None
    while nxt is not None and nxt[4].strip() == "":
        end += 1
        nxt = chars[end + 1] if end + 1 < len(chars) else None
    # 只拆开后面隔着空隙、紧跟大写英文单词的编号，例如七选五的 “36 To”。
    # 5 mL、2x 这种单位和系数，以及 A．3 这种选项数字，不能拆。
    if nxt is None or not (nxt[4][:1].isascii() and nxt[4][:1].isupper()):
        return False
    if nxt[0] - chars[end][2] <= 1.5:
        return False
    prev = chars[start - 1] if start > 0 else None
    return prev is None or prev[4][-1:] not in "．.、" or chars[start][0] - prev[2] > 2.0


def _dedupe_glyphs(ordered: list[tuple]) -> list[tuple]:
    # 描边加填充会把同一个字画两遍，位置几乎重合。
    kept: list[tuple] = []
    for char in ordered:
        if kept:
            prev = kept[-1]
            overlap = min(prev[2], char[2]) - max(prev[0], char[0])
            width = max(char[2] - char[0], 0.1)
            if char[4] == prev[4] and abs(char[1] - prev[1]) < 1.2 and overlap > 0.6 * width:
                continue
        kept.append(char)
    return kept


def _line_metrics(chars: list[tuple]) -> tuple[float, float]:
    bottoms = sorted(item[3] for item in chars)
    baseline = bottoms[min(len(bottoms) - 1, len(bottoms) * 3 // 4)]
    heights = sorted(item[3] - item[1] for item in chars)
    return baseline, heights[len(heights) // 2]


def _is_raised(char: tuple, baseline: float, body_h: float) -> bool:
    height = char[3] - char[1]
    return body_h > 0 and height < body_h * 0.8 and baseline - char[3] >= 2.5


def _typical_top(chars: list[tuple], body_h: float) -> float | None:
    tops = [item[1] for item in chars if abs((item[3] - item[1]) - body_h) <= 1.5]
    if not tops:
        return None
    tops.sort()
    return tops[len(tops) // 2]


def _script_text(text: str, table: dict[int, str]) -> str:
    return text.translate(_SIGN_FOLD).translate(table)


def _line_text(chars: list[tuple], baseline: float, body_h: float) -> str:
    chars = _fold_exponent_fractions(chars, baseline, body_h)
    chars = sorted(chars, key=lambda item: (item[0], -item[1]))
    body_top = _typical_top(chars, body_h)
    parts: list[str] = []
    prev_right = None
    prev_text = ""
    for left, top, right, bottom, text, _bare in chars:
        height = bottom - top
        lowered = (
            body_top is not None
            and body_h > 0
            and height < body_h * 0.8
            and top >= body_top + 2.0
            and baseline - bottom < 2.5
        )
        if _is_raised((left, top, right, bottom, text, 0), baseline, body_h):
            text = _script_text(text, _SUPER)
        elif lowered or (body_h > 0 and height < body_h * 0.8 and bottom > baseline + 1.5):
            text = _script_text(text, _SUB)
        gap = prev_right is not None and left - prev_right > 2.0
        after_dot = (
            prev_right is not None
            and prev_text[-1:] in ".．"
            and text[:1].isupper()
            and left - prev_right > 0.6
        )
        if (gap or after_dot) and text and not unicodedata.combining(text[0]):
            parts.append(" ")
        parts.append(text)
        prev_right = right
        prev_text = text
    return _tidy_math("".join(parts))


def _fold_exponent_fractions(chars: list[tuple], baseline: float, body_h: float) -> list[tuple]:
    """指数位置上的 1/e 叠在一起，写成 ^(1/e)，不要排成 ¹e。"""
    ordered = sorted(chars, key=lambda item: item[0])
    folded: list[tuple] = []
    index = 0
    while index < len(ordered):
        char = ordered[index]
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        width = max(char[2] - char[0], 0.1)
        overlap = 0.0 if nxt is None else min(char[2], nxt[2]) - max(char[0], nxt[0])
        body_top = _typical_top(ordered, body_h)
        lowered = (
            nxt is not None
            and body_top is not None
            and body_h > 0
            and nxt[3] - nxt[1] < body_h * 0.8
            and nxt[1] >= body_top + 2.0
        )
        if (
            nxt is not None
            and _is_raised(char, baseline, body_h)
            and overlap > 0.5 * width
            and not _is_raised(nxt, baseline, body_h)
            and not lowered
        ):
            token = f"^({char[4]}/{nxt[4]})"
            folded.append((char[0], char[1], max(char[2], nxt[2]), char[3], token, 0))
            index += 2
            continue
        folded.append(char)
        index += 1
    return folded


_VISION_PROMPT = (
    "转写这一页试卷上的全部文字。"
    "只输出页面上的文字，不要解释，不要开场白，不要改写。"
    "题号单独成行，写成“1.”这样的形式；小题写成“（1）”。"
    "七选五空格里的题号保留，并单独写成一行，例如“36.”；选项如果到 G，也逐行写出。"
    "分值保留，如“（3分）”。选项 A. B. C. D. 各占一行。"
    "看不清的字用□，不要编造。"
    "公式按卷面写成普通文字，不要使用 LaTeX，不要出现 $ 和反斜杠。"
    "根号写成√，分式写成分子/分母，例如 √3/3。"
)
_TABLE_PROMPT = (
    "这张图只有一张表格。只输出 markdown 表格，不要解释。"
    "从左到右、从上到下逐格转写，每一行的单元格数必须和第一行相同。"
    "数字即使和相邻格子相同，也要逐格写出，不能省略，不能合并。"
    "看不清的格子用□，不要编造。"
    "公式按卷面写成普通文字，不要使用 LaTeX。"
)
_MD_TABLE_RE = re.compile(r"(?m)(?:^\|.*\|\s*\n?){2,}")


def _ocr_pdf(data: bytes, on_progress: Any = None) -> str:
    if on_progress:
        on_progress(0, 0, "正在准备页面")
    pages, tables = _pdf_page_parts(data)
    return _vision_transcribe(pages, on_progress, tables)


def _pdf_page_parts(data: bytes) -> tuple[list[bytes], list[list[bytes]]]:
    import fitz

    pages: list[bytes] = []
    tables: list[list[bytes]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            if len(pages) >= 16:
                break
            pages.append(page.get_pixmap(dpi=150, alpha=False).tobytes("png"))
            clips: list[bytes] = []
            for rect in _table_rects(page):
                clip = page.get_pixmap(dpi=300, alpha=False, clip=rect)
                clips.append(clip.tobytes("png"))
            tables.append(clips)
    return pages, tables


def _table_rects(page: Any) -> list[Any]:
    """从矢量线框里找出表格。文字层缺失时，整页转写容易把宽表写漏。"""
    import fitz

    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            kind = item[0]
            if kind == "re":
                _collect_rule(item[1], horizontal, vertical)
            elif kind == "l":
                _collect_line(item[1], item[2], horizontal, vertical)
    horizontal.sort(key=lambda seg: (seg[1], seg[0]))
    groups: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    for seg in horizontal:
        if not current:
            current = [seg]
            continue
        prev = current[-1]
        same_span = abs(seg[0] - prev[0]) <= 8 and abs(seg[2] - prev[2]) <= 8
        close = 0 <= seg[1] - prev[1] <= 28
        if same_span and close:
            current.append(seg)
        else:
            groups.append(current)
            current = [seg]
    if current:
        groups.append(current)
    found: list[Any] = []
    for group in groups:
        if len(group) < 3:
            continue
        x0 = min(seg[0] for seg in group)
        x1 = max(seg[2] for seg in group)
        y0 = min(seg[1] for seg in group)
        y1 = max(seg[1] for seg in group)
        columns = [
            seg
            for seg in vertical
            if y0 - 2 <= seg[1] and seg[2] <= y1 + 2 and x0 - 2 <= seg[0] <= x1 + 2
        ]
        if len(columns) < 3:
            continue
        found.append(fitz.Rect(x0 - 3, y0 - 3, x1 + 3, y1 + 3) & page.rect)
    return found


def _collect_rule(rect: Any, horizontal: list[tuple[float, float, float]], vertical: list[tuple[float, float, float]]) -> None:
    if rect.height <= 1.2 and rect.width >= 80:
        horizontal.append((rect.x0, (rect.y0 + rect.y1) / 2, rect.x1))
    elif rect.width <= 1.2 and rect.height >= 8:
        vertical.append(((rect.x0 + rect.x1) / 2, rect.y0, rect.y1))


def _collect_line(start: Any, end: Any, horizontal: list[tuple[float, float, float]], vertical: list[tuple[float, float, float]]) -> None:
    if abs(start.y - end.y) <= 1.2 and abs(start.x - end.x) >= 80:
        horizontal.append((min(start.x, end.x), (start.y + end.y) / 2, max(start.x, end.x)))
    elif abs(start.x - end.x) <= 1.2 and abs(start.y - end.y) >= 8:
        vertical.append(((start.x + end.x) / 2, min(start.y, end.y), max(start.y, end.y)))


def _ocr_image(data: bytes, on_progress: Any = None) -> str:
    return _ocr_images([data], on_progress)


def _ocr_images(images: list[bytes], on_progress: Any = None) -> str:
    if not images:
        return ""
    return _vision_transcribe(images, on_progress)


def _vision_llm():
    from src.common.core.config import get_settings
    from src.llm.service import create_llm

    settings = get_settings()
    if not (settings.llm_base_url and settings.llm_api_key):
        raise ValueError("没有配置大模型，无法识别没有文字层的试卷")
    kwargs: dict[str, Any] = {
        "provider": "openai",
        "timeout": 180,
        "temperature": 0,
        "max_tokens": 8192,
    }
    if "minimax" in (settings.llm_model or "").lower():
        kwargs["thinking"] = "disabled"
    return create_llm(**kwargs)


def _vision_transcribe(
    images: list[bytes],
    on_progress: Any = None,
    table_images: list[list[bytes]] | None = None,
) -> str:
    """每页单独交给大模型转写。页内的矢量表格再单独裁切识别，避免宽表被写漏。"""
    llm = _vision_llm()
    total = len(images)
    pages: list[str] = []
    for index, blob in enumerate(images, start=1):
        crops = table_images[index - 1] if table_images and index - 1 < len(table_images) else []
        if on_progress:
            on_progress(index - 1, total, f"正在识别第 {index}/{total} 页")
        try:
            text = _vision_ask(llm, _vision_content([blob], index, _page_prompt(len(crops))))
        except Exception as exc:
            raise ValueError(f"第{index}页图片识别失败：{exc}") from exc
        if crops:
            if on_progress:
                on_progress(index - 1, total, f"正在识别第 {index}/{total} 页的表格")
            rendered: list[str] = []
            for crop_index, crop in enumerate(crops, start=1):
                try:
                    table = _vision_ask(
                        llm, _vision_content([crop], prompt=_TABLE_PROMPT, label=False)
                    )
                except Exception as exc:
                    raise ValueError(f"第{index}页第{crop_index}个表格识别失败：{exc}") from exc
                rendered.append(table)
            text = _apply_tables(text, rendered)
        if text:
            pages.append(text)
    if on_progress and total:
        on_progress(total, total, "正在整理题目")
    return "\n".join(pages)


def _page_prompt(table_count: int) -> str:
    if table_count <= 0:
        return _VISION_PROMPT
    markers = "、".join(f"【表{i}】" for i in range(1, table_count + 1))
    return (
        _VISION_PROMPT
        + f"本页有{table_count}个表格，不要转写表格中的文字。"
        + f"按从上到下的位置，各单独一行依次写{markers}。"
    )


def _vision_ask(llm: Any, content: list[dict[str, Any]]) -> str:
    raw = llm.chat([{"role": "user", "content": content}])
    return _plain_formula(_strip_transcript(_message_text(raw)))


_SUB_DIGITS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_LATEX_COMMANDS = (
    ("\\times", "×"),
    ("\\cdot", "·"),
    ("\\leq", "≤"),
    ("\\geq", "≥"),
    ("\\le", "≤"),
    ("\\ge", "≥"),
    ("\\neq", "≠"),
    ("\\approx", "≈"),
    ("\\pm", "±"),
    ("\\infty", "∞"),
    ("\\pi", "π"),
    ("\\alpha", "α"),
    ("\\beta", "β"),
    ("\\gamma", "γ"),
    ("\\theta", "θ"),
    ("\\lambda", "λ"),
    ("\\mu", "μ"),
    ("\\xi", "ξ"),
    ("\\sigma", "σ"),
    ("\\chi", "χ"),
    ("\\omega", "ω"),
    ("\\Delta", "Δ"),
    ("\\ln", "ln"),
    ("\\log", "log"),
    ("\\sin", "sin"),
    ("\\cos", "cos"),
    ("\\tan", "tan"),
    ("\\sqrt", "√"),
    ("\\left", ""),
    ("\\right", ""),
    ("\\mathrm", ""),
    ("\\text", ""),
    ("\\overrightarrow", ""),
    ("\\vec", ""),
)


def _plain_formula(text: str) -> str:
    """模型常把印刷公式写成 LaTeX。转回卷面上的普通写法。"""
    cleaned = (text or "").replace("$$", "").replace("$", "")
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"\\sqrt\{([^{}]*)\}", lambda match: _radical(match.group(1)), cleaned)
        cleaned = re.sub(
            r"\\frac\{([^{}]*)\}\{([^{}]*)\}",
            lambda match: _fraction(match.group(1), match.group(2)),
            cleaned,
        )
    cleaned = re.sub(
        r"_\{([0-9]{1,3})\}",
        lambda match: match.group(1).translate(_SUB_DIGITS),
        cleaned,
    )
    cleaned = re.sub(
        r"\^\{([0-9]{1,3})\}",
        lambda match: match.group(1).translate(_SUP_DIGITS),
        cleaned,
    )
    for src, dst in _LATEX_COMMANDS:
        cleaned = cleaned.replace(src, dst)
    cleaned = re.sub(r"\\[a-zA-Z]+", "", cleaned)
    return cleaned.replace("{", "").replace("}", "")


def _radical(body: str) -> str:
    if re.fullmatch(r"[\d.]+|[A-Za-z]", body):
        return "√" + body
    return f"√({body})"


def _fraction(numerator: str, denominator: str) -> str:
    simple = r"[\d.√A-Za-z+-]+"
    if re.fullmatch(simple, numerator) and re.fullmatch(simple, denominator):
        return f"{numerator}/{denominator}"
    return f"({numerator})/({denominator})"


def _apply_tables(text: str, tables: list[str]) -> str:
    """用单独识别的表格替换占位符；模型若仍写出半张表，则按顺序换掉。"""
    used: list[int] = []

    def take_marker(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(tables) and index not in used:
            used.append(index)
            return f"\n@@TABLE{index}@@\n"
        return ""

    rewritten = re.sub(r"【表(\d+)】", take_marker, text or "")
    unused = [index for index in range(len(tables)) if index not in used]

    def take_markdown(match: re.Match[str]) -> str:
        if not unused:
            return ""
        index = unused.pop(0)
        return f"\n@@TABLE{index}@@\n"

    rewritten = _MD_TABLE_RE.sub(take_markdown, rewritten)
    for index, table in enumerate(tables):
        token = f"@@TABLE{index}@@"
        block = table.strip()
        if token in rewritten:
            rewritten = rewritten.replace(token, block, 1)
        elif block:
            rewritten = rewritten.rstrip() + "\n" + block
    return re.sub(r"\n{3,}", "\n\n", rewritten).strip()


def _vision_content(
    images: list[bytes],
    page_no: int = 1,
    prompt: str = _VISION_PROMPT,
    label: bool = True,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for offset, blob in enumerate(images):
        index = page_no + offset
        encoded = base64.b64encode(blob).decode("ascii")
        if label:
            content.append({"type": "text", "text": f"第{index}页"})
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{_image_mime(blob)};base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    return content


def _image_mime(blob: bytes) -> str:
    if blob.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if blob.startswith(b"\x89PNG"):
        return "image/png"
    if blob.startswith(b"GIF8"):
        return "image/gif"
    if len(blob) >= 12 and blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _strip_transcript(text: str) -> str:
    cleaned = re.sub(r"(?s)<think>.*?</think>", "", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def _docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
    return "\n".join(lines)
