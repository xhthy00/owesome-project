import pytest

from src.agent.education.paper_import import save_paper_outline

_QUESTION = {
    "question_no": "1",
    "question_type": "单选题",
    "question_score": 5,
    "content": "题干",
}


class _Session:
    def __init__(self, exams: list[tuple], *, index_exists: bool = False, db_type: str = "pg"):
        self.exams = exams
        self.index_exists = index_exists
        self.db_type = db_type
        self.writes: list[str] = []
        self.params: list[tuple | None] = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute_query(self, sql, params):
        self.params.append(params)
        if "pg_indexes" in sql:
            rows = [(1,)] if self.index_exists else []
        elif "pg_class" in sql:
            rows = []
        elif "GROUP BY" in sql:
            rows = []
        elif "tb_exam_batch" in sql:
            rows = [(1, "2026届高三1月期末", None)]
        elif "FROM tb_exam" in sql:
            rows = self.exams
        elif sql.startswith("INSERT INTO tb_exam"):
            rows = [(42,)]
        else:
            rows = []
        return True, "Success", {"rows": rows}

    def execute_write(self, sql, params):
        self.writes.append(sql)
        self.params.append(params)
        if sql.startswith("DELETE FROM tb_score_detail"):
            return True, "Success", {"row_count": 4}
        if sql.startswith("DELETE FROM tb_score "):
            return True, "Success", {"row_count": 2}
        if sql.startswith("UPDATE tb_score_overview"):
            return True, "Success", {"row_count": 6}
        return True, "Success", {"row_count": 1}

    def commit(self):
        self.committed = True


def _save(monkeypatch, session: _Session):
    monkeypatch.setattr(
        "datasource.db.db.WriteDbSession",
        lambda *_args, **_kwargs: session,
    )
    return save_paper_outline(
        "pg",
        {},
        exam_batch_id=1,
        subject="化学",
        exam_score=100,
        questions=[_QUESTION],
    )


def test_new_paper_does_not_touch_scores(monkeypatch):
    session = _Session([])
    saved = _save(monkeypatch, session)
    assert saved["updated"] is False
    assert saved["exam_id"] == 42
    assert saved["exam_name"] == "2026届高三1月期末"
    assert saved["cleared_score_count"] == 0
    assert saved["cleared_detail_count"] == 0
    assert not any(sql.startswith("DELETE FROM tb_score") for sql in session.writes)
    assert not any(sql.startswith("UPDATE tb_score_overview") for sql in session.writes)
    assert any("CREATE UNIQUE INDEX IF NOT EXISTS uk_exam_batch_subject" in sql for sql in session.writes)
    assert session.committed


def test_existing_paper_replaces_questions_and_clears_scores(monkeypatch):
    session = _Session([(9,)])
    saved = _save(monkeypatch, session)
    assert saved["updated"] is True
    assert saved["exam_id"] == 9
    assert saved["exam_name"] == "2026届高三1月期末"
    assert saved["cleared_detail_count"] == 4
    assert saved["cleared_score_count"] == 2
    assert "cleared_overview_count" not in saved
    assert not any(sql.startswith("UPDATE tb_score_overview") for sql in session.writes)
    detail_at = next(i for i, sql in enumerate(session.writes) if "tb_score_detail" in sql)
    question_at = next(i for i, sql in enumerate(session.writes) if "DELETE FROM tb_exam_question " in sql)
    assert detail_at < question_at
    assert session.committed


def test_duplicate_papers_in_one_batch_are_rejected(monkeypatch):
    session = _Session([(9,), (10,)])
    with pytest.raises(ValueError, match="多份化学试卷"):
        _save(monkeypatch, session)
    assert not any(sql.startswith("INSERT INTO tb_exam_question") for sql in session.writes)
    assert not any(sql.startswith("DELETE FROM tb_score") for sql in session.writes)
