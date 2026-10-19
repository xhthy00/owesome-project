"""知识点核对。核对结果写在 edu 业务库，模型使用平台已配置的大模型。"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from datasource.db.db import WriteDbSession
from src.agent.education.exam_library import (
    ExamActor,
    _replace_links,
    get_exam,
    normalize_links,
)

logger = logging.getLogger(__name__)

_guard = threading.Lock()
_busy = False
_cancelled: set[int] = set()

__all__ = [
    "apply_suggestions",
    "get_report",
    "get_task",
    "parse_match_reply",
    "start_check",
]


def parse_match_reply(
    raw: str,
    candidates: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> dict[str, Any]:
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw or "")
    text = text.replace("```json", "").replace("```", "").strip() or "{}"
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("核对结果不是 JSON 对象")
    by_name = {str(item.get("knowledge_name") or ""): int(item["id"]) for item in candidates}
    current_by_name = {
        str(item.get("knowledge_name") or f"ID:{item.get('knowledge_id')}"): int(item["knowledge_id"])
        for item in current
        if item.get("knowledge_id") is not None
    }
    add = []
    for item in parsed.get("add") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        knowledge_id = by_name.get(name)
        if knowledge_id is None:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        add.append(
            {
                "knowledge_id": knowledge_id,
                "knowledge_name": name,
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": str(item.get("reason") or ""),
            }
        )
    remove = []
    for item in parsed.get("remove") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        knowledge_id = current_by_name.get(name)
        if knowledge_id is None:
            continue
        remove.append(
            {
                "knowledge_id": knowledge_id,
                "knowledge_name": name,
                "reason": str(item.get("reason") or ""),
            }
        )
    ok = bool(parsed.get("ok"))
    verdict = "ok" if ok or not (add or remove) else "needs_review"
    return {"verdict": verdict, "add": add, "remove": remove}


def _one(session: Any, sql: str, params: tuple) -> tuple | None:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    rows = (data or {}).get("rows") or []
    if not rows:
        return None
    row = rows[0]
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _all(session: Any, sql: str, params: tuple) -> list[tuple]:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    return [
        tuple(row.values()) if isinstance(row, dict) else tuple(row)
        for row in (data or {}).get("rows") or []
    ]


def _must_write(session: Any, sql: str, params: tuple) -> None:
    ok, msg, _data = session.execute_write(sql, params)
    if not ok:
        raise ValueError(msg or "写入失败")


def _ensure_tables(session: Any) -> None:
    _must_write(
        session,
        """
        CREATE TABLE IF NOT EXISTS tb_exam_match_task (
            id BIGSERIAL PRIMARY KEY,
            exam_id BIGINT NOT NULL,
            scope VARCHAR(16) NOT NULL,
            question_id BIGINT,
            status VARCHAR(16) NOT NULL,
            total_count INT NOT NULL DEFAULT 0,
            done_count INT NOT NULL DEFAULT 0,
            failed_count INT NOT NULL DEFAULT 0,
            summary TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        (),
    )
    _must_write(
        session,
        """
        CREATE TABLE IF NOT EXISTS tb_exam_match_item (
            id BIGSERIAL PRIMARY KEY,
            task_id BIGINT NOT NULL,
            question_id BIGINT NOT NULL,
            question_no VARCHAR(32),
            content TEXT,
            current_links TEXT,
            add_suggestions TEXT,
            remove_suggestions TEXT,
            verdict VARCHAR(16),
            adopted BOOLEAN DEFAULT FALSE
        )
        """,
        (),
    )


def _loads(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _task_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "exam_id": int(row[1]),
        "scope": row[2],
        "question_id": None if row[3] is None else int(row[3]),
        "status": row[4],
        "total": int(row[5] or 0),
        "done": int(row[6] or 0),
        "failed": int(row[7] or 0),
        "summary": row[8] or "",
        "error": row[9] or "",
    }


def _item_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "question_id": int(row[1]),
        "question_no": "" if row[2] is None else str(row[2]),
        "content": row[3] or "",
        "current_links": _loads(row[4]),
        "add_suggestions": _loads(row[5]),
        "remove_suggestions": _loads(row[6]),
        "verdict": row[7] or "",
        "adopted": bool(row[8]),
    }


