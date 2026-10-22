"""知识点核对。核对结果写在 edu 业务库，模型使用平台已配置的大模型。"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "attach_match_context",
    "fit_match_removals",
    "limit_suggestions",
    "links_after_adopt",
    "match_summary",
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
    add = limit_suggestions(add)
    remove = fit_match_removals(current, add, limit_suggestions(remove))
    verdict = "ok" if ok or not (add or remove) else "needs_review"
    return {"verdict": verdict, "add": add, "remove": remove}


def links_after_adopt(
    current_ids: list[int],
    remove_ids: set[int],
    add_ids: list[int],
    limit: int = 3,
) -> list[int]:
    """合并仍保留的当前关联和勾选的新建议，结果不超过 limit 个。

    一次勾选满 limit 条建议时，这些建议就是本题的关联，原来多出来的关联让位。
    """
    removed = {int(item) for item in remove_ids}
    ordered_add: list[int] = []
    for raw in add_ids:
        knowledge_id = int(raw)
        if knowledge_id not in ordered_add:
            ordered_add.append(knowledge_id)
    kept = [int(item) for item in current_ids if int(item) not in removed]
    fresh = [item for item in ordered_add if item not in set(kept)]
    if len(ordered_add) >= limit and len(kept) + len(fresh) > limit:
        chosen = set(ordered_add)
        kept = [item for item in kept if item in chosen]
        fresh = [item for item in ordered_add if item not in set(kept)]
    if len(fresh) > limit or len(kept) + len(fresh) > limit:
        raise ValueError(
            f"当前保留 {len(kept)} 个，再采纳 {len(fresh)} 个会超过 {limit} 个。请先去掉不需要的当前关联"
        )
    return kept + fresh


def fit_match_removals(
    current: list[dict[str, Any]],
    add: list[dict[str, Any]],
    remove: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """建议新增后若会超过上限，把权重较低的当前关联补进移除建议。"""
    if not add:
        return remove
    add_ids = {int(item["knowledge_id"]) for item in add}
    remove_ids = {int(item["knowledge_id"]) for item in remove}
    kept: list[tuple[int, float, str]] = []
    for item in current:
        try:
            knowledge_id = int(item.get("knowledge_id"))
        except (TypeError, ValueError):
            continue
        if knowledge_id in add_ids or knowledge_id in remove_ids:
            continue
        try:
            weight = float(item.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        kept.append((knowledge_id, weight, str(item.get("knowledge_name") or "")))
    overflow = len(kept) + len(add) - limit
    if overflow <= 0:
        return remove
    extra = [
        {
            "knowledge_id": knowledge_id,
            "knowledge_name": name,
            "reason": "超出单题 3 个知识点上限",
        }
        for knowledge_id, _weight, name in sorted(kept, key=lambda row: (row[1], row[0]))[:overflow]
    ]
    return [*remove, *extra]


def limit_suggestions(items: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """只保留置信度最高的几条，避免一道题被贴上一长串泛化知识点。"""
    ranked = sorted(items, key=lambda item: float(item.get("confidence") or 0), reverse=True)
    return ranked[:limit]


def match_summary(
    total: int,
    ok_count: int,
    review_count: int,
    failed_count: int,
    unlinked_count: int,
) -> str:
    if total and unlinked_count == total:
        return (
            f"共 {total} 题目前都还没有关联知识点，"
            f"本次给出关联建议 {review_count} 题，核对失败 {failed_count} 题"
        )
    return (
        f"共 {total} 题：关联正确 {ok_count} 题，"
        f"建议调整 {review_count} 题，核对失败 {failed_count} 题"
    )


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
    attach_match_context(questions)
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
        "你是学科教研员。请从候选清单中为题目选出最贴合的知识点，最多 3 个。\n"
        "只输出 JSON，不要解释：\n"
        '{"ok": false, "add": [{"name": "知识点名称", "confidence": 0.9, "reason": "20字以内"}], '
        '"remove": []}\n'
        "规则：name 必须与候选清单逐字一致；"
        "还没有关联知识点时只填写 add，ok 必须为 false；"
        "已有关联且不需要增删时 ok 为 true，add 和 remove 都为空；"
        "当前关联加上 add、减去 remove 后不能超过 3 个，位置不够时把要让出的当前关联写入 remove；"
        "remove 只能填写当前已关联的名称。不要输出清单以外的名称。\n"
        "若题目含有「大题材料与前文」，必须按当前小题在这份材料里的设问来判断，"
        "不要只根据当前小题的短题干匹配知识点。"
    )


def question_main_no(question_no: str) -> str | None:
    """返回小题所属大题号。``16-2``、``15_1``、``14（1）②`` 属于大题 16、15、14。"""
    matched = re.match(r"^(\d+)(.*)$", str(question_no or "").strip())
    if not matched:
        return None
    rest = matched.group(2)
    if not rest or rest[0] not in "-_（(":
        return None
    return matched.group(1)


def _remember_context(chunks: list[str], text: str, current: str) -> None:
    text = text.strip()
    if not text or text == current or (current and current in text):
        return
    kept: list[str] = []
    for item in chunks:
        if text in item:
            return
        if item not in text:
            kept.append(item)
    kept.append(text)
    chunks[:] = kept


def attach_match_context(questions: list[dict[str, Any]]) -> None:
    """给小题补上同大题的前文。材料通常在第一小题，后面的小题题干很短。"""
    by_no = {str(item.get("question_no") or ""): item for item in questions}
    groups: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        main = question_main_no(str(question.get("question_no") or ""))
        if main is None:
            continue
        groups.setdefault(main, []).append(question)
    for main, members in groups.items():
        parent = by_no.get(main)
        if len(members) < 2 and parent is None:
            continue
        for index, question in enumerate(members):
            if index == 0 and parent is None:
                continue
            current = str(question.get("content") or "").strip()
            chunks: list[str] = []
            if parent is not None and parent is not question:
                _remember_context(chunks, str(parent.get("content") or ""), current)
            first = members[0]
            if first is not question:
                _remember_context(chunks, str(first.get("content") or ""), current)
            if index > 0:
                previous = members[index - 1]
                if previous is not first:
                    _remember_context(chunks, str(previous.get("content") or ""), current)
            if chunks:
                question["lead_in"] = "\n\n".join(chunks)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _user_prompt(question: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    current = question.get("knowledge_links") or []
    current_text = "\n".join(
        f"- {item.get('knowledge_name') or item.get('knowledge_id')}" for item in current
    ) or "（无）"
    names = "\n".join(f"- {item['knowledge_name']}" for item in candidates if item.get("knowledge_name"))
    content = str(question.get("content") or "").strip() or "（无题干）"
    lead = str(question.get("lead_in") or "").strip()
    if lead:
        body = _clip(content, 700)
        shown = f"大题材料与前文：\n{_clip(lead, max(200, 1500 - len(body)))}\n\n当前小题：\n{body}"
    else:
        shown = _clip(content, 1600)
    return (
        f"题目：{shown}\n"
        f"题号：{question.get('question_no') or '-'}\n"
        f"题型：{question.get('question_type') or '未指定'}\n\n"
        f"当前已关联知识点：\n{current_text}\n\n"
        f"本学科候选知识点清单：\n{names}\n\n"
        "请按系统要求输出 JSON。"
    )


def _match_llm():
    """核对只需要短 JSON。MiniMax-M3 默认会长时间思考，这里关掉并限制输出长度。"""
    from src.common.core.config import get_settings
    from src.llm.service import create_llm

    settings = get_settings()
    kwargs: dict[str, Any] = {"max_tokens": 512, "temperature": 0}
    model = (settings.llm_model or "").lower()
    if "minimax" in model or settings.llm_thinking:
        kwargs["thinking"] = "disabled"
    return create_llm(**kwargs)


def _ask_model(question: dict[str, Any], candidates: list[dict[str, Any]], llm: Any = None) -> dict[str, Any]:
    if llm is None:
        llm = _match_llm()
    last_error = "大模型调用失败"
    for _attempt in range(2):
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
        llm = _match_llm() if candidates else None
        total = len(questions)
        ok_count = 0
        failed_count = 0
        unlinked_count = 0
        done = 0
        progress = threading.Lock()

        def evaluate(question: dict[str, Any]):
            if task_id in _cancelled:
                return None
            if not candidates:
                return question, "needs_review", [], []
            try:
                result = _ask_model(question, candidates, llm)
            except Exception:
                logger.exception("题目 %s 核对失败", question.get("id"))
                return question, "failed", [], []
            return question, result["verdict"], result["add"], result["remove"]

        workers = 1 if not candidates else min(3, total)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(evaluate, question) for question in questions]
            for future in as_completed(futures):
                outcome = future.result()
                if outcome is None or task_id in _cancelled:
                    _finish(db_type, config, task_id, "cancelled", f"已取消：完成 {done}/{total} 题")
                    return
                question, verdict, add, remove = outcome
                with progress:
                    done += 1
                    if verdict == "failed":
                        failed_count += 1
                    if verdict == "ok":
                        ok_count += 1
                    if not (question.get("knowledge_links") or []):
                        unlinked_count += 1
                    current_done = done
                    current_failed = failed_count
                _insert_item(
                    db_type,
                    config,
                    task_id,
                    question,
                    verdict,
                    add,
                    remove,
                    current_done,
                    current_failed,
                    total,
                )
        review_count = total - ok_count - failed_count
        summary = match_summary(total, ok_count, review_count, failed_count, unlinked_count)
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
            current_weight = {int(link[0]): float(link[1] or 0) for link in current_rows}
            ordered_add = []
            suggestion_by_id = {}
            for suggestion in item["add_suggestions"]:
                knowledge_id = int(suggestion.get("knowledge_id") or 0)
                if knowledge_id not in add_ids or knowledge_id in suggestion_by_id:
                    continue
                suggestion_by_id[knowledge_id] = suggestion
                ordered_add.append(knowledge_id)
            try:
                chosen = links_after_adopt(list(current_weight), remove_ids, ordered_add)
            except ValueError as exc:
                errors.append(f"题号 {item['question_no']}：{exc}")
                continue
            given = operation.get("weights") or {}
            links = []
            for knowledge_id in chosen:
                suggestion = suggestion_by_id.get(knowledge_id)
                raw = given.get(str(knowledge_id), given.get(knowledge_id)) if suggestion else None
                if suggestion is not None and raw is not None:
                    try:
                        weight = float(raw)
                    except (TypeError, ValueError):
                        weight = float(suggestion.get("confidence") or 0.5)
                elif knowledge_id in current_weight and (suggestion is None or raw is None):
                    weight = current_weight[knowledge_id]
                else:
                    weight = float((suggestion or {}).get("confidence") or 0.5)
                links.append({"knowledge_id": knowledge_id, "weight": weight})
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
