"""根据题目内容自动匹配知识点并写入 tb_exam_question_knowledge。

用途
----
读取 tb_exam_question 中尚未关联知识点的题目，按科目加载 tb_knowledge 候选
知识点，调用 LLM 做语义判断，将最匹配的知识点写入 tb_exam_question_knowledge。
暂时每道题只关联一个知识点；无法匹配的题目跳过。

调用示例
--------
    uv run python scripts/match_question_knowledge.py
    uv run python scripts/match_question_knowledge.py --subject 数学
    uv run python scripts/match_question_knowledge.py --exam-id 42 --limit 10
    uv run python scripts/match_question_knowledge.py --database-url "postgresql://root:123456@36.213.182.180:5435/edu" --limit 10

参数说明
--------
    --database-url  目标库连接串；默认从环境变量 DATABASE_URL 读取
    --subject       只处理指定科目的题目
    --exam-id       只处理指定考试的题目
    --limit         本次处理的最大题目数（用于小批量验证）
    --sleep-ms      每题 LLM 调用间隔毫秒数，默认 200
    --dry-run       只输出将要执行的操作，不写入数据库
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import create_engine, text
from sqlmodel import Session

# 将项目根目录加入 Python 路径，使 src.* 可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.util.json_parser import parse_json_tolerant  # noqa: E402
from src.common.core.config import get_settings  # noqa: E402
from src.llm.service import create_llm  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一位资深学科教研员。请根据题目信息，从候选知识点列表中选出最相关的一个。

严格要求：
1. 必须且只能从候选列表中选择知识点，不要自行编造。
2. 输出时**只能填写候选列表中的"叶子名"**（即不带 ">" 分隔前缀的纯名称），保持与候选列表逐字一致。
3. 若题干过于模糊、信息不足，或与所有候选知识点均不相关，请返回 {"unmatched": true}。
4. 只输出 JSON，不要解释。输出格式必须是 {"knowledge_name": "叶子名"} 或 {"unmatched": true}。"""