def _require_exam(db_type: str, config: dict[str, Any], actor: ExamActor, exam_id: int) -> dict:
    detail = get_exam(db_type, config, actor, exam_id)
    if detail is None:
        raise ValueError("试卷不存在或无权查看")
    exam = detail["exam"]
    if not exam.get("subject"):
        raise ValueError("试卷未设置科目，无法核对")
    return detail


def start_check(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
    question_id: int | None = None,
) -> dict[str, Any]:
    global _busy
    detail = _require_exam(db_type, config, actor, exam_id)
    questions = detail["questions"]
    if question_id is not None:
        questions = [item for item in questions if int(item["id"]) == int(question_id)]
        if not questions:
            raise ValueError("题目不存在")
    if not questions:
        raise ValueError("试卷还没有题目")
    with _guard:
        if _busy:
            raise ValueError("已有知识点核对在进行，请稍后再试")
        _busy = True
    try:
        with WriteDbSession(db_type, config) as session:
            _ensure_tables(session)
            _must_write(
                session,
                "UPDATE tb_exam_match_task SET status = 'failed', error = '核对已中断' "
                "WHERE status = 'running'",
                (),
            )
            created = _one(
                session,
                "INSERT INTO tb_exam_match_task "
                "(exam_id, scope, question_id, status, total_count) "
                "VALUES (%s, %s, %s, 'running', %s) RETURNING id",
                (
                    int(exam_id),
                    "question" if question_id is not None else "exam",
                    None if question_id is None else int(question_id),
                    len(questions),
                ),
            )
            if created is None:
                raise ValueError("创建核对任务失败")
            task_id = int(created[0])
            session.commit()
    except Exception:
        with _guard:
            _busy = False
        raise
    thread = threading.Thread(
        target=_run_check,
        args=(db_type, config, task_id, detail["exam"]["subject"], questions),
        daemon=True,
    )
    thread.start()
    return get_task(db_type, config, task_id) or {"id": task_id, "status": "running"}


def _candidates(db_type: str, config: dict[str, Any], subject: str) -> list[dict[str, Any]]:
    with WriteDbSession(db_type, config) as session:
        rows = _all(
            session,
            "SELECT id, knowledge_name FROM tb_knowledge WHERE subject = %s ORDER BY id LIMIT 400",
            (subject,),
        )
    return [{"id": int(row[0]), "knowledge_name": row[1] or ""} for row in rows]


def _system_prompt() -> str:
    return (
        "你是知识点匹配助手。请根据题目内容、题型和已关联知识点，判断关联是否准确，"
        "并给出可操作的增删建议。\n"
        "输出必须是 JSON 对象，不要有任何其他文字：\n"
        '{"ok": true, "add": [{"name": "知识点名称", "confidence": 0.95, "reason": "简短理由"}], '
        '"remove": [{"name": "知识点名称", "reason": "简短理由"}]}\n'
        "规则：add 中的知识点必须从候选知识点清单中选取，名称完全一致；"
        "remove 中的知识点必须是当前关联知识点之一，名称完全一致；"
        "当前关联为空但题干明显对应某个候选知识点时 ok=false 并给出 add；"
        "当前关联已正确覆盖题意时 ok=true，add 和 remove 留空。"
    )


def _user_prompt(question: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    current = question.get("knowledge_links") or []
    current_text = "\n".join(
        f"- {item.get('knowledge_name') or item.get('knowledge_id')}" for item in current
    ) or "（无）"
    names = "\n".join(f"- {item['knowledge_name']}" for item in candidates if item.get("knowledge_name"))
    return (
        f"题目：{question.get('content') or '（无题干）'}\n"
        f"题号：{question.get('question_no') or '-'}\n"
        f"题型：{question.get('question_type') or '未指定'}\n\n"
        f"当前已关联知识点：\n{current_text}\n\n"
        f"本学科候选知识点清单：\n{names}\n\n"
        "请按系统要求输出 JSON。"
    )


def _ask_model(question: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    from src.llm.service import create_llm

    llm = create_llm()
    last_error = "大模型调用失败"
    for _attempt in range(3):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": _user_prompt(question, candidates)},
                ]
            )
        except Exception as exc:
            last_error = str(exc)
            continue
        text = raw if isinstance(raw, str) else str(raw)
        try:
            return parse_match_reply(text, candidates, question.get("knowledge_links") or [])
        except (json.JSONDecodeError, ValueError, TypeError):
            last_error = f"大模型返回解析失败：{text[:180]}"
    raise ValueError(last_error)


def _insert_item(
    db_type: str,
    config: dict[str, Any],
    task_id: int,
    question: dict[str, Any],
    verdict: str,
    add: list,
    remove: list,
    done: int,
    failed: int,
    total: int,
) -> None:
    current = [
        {
            "knowledge_id": item.get("knowledge_id"),
            "knowledge_name": item.get("knowledge_name") or "",
            "weight": item.get("weight"),
        }
        for item in question.get("knowledge_links") or []
    ]
    with WriteDbSession(db_type, config) as session:
        _must_write(
            session,
            "INSERT INTO tb_exam_match_item "
            "(task_id, question_id, question_no, content, current_links, add_suggestions, "
            "remove_suggestions, verdict) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                task_id,
                int(question["id"]),
                str(question.get("question_no") or ""),
                question.get("content") or "",
                json.dumps(current, ensure_ascii=False),
                json.dumps(add, ensure_ascii=False),
                json.dumps(remove, ensure_ascii=False),
                verdict,
            ),
        )
        _must_write(
            session,
            "UPDATE tb_exam_match_task SET done_count = %s, failed_count = %s, total_count = %s "
            "WHERE id = %s",
            (done, failed, total, task_id),
        )
        session.commit()


def _finish(db_type: str, config: dict[str, Any], task_id: int, status: str, summary: str, error: str = "") -> None:
    with WriteDbSession(db_type, config) as session:
        _must_write(
            session,
            "UPDATE tb_exam_match_task SET status = %s, summary = %s, error = %s WHERE id = %s",
            (status, summary, error, task_id),
        )
        session.commit()


def _run_check(
    db_type: str,
    config: dict[str, Any],
    task_id: int,
    subject: str,
    questions: list[dict[str, Any]],
) -> None:
    global _busy
    try:
        candidates = _candidates(db_type, config, subject)
        ok_count = 0
        failed_count = 0
        total = len(questions)
        for index, question in enumerate(questions, start=1):
            if task_id in _cancelled:
                _finish(db_type, config, task_id, "cancelled", f"已取消：完成 {index - 1}/{total} 题")
                return
            if not candidates:
                verdict, add, remove = "needs_review", [], []
            else:
                try:
                    result = _ask_model(question, candidates)
                    verdict, add, remove = result["verdict"], result["add"], result["remove"]
                except Exception:
                    logger.exception("题目 %s 核对失败", question.get("id"))
                    verdict, add, remove = "failed", [], []
                    failed_count += 1
            if verdict == "ok":
                ok_count += 1
            _insert_item(
                db_type,
                config,
                task_id,
                question,
                verdict,
                add,
                remove,
                index,
                failed_count,
                total,
            )
        review_count = total - ok_count - failed_count
        summary = f"共 {total} 题：贴切 {ok_count} 题，待复核 {review_count} 题，失败 {failed_count} 题"
        _finish(db_type, config, task_id, "done", summary)
    except Exception as exc:
        logger.exception("知识点核对失败")
        try:
            _finish(db_type, config, task_id, "failed", "", str(exc))
        except Exception:
            logger.exception("回写核对失败状态失败")
    finally:
        _cancelled.discard(task_id)
        with _guard:
            _busy = False


def get_task(db_type: str, config: dict[str, Any], task_id: int) -> dict[str, Any] | None:
    with WriteDbSession(db_type, config) as session:
        _ensure_tables(session)
        session.commit()
        row = _one(
            session,
            "SELECT id, exam_id, scope, question_id, status, total_count, done_count, "
            "failed_count, summary, error FROM tb_exam_match_task WHERE id = %s",
            (int(task_id),),
        )
    return _task_dict(row) if row else None