USER_PROMPT_TEMPLATE = """科目：{subject}
题型：{question_type}
题干：{content}

候选知识点列表（每行一条，仅叶子名）：
{knowledge_list}

请输出 JSON。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class QuestionRow:
    id: int
    exam_id: int
    question_no: str
    subject: str | None
    question_type: str | None
    content: str


@dataclass
class KnowledgeRow:
    id: int
    name: str
    module: str | None
    chapter: str | None


# ---------------------------------------------------------------------------
# 数据库查询
# ---------------------------------------------------------------------------
def fetch_unmatched_questions(
    session,
    subject: str | None = None,
    exam_id: int | None = None,
    limit: int | None = None,
) -> list[QuestionRow]:
    """查询尚未关联知识点的题目，科目从 tb_exam 关联获取。"""
    sql = """
        SELECT q.id, q.exam_id, q.question_no, e.subject, q.question_type, q.content
        FROM tb_exam_question q
        JOIN tb_exam e ON q.exam_id = e.id
        LEFT JOIN tb_exam_question_knowledge qk ON q.id = qk.question_id
        WHERE qk.question_id IS NULL
          AND q.content IS NOT NULL
          AND TRIM(q.content) <> ''
          AND TRIM(q.content) <> '暂无'
    """
    params: dict[str, Any] = {}
    if subject:
        sql += " AND e.subject = :subject"
        params["subject"] = subject
    if exam_id is not None:
        sql += " AND q.exam_id = :exam_id"
        params["exam_id"] = exam_id
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    rows = session.execute(text(sql), params).mappings().all()
    return [
        QuestionRow(
            id=r["id"],
            exam_id=r["exam_id"],
            question_no=r["question_no"] or "",
            subject=r["subject"],
            question_type=r["question_type"],
            content=str(r["content"]).strip(),
        )
        for r in rows
    ]


def fetch_knowledge_by_subjects(session, subjects: set[str]) -> dict[str, list[KnowledgeRow]]:
    """按科目批量加载知识点。"""
    if not subjects:
        return {}

    rows = (
        session.execute(
            text("""
                SELECT id, subject, module, chapter, knowledge_name
                FROM tb_knowledge
                WHERE subject = ANY(:subjects)
            """),
            {"subjects": list(subjects)},
        )
        .mappings()
        .all()
    )

    result: dict[str, list[KnowledgeRow]] = {}
    for r in rows:
        subject = r["subject"] or ""
        result.setdefault(subject, []).append(
            KnowledgeRow(
                id=r["id"],
                name=r["knowledge_name"] or "",
                module=r["module"],
                chapter=r["chapter"],
            )
        )
    return result


# ---------------------------------------------------------------------------
# LLM 匹配
# ---------------------------------------------------------------------------
def build_knowledge_list(knowledge_rows: list[KnowledgeRow]) -> str:
    """构造候选知识点文本：仅叶子名（去层级前缀）。"""
    lines = []
    for k in knowledge_rows:
        # LLM 只需看叶子名，避免它把层级前缀塞进输出
        lines.append(f"- {k.name}")
    return "\n".join(lines)


def match_question(
    llm,
    question: QuestionRow,
    knowledge_rows: list[KnowledgeRow],
) -> int | None:
    """调用 LLM 匹配一道题，返回 knowledge_id；无法匹配返回 None。"""
    knowledge_list = build_knowledge_list(knowledge_rows)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        subject=question.subject or "",
        question_type=question.question_type or "",
        content=question.content,
        knowledge_list=knowledge_list,
    )

    response = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    )

    parsed = parse_json_tolerant(response)
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM 返回非 JSON 对象: {response[:200]!r}")

    if parsed.get("unmatched"):
        return None

    name = parsed.get("knowledge_name")
    if not name:
        raise ValueError(f"LLM 返回缺少 knowledge_name: {response[:200]!r}")

    name = str(name).strip()
    # 兼容 LLM 把层级前缀一起返回的情况：取最后一段叶子名
    leaf = name.split(">")[-1].strip()

    for k in knowledge_rows:
        if k.name == leaf:
            return k.id

    raise ValueError(f"LLM 返回的知识点 '{name}' 不在候选列表中")


# ---------------------------------------------------------------------------
# 结果写入
# ---------------------------------------------------------------------------
def insert_matches(session, matches: list[tuple[int, int]]) -> int:
    """批量插入题目-知识点关联。"""
    if not matches:
        return 0

    # 去重并按 (question_id, knowledge_id) 插入
    seen: set[tuple[int, int]] = set()
    unique_matches = []
    for qid, kid in matches:
        key = (qid, kid)
        if key in seen:
            continue
        seen.add(key)
        unique_matches.append(key)

    session.execute(
        text("""
            INSERT INTO tb_exam_question_knowledge (question_id, knowledge_id, weight)
            VALUES (:question_id, :knowledge_id, 1)
            ON CONFLICT (question_id, knowledge_id) DO NOTHING
        """),
        [
            {"question_id": qid, "knowledge_id": kid}
            for qid, kid in unique_matches
        ],
    )
    session.commit()
    return len(unique_matches)


# ---------------------------------------------------------------------------
# 日志记录
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """配置日志：控制台 + 文件。"""
    logger = logging.getLogger("match_question_knowledge")
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        f"match_question_knowledge_{timestamp}.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def log_unmatched(
    logger: logging.Logger,
    question: QuestionRow,
    reason: str,
) -> None:
    """记录无法匹配或失败的题目。"""
    logger.info(
        "[未匹配/失败] question_id=%s exam_id=%s question_no=%s subject=%s reason=%s content=%s",
        question.id,
        question.exam_id,
        question.question_no,
        question.subject,
        reason,
        json.dumps(question.content, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# 数据库会话
# ---------------------------------------------------------------------------
@contextmanager
def _make_session(database_url: str) -> Generator[Session, None, None]:
    """根据运行时传入的数据库地址创建会话。"""
    engine = create_engine(database_url, pool_pre_ping=True)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="自动匹配题目与知识点")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="目标库连接串；默认从环境变量 DATABASE_URL 读取",
    )
    parser.add_argument("--subject", help="只处理指定科目的题目")
    parser.add_argument("--exam-id", type=int, help="只处理指定考试的题目")
    parser.add_argument("--limit", type=int, help="本次处理的最大题目数")
    parser.add_argument("--sleep-ms", type=int, default=200, help="每题 LLM 调用间隔毫秒数，默认 200")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    args = parser.parse_args()

    logger = setup_logging()

    if not args.database_url:
        logger.error("错误：未提供 --database-url，也未设置环境变量 DATABASE_URL")
        return 1

    # 验证配置
    settings = get_settings()
    if not settings.llm_model:
        logger.error("错误：未配置 LLM_MODEL")
        return 1

    llm = create_llm()
    sleep_seconds = args.sleep_ms / 1000.0

    with _make_session(args.database_url) as session:
        questions = fetch_unmatched_questions(
            session,
            subject=args.subject,
            exam_id=args.exam_id,
            limit=args.limit,
        )
        logger.info("待匹配题目: %d", len(questions))

        if not questions:
            logger.info("没有需要匹配的题目，退出")
            return 0

        subjects = {q.subject for q in questions if q.subject}
        knowledge_map = fetch_knowledge_by_subjects(session, subjects)

        # 统计没有知识点的科目
        for q in questions:
            if q.subject and q.subject not in knowledge_map:
                logger.warning("科目 '%s' 在 tb_knowledge 中没有知识点", q.subject)

        matched_count = 0
        unmatched_count = 0
        failed_count = 0
        matches: list[tuple[int, int]] = []

        for idx, question in enumerate(questions, 1):
            knowledge_rows = knowledge_map.get(question.subject or "", [])
            if not knowledge_rows:
                log_unmatched(logger, question, "该科目无候选知识点")
                unmatched_count += 1
                continue

            if args.dry_run:
                logger.info(
                    "[DRY RUN] 将匹配 question_id=%s subject=%s candidates=%d",
                    question.id,
                    question.subject,
                    len(knowledge_rows),
                )
                continue

            try:
                knowledge_id = match_question(llm, question, knowledge_rows)
                if knowledge_id is None:
                    log_unmatched(logger, question, "LLM 判断无法匹配")
                    unmatched_count += 1
                else:
                    matches.append((question.id, knowledge_id))
                    matched_count += 1
                    logger.info(
                        "[匹配成功] %d/%d question_id=%s -> knowledge_id=%s",
                        idx,
                        len(questions),
                        question.id,
                        knowledge_id,
                    )
            except Exception as exc:  # noqa: BLE001
                log_unmatched(logger, question, f"匹配失败: {exc}")
                failed_count += 1

            if idx < len(questions):
                time.sleep(sleep_seconds)

        if not args.dry_run:
            inserted = insert_matches(session, matches)
            logger.info("数据库实际写入: %d 条", inserted)

    logger.info("已匹配: %d", matched_count)
    logger.info("无法匹配: %d", unmatched_count)
    logger.info("失败: %d", failed_count)

    return 0


if __name__ == "__main__":
    sys.exit(main())