def get_report(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
    task_id: int | None = None,
) -> dict[str, Any]:
    if get_exam(db_type, config, actor, exam_id) is None:
        raise ValueError("试卷不存在或无权查看")
    with WriteDbSession(db_type, config) as session:
        _ensure_tables(session)
        session.commit()
        if task_id is None:
            row = _one(
                session,
                "SELECT id, exam_id, scope, question_id, status, total_count, done_count, "
                "failed_count, summary, error FROM tb_exam_match_task "
                "WHERE exam_id = %s ORDER BY id DESC LIMIT 1",
                (int(exam_id),),
            )
        else:
            row = _one(
                session,
                "SELECT id, exam_id, scope, question_id, status, total_count, done_count, "
                "failed_count, summary, error FROM tb_exam_match_task "
                "WHERE id = %s AND exam_id = %s",
                (int(task_id), int(exam_id)),
            )
        if row is None:
            return {"task": None, "items": []}
        items = _all(
            session,
            "SELECT id, question_id, question_no, content, current_links, add_suggestions, "
            "remove_suggestions, verdict, adopted FROM tb_exam_match_item "
            "WHERE task_id = %s ORDER BY id",
            (int(row[0]),),
        )
    return {"task": _task_dict(row), "items": [_item_dict(item) for item in items]}


def cancel_check(task_id: int) -> None:
    _cancelled.add(int(task_id))


def apply_suggestions(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    task_id: int,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    report_task = get_task(db_type, config, task_id)
    if report_task is None:
        raise ValueError("核对报告不存在")
    if get_exam(db_type, config, actor, int(report_task["exam_id"])) is None:
        raise ValueError("试卷不存在或无权查看")
    success = 0
    errors: list[str] = []
    with WriteDbSession(db_type, config) as session:
        for operation in operations:
            item_id = int(operation.get("item_id") or 0)
            row = _one(
                session,
                "SELECT id, question_id, question_no, content, current_links, add_suggestions, "
                "remove_suggestions, verdict, adopted FROM tb_exam_match_item "
                "WHERE id = %s AND task_id = %s",
                (item_id, int(task_id)),
            )
            if row is None:
                errors.append(f"建议 {item_id} 不存在")
                continue
            item = _item_dict(row)
            add_ids = {int(value) for value in operation.get("add_ids") or []}
            remove_ids = {int(value) for value in operation.get("remove_ids") or []}
            if not add_ids and not remove_ids:
                continue
            question = _one(
                session,
                "SELECT id FROM tb_exam_question WHERE id = %s",
                (item["question_id"],),
            )
            if question is None:
                errors.append(f"题号 {item['question_no'] or item['question_id']} 已不存在")
                continue
            current_rows = _all(
                session,
                "SELECT knowledge_id, weight FROM tb_exam_question_knowledge WHERE question_id = %s",
                (item["question_id"],),
            )
            links = [
                {"knowledge_id": int(link[0]), "weight": float(link[1] or 0)}
                for link in current_rows
                if int(link[0]) not in remove_ids
            ]
            known = {link["knowledge_id"] for link in links}
            for suggestion in item["add_suggestions"]:
                knowledge_id = int(suggestion.get("knowledge_id") or 0)
                if knowledge_id not in add_ids or knowledge_id in known:
                    continue
                links.append(
                    {
                        "knowledge_id": knowledge_id,
                        "weight": float(suggestion.get("confidence") or 0.5),
                    }
                )
            try:
                prepared = normalize_links(links)
                _replace_links(session, item["question_id"], prepared)
                _must_write(
                    session,
                    "UPDATE tb_exam_match_item SET adopted = TRUE WHERE id = %s",
                    (item_id,),
                )
                session.commit()
            except ValueError as exc:
                errors.append(f"题号 {item['question_no']}：{exc}")
                continue
            success += 1
    return {"success": success, "errors": errors}
