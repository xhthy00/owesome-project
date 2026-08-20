# 教育模块试卷题目重做实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改造 `scripts/import_exam_scores.py`，将 `2026届高三1月期末` 9 科 mock 题目替换为按小题分粒度拆分、并从真实试卷 PDF 提取题干的题目数据。

**Architecture:** 拆分出两个纯函数模块：`question_parser.py`（xls 表头解析）和 `paper_parser.py`（PDF 文本解析）；`import_exam_scores.py` 改为调用这两个模块，新增 `--paper-dir` 参数控制是否从 PDF 填充题干。

**Tech Stack:** Python 3.11, pandas, xlrd, psycopg2, pymupdf (fitz), pytest

---

## 文件结构

| 文件 | 变更 | 职责 |
|---|---|---|
| `src/agent/education/question_parser.py` | 新建 | xls 表头解析为题目定义列表 |
| `src/agent/education/paper_parser.py` | 新建 | 从 PDF 按题号提取题干与子问文本 |
| `scripts/import_exam_scores.py` | 修改 | 主入口，调用解析模块，新增 `--paper-dir` |
| `tests/agent/education/test_question_parser.py` | 新建 | 题目解析器单元测试 |
| `tests/agent/education/test_paper_parser.py` | 新建 | 试卷解析器单元测试 |
| `pyproject.toml` | 可能修改 | 添加 `pymupdf` 依赖 |

---

## Task 1: 添加 pymupdf 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 检查当前依赖**

```bash
uv run python -c "import fitz; print(fitz.__doc__[:50])"
```

Expected: 如果已安装则输出版本信息；如果报错 `ModuleNotFoundError`，需要安装。

- [ ] **Step 2: 添加依赖**

在 `pyproject.toml` 的 `[project.dependencies]` 段添加：

```toml
"pymupdf>=1.24.0",
```

- [ ] **Step 3: 同步环境**

```bash
uv sync
```

Expected: 成功安装 pymupdf。

- [ ] **Step 4: 验证安装**

```bash
uv run python -c "import fitz; print('pymupdf ok')"
```

Expected: 输出 `pymupdf ok`。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add pymupdf for PDF parsing"
```

---

## Task 2: 实现 `src/agent/education/question_parser.py`

**Files:**
- Create: `src/agent/education/question_parser.py`
- Test: `tests/agent/education/test_question_parser.py`

- [ ] **Step 1: 把旧解析函数从 `import_exam_scores.py` 搬过来并做 TDD**

先写测试（TDD）：

```python
# tests/agent/education/test_question_parser.py
import pytest

from src.agent.education.question_parser import (
    parse_question_header,
    parse_questions_from_headers,
    question_type_from_label,
)


def test_parse_question_header_with_score():
    assert parse_question_header("单选1（5.0分）") == ("单选1", 5.0)
    assert parse_question_header("15_1（6.0分）") == ("15_1", 6.0)
    assert parse_question_header("15（13.0分）") == ("15", 13.0)


def test_parse_question_header_invalid():
    assert parse_question_header("单选1_答案（B）") is None
    assert parse_question_header(None) is None


def test_question_type_from_label():
    assert question_type_from_label("单选1") == "单选题"
    assert question_type_from_label("多选9") == "多选题"
    assert question_type_from_label("15_1") == "解答题"


def test_parse_questions_from_headers_with_main_and_subs():
    headers = [
        "单选1（5.0分）",
        "单选2（5.0分）",
        "多选9（6.0分）",
        "12（5.0分）",
        "15（13.0分）",
        "15_1（6.0分）",
        "15_2（7.0分）",
    ]
    questions = parse_questions_from_headers(headers)
    assert [q["question_no"] for q in questions] == [
        "单选1", "单选2", "多选9", "12", "15", "15_1", "15_2"
    ]
    assert [q["question_score"] for q in questions] == [5.0, 5.0, 6.0, 5.0, 13.0, 6.0, 7.0]
    main = next(q for q in questions if q["question_no"] == "15")
    assert main["is_sub"] is False
    sub = next(q for q in questions if q["question_no"] == "15_1")
    assert sub["is_sub"] is True
    assert sub["main_no"] == "15"


def test_parse_questions_skips_answer_columns():
    headers = [
        "单选1（5.0分）",
        "单选1_答案（B）",
        "15_1（6.0分）",
    ]
    questions = parse_questions_from_headers(headers)
    assert [q["question_no"] for q in questions] == ["单选1", "15_1"]


def test_parse_questions_score_sum_matches_main():
    headers = ["15（13.0分）", "15_1（6.0分）", "15_2（7.0分）"]
    questions = parse_questions_from_headers(headers)
    main = next(q for q in questions if q["question_no"] == "15")
    subs = [q for q in questions if q["is_sub"]]
    assert main["question_score"] == round(sum(s["question_score"] for s in subs), 2)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/agent/education/test_question_parser.py -v
```

Expected: 多个 FAIL（模块未定义）。

- [ ] **Step 3: 实现 `question_parser.py`**

```python
"""教育模块试卷题目解析器。

将小题分 Excel 文件的表头列名解析为题目定义列表，支持大题 + 子题（如 15、15_1、15_2）
共存。
"""

from __future__ import annotations

import re
from typing import Any


def parse_question_header(value: Any) -> tuple[str, float] | None:
    """解析题目列表头：'15_1（6.0分）' -> (label, score)。"""
    if value is None or not isinstance(value, str):
        return None
    m = re.match(r"^(.*?)[（(]([0-9.]+)分[）)]$", value.strip())
    if not m:
        return None
    return m.group(1), float(m.group(2))


def question_type_from_label(label: str) -> str:
    """根据列名推断题型。"""
    if "单选" in label:
        return "单选题"
    if "多选" in label:
        return "多选题"
    return "解答题"


def _main_no_of(label: str) -> str | None:
    """返回大题号；对子题 15_1 返回 15；对单选/多选 N 返回 None（单独处理）。"""
    # 单选/多选不拆父子
    if re.match(r"^(单选|多选)\d+", label):
        return None
    m = re.match(r"^(\d+)(_\d+)?$", label)
    return m.group(1) if m else None


def parse_questions_from_headers(headers: list[Any]) -> list[dict[str, Any]]:
    """将表头列表解析为题目定义。

    返回每项包含：
    - question_no: str   题号字符串（如 '单选1' / '15' / '15_1'）
    - question_score: float
    - question_type: str
    - is_sub: bool
    - main_no: str | None  父题号（大题本身为 None）
    """
    questions: list[dict[str, Any]] = []
    for raw in headers:
        parsed = parse_question_header(raw)
        if not parsed:
            continue
        label, score = parsed
        if "答案" in label:
            continue
        qtype = question_type_from_label(label)
        main_no = _main_no_of(label)
        is_sub = main_no is not None and "_" in label
        questions.append(
            {
                "question_no": label,
                "question_score": score,
                "question_type": qtype,
                "is_sub": is_sub,
                "main_no": main_no if is_sub else None,
            }
        )
    return questions
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/agent/education/test_question_parser.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/agent/education/question_parser.py tests/agent/education/test_question_parser.py
git commit -m "feat(edu): add question_parser with main/sub support"
```

---

## Task 3: 实现 `src/agent/education/paper_parser.py`

**Files:**
- Create: `src/agent/education/paper_parser.py`
- Test: `tests/agent/education/test_paper_parser.py`

- [ ] **Step 1: 写测试**

```python
# tests/agent/education/test_paper_parser.py
from src.agent.education.paper_parser import PaperParser


def test_parse_text_blocks():
    text = """
1．设集合 ...
2．已知复数 ...
15．（13分）已知函数 f(x)=... .
（1）求 f(x) 的单调区间；
（2）若 a=1，求 ...；
（3）证明 ... .
16．（15分）已知 ...
"""
    parser = PaperParser()
    parser.feed_text(text)
    assert "1" in parser.questions
    assert "15" in parser.questions
    q15 = parser.questions["15"]
    assert "已知函数 f(x)" in q15["full"]
    assert "（1）求 f(x) 的单调区间" in q15["sub_questions"]["1"]
    assert "（2）若 a=1" in q15["sub_questions"]["2"]


def test_build_content_main():
    text = "15．（13分）已知函数 ...\n（1）求 ...；\n（2）证明 ...。"
    parser = PaperParser()
    parser.feed_text(text)
    content = parser.build_content("15", sub_no=None)
    assert "15．（13分）" in content
    assert "（1）求" in content
    assert "（2）证明" in content


def test_build_content_sub():
    text = "15．（13分）已知函数 ...\n（1）求单调区间；\n（2）证明不等式。"
    parser = PaperParser()
    parser.feed_text(text)
    content = parser.build_content("15", sub_no="1")
    assert "15．（13分）" in content
    assert "（1）求单调区间" in content
    assert "（2）证明不等式" not in content


def test_build_content_missing_fallback():
    parser = PaperParser()
    parser.feed_text("1．题目一\n2．题目二")
    assert parser.build_content("99", sub_no=None) is None


def test_read_pdf_uses_fitz(tmp_path, monkeypatch):
    # 仅验证调用 fitz.open；真实 PDF 测试在集成阶段跑
    called = []

    class FakePage:
        def get_text(self):
            return "1．题目一"

    class FakeDoc:
        page_count = 1

        def __iter__(self):
            return iter([FakePage()])

        def close(self):
            pass

    def fake_open(path):
        called.append(path)
        return FakeDoc()

    monkeypatch.setattr("fitz.open", fake_open)

    from src.agent.education.paper_parser import read_pdf_text
    text = read_pdf_text(str(tmp_path / "fake.pdf"))
    assert "1．题目一" in text
    assert called
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/agent/education/test_paper_parser.py -v
```

Expected: 多个 FAIL。

- [ ] **Step 3: 实现 `paper_parser.py`**

```python
"""教育模块试卷 PDF 解析器。

从真实试卷 PDF 中提取题号 → 题干/子问文本，用于填充 tb_exam_question.content。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def read_pdf_text(path: str) -> str:
    """读取 PDF 全部文本并返回拼接字符串。"""
    import fitz  # pymupdf

    doc = fitz.open(path)
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text())
        return "\n".join(parts)
    finally:
        doc.close()


class PaperParser:
    """解析试卷文本，按题号索引题干与子问。"""

    def __init__(self) -> None:
        self.questions: dict[str, dict[str, Any]] = {}

    def feed_text(self, text: str) -> None:
        """输入试卷全文，解析出每道题。"""
        self.questions = self._parse_questions(text)

    def load_pdf(self, path: str) -> None:
        """从 PDF 文件加载并解析。"""
        self.feed_text(read_pdf_text(path))

    def _parse_questions(self, text: str) -> dict[str, dict[str, Any]]:
        """按题号边界切分文本。

        识别模式：
        - 题号开头："1．...", "15．..."
        - 子问："（1）...", "（2）...", "(1)...", "(2)..."
        """
        # 匹配题号行开头
        question_pattern = re.compile(r"(?m)^\s*(\d+)[\.．]\s*(.*?)$")
        sub_pattern = re.compile(r"[（(]([1-9][0-9]?)[）)]\s*(.*?)(?=[（(][1-9][0-9]?[）)]|$)", re.S)

        questions: dict[str, dict[str, Any]] = {}
        matches = list(question_pattern.finditer(text))

        for i, m in enumerate(matches):
            no = m.group(1)
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end]

            full_text = block.strip()
            # 去掉页眉页脚常见行
            lines = [ln for ln in full_text.splitlines() if not self._is_noise_line(ln)]
            cleaned_full = "\n".join(lines)

            # 提取子问
            sub_questions: dict[str, str] = {}
            # 子问可能跨行，把 cleaned_full 当整体匹配
            for sub_m in sub_pattern.finditer(cleaned_full):
                sub_no = sub_m.group(1)
                sub_text = sub_m.group(2).strip()
                if sub_text:
                    sub_questions[sub_no] = sub_text

            questions[no] = {
                "full": cleaned_full,
                "sub_questions": sub_questions,
            }

        return questions

    @staticmethod
    def _is_noise_line(line: str) -> bool:
        """过滤页眉页脚等噪声行。"""
        noise_patterns = [
            r"^\s*第\s*\d+\s*页",
            r"^\s*试卷第\d+页",
            r"^\s*高三[一-龥]+试卷",
            r"^\s*注意事项",
            r"^\s*\d+\s*\/\s*\d+\s*页",
        ]
        for p in noise_patterns:
            if re.search(p, line):
                return True
        return False

    def build_content(self, question_no: str, sub_no: str | None = None) -> str | None:
        """构造 content。

        - question_no: 大题题号，如 "15"
        - sub_no: 子问编号，如 "1" / "2" / "3"；None 表示大题本身

        返回：完整题干（+ 子问文本），未命中返回 None。
        """
        q = self.questions.get(question_no)
        if q is None:
            return None

        full = q["full"]
        if sub_no is None:
            return full

        sub_text = q["sub_questions"].get(sub_no)
        if sub_text is None:
            return full  #  fallback：至少返回完整题干

        # 子题 content = 完整题干 + 子问文本
        return f"{full}\n{sub_text}"


def build_question_content(
    parser: PaperParser | None,
    question_no: str,
    main_no: str | None,
) -> str:
    """根据 question_no 从解析器构建 content。"""
    if parser is None:
        return "暂无"

    # 子题：question_no 形如 15_1，子问编号 = 1
    if main_no is not None and "_" in question_no:
        sub_no = question_no.split("_", 1)[1]
        content = parser.build_content(main_no, sub_no=sub_no)
        return content if content is not None else "暂无"

    # 大题 / 单选 / 多选 / 填空
    content = parser.build_content(question_no, sub_no=None)
    return content if content is not None else "暂无"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/agent/education/test_paper_parser.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/agent/education/paper_parser.py tests/agent/education/test_paper_parser.py
git commit -m "feat(edu): add paper_parser for PDF question extraction"
```

---

## Task 4: 改造 `scripts/import_exam_scores.py`

**Files:**
- Modify: `scripts/import_exam_scores.py`

- [ ] **Step 1: 替换 import 并删除旧解析函数**

在 `scripts/import_exam_scores.py` 顶部添加：

```python
from src.agent.education.question_parser import (
    parse_question_header,
    parse_questions_from_headers,
    question_type_from_label,
)
from src.agent.education.paper_parser import (
    PaperParser,
    build_question_content,
)
```

删除文件内原有 `parse_question_header()`、`question_type_from_label()` 函数定义。

- [ ] **Step 2: 修改 `read_detail_file()` 返回结构**

把 `read_detail_file()` 改为使用新 parser：

```python
def read_detail_file(path: str) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """读取一个小题分文件。"""
    df_raw = pd.read_excel(path, sheet_name=0, header=None)
    hdr = df_raw.iloc[2].tolist()
    data = df_raw.iloc[3:].copy()
    data.columns = range(data.shape[1])

    questions = parse_questions_from_headers(hdr)
    # 保留 col_idx：题目定义对应 xls 中哪一列
    for i, cell in enumerate(hdr):
        parsed = parse_question_header(cell)
        if not parsed:
            continue
        label, _ = parsed
        if "答案" in label:
            continue
        q = next((q for q in questions if q["question_no"] == label), None)
        if q is not None:
            q.setdefault("col_idx", i)

    return questions, data
```

- [ ] **Step 3: 删除 `build_questions()` 函数**

新逻辑直接返回 `questions`，无需旧聚合。将 `build_exams_and_questions()` 中的 `questions = build_questions(questions_raw)` 改为 `questions = questions_raw`。

- [ ] **Step 4: 新增 `--paper-dir` 参数**

在 `argparse` 中添加：

```python
parser.add_argument(
    "--paper-dir",
    default="",
    help="真实试卷 PDF 目录，按 '科目.pdf' 命名；传入则解析题干写入 content",
)
```

- [ ] **Step 5: 在 `build_exams_and_questions()` 中接入 PDF 解析**

修改 `build_exams_and_questions()` 签名：

```python
def build_exams_and_questions(
    detail_dir: str,
    exam_name_prefix: str,
    exam_time: str,
    paper_dir: str = "",
) -> list[dict[str, Any]]:
```

内部逻辑：

```python
paper_parsers: dict[str, PaperParser] = {}
if paper_dir:
    for f in sorted(glob.glob(os.path.join(paper_dir, "*.pdf"))):
        subject_name = Path(f).stem  # "数学"
        parser = PaperParser()
        parser.load_pdf(f)
        paper_parsers[subject_name] = parser
        print(f"  [PDF] 已加载 {subject_name}: {f}")

# ... 处理每科小题分 ...
for f in sorted(files):
    subject = Path(f).name.split("(")[1].split(")")[0]
    ...
    questions = parse_questions_from_headers(hdr)  # 或 read_detail_file 返回
    # 填充 content
    parser = paper_parsers.get(subject)
    for q in questions:
        q["content"] = build_question_content(parser, q["question_no"], q.get("main_no"))
    ...
```

- [ ] **Step 6: 更新数据库写入列**

`upsert_exams()` 中 INSERT/UPDATE `tb_exam_question` 时，`content` 使用 `q["content"]` 而不是硬编码 `"暂无"`。

- [ ] **Step 7: 更新 `build_score_rows()` 小题分求和逻辑**

旧逻辑：一道大题的 score = 多个 `col_idx` 列求和。  
新逻辑：每道子题对应一列（`col_idx` 为单个 int），直接取该列值。

修改 `build_score_rows()` 中的循环：

```python
for q in questions:
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
            "question_score": q["question_score"],
            "class": class_name,
            "sfzh": sfzh,
            "xx": school_name,
            "subject_name": subject,
        }
    )
```

- [ ] **Step 8: 修改 DELETE 旧数据逻辑**

在 `main()` 写入前、构建好 `exams` 后，先删除旧题目和旧小题分明细：

```python
def delete_existing_exam_data(conn, exam_ids: list[int]) -> None:
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM tb_score_detail WHERE exam_id = ANY(%s)",
        (exam_ids,),
    )
    cur.execute(
        "DELETE FROM tb_exam_question WHERE exam_id = ANY(%s)",
        (exam_ids,),
    )
    conn.commit()
```

在 `main()` 中：

```python
with psycopg2.connect(args.database_url) as conn:
    # 1. 删除旧题目/旧小题分明细
    if exam_ids := [e["exam_id"] for e in exams if e.get("exam_id")]:
        delete_existing_exam_data(conn, exam_ids)
    # 2. 写入新数据
    subject_to_id = upsert_exams(conn, exams)
    stats = upsert_scores_and_details(conn, subject_to_id, score_rows, detail_rows)
```

注意：`exam_id` 需要先从数据库查出来。可在 `build_exams_and_questions()` 阶段通过 `SELECT id FROM tb_exam WHERE exam_name = %s` 预查。

- [ ] **Step 9: 跑 lint**

```bash
uv run ruff check scripts/import_exam_scores.py
```

Expected: 无错误。

- [ ] **Step 10: Commit**

```bash
git add scripts/import_exam_scores.py
git commit -m "feat(scripts): rewrite import_exam_scores to support fine-grained questions and PDF content"
```

---

## Task 5: 端到端 dry-run 验证

**Files:**
- Run: `scripts/import_exam_scores.py --dry-run`

- [ ] **Step 1: 准备命令**

```bash
uv run python scripts/import_exam_scores.py \
  --score-file "temp/教科院/考试/2026届-高三/2026届高三1月期末/成绩宽表.xlsx" \
  --detail-dir "temp/教科院/考试/2026届-高三/2026届高三1月期末/小题分" \
  --paper-dir "temp/教科院/考试/2026届-高三/2026届高三1月期末/试卷" \
  --exam-name "2026届高三1月期末" \
  --exam-time 2026-01-23 \
  --database-url "postgresql://root:123456@36.213.182.180:5435/edu" \
  --dry-run
```

- [ ] **Step 2: 检查 dry-run 输出**

Expected:
- 9 科试卷信息正常打印
- 每科题目数量合理（大题 + 子题）
- PDF 加载信息正常
- 不执行数据库写入

- [ ] **Step 3: 检查题目拆分统计**

重点确认：
- 数学 `15` 大题 + `15_1` / `15_2` 子题都出现
- 每道题的 content 预览不为空

- [ ] **Step 4: Commit（如有小修）**

```bash
git commit -m "fix(scripts): dry-run fixes for import_exam_scores"
```

---

## Task 6: 真实执行并校验

**Files:**
- Run: `scripts/import_exam_scores.py`
- Verify: 数据库查询

- [ ] **Step 1: 执行写入**

去掉 `--dry-run`，执行 Task 5 的命令。

- [ ] **Step 2: 数据库校验题目数量**

```python
import psycopg2
conn = psycopg2.connect('postgresql://root:123456@36.213.182.180:5435/edu')
cur = conn.cursor()
cur.execute("""
SELECT e.subject, COUNT(*) as q_count
FROM tb_exam_question q
JOIN tb_exam e ON q.exam_id = e.id
WHERE e.exam_batch_id = (SELECT id FROM tb_exam_batch WHERE batch_name = '2026届高三1月期末')
GROUP BY e.subject
ORDER BY e.subject
""")
for row in cur.fetchall():
    print(row)
conn.close()
```

Expected: 9 科每科题目数量 > 0，且数学/物理等有大题的科目题目数明显增多（含子题）。

- [ ] **Step 3: 校验 content 已写入**

```python
import psycopg2
conn = psycopg2.connect('postgresql://root:123456@36.213.182.180:5435/edu')
cur = conn.cursor()
cur.execute("""
SELECT q.question_no, q.content
FROM tb_exam_question q
JOIN tb_exam e ON q.exam_id = e.id
WHERE e.subject = '数学'
  AND e.exam_batch_id = (SELECT id FROM tb_exam_batch WHERE batch_name = '2026届高三1月期末')
  AND q.question_no IN ('15', '15_1', '15_2', '单选1')
ORDER BY q.question_no
""")
for row in cur.fetchall():
    print(row[0], '->', row[1][:80], '...')
conn.close()
```

Expected:
- `单选1` content 包含第 1 题题干
- `15` content 包含完整第 15 题
- `15_1` content 包含第 15 题题干 + `（1）` 小问
- `15_2` content 包含第 15 题题干 + `（2）` 小问

- [ ] **Step 4: 校验 tb_score_detail 子题粒度**

```python
import psycopg2
conn = psycopg2.connect('postgresql://root:123456@36.213.182.180:5435/edu')
cur = conn.cursor()
cur.execute("""
SELECT COUNT(*) FROM tb_score_detail sd
JOIN tb_exam e ON sd.exam_id = e.id
WHERE e.subject = '数学'
  AND e.exam_batch_id = (SELECT id FROM tb_exam_batch WHERE batch_name = '2026届高三1月期末')
  AND sd.question_no LIKE '15%'
""")
print('15_ detail count:', cur.fetchone()[0])
cur.execute("""
SELECT DISTINCT sd.question_no
FROM tb_score_detail sd
JOIN tb_exam e ON sd.exam_id = e.id
WHERE e.subject = '数学'
  AND e.exam_batch_id = (SELECT id FROM tb_exam_batch WHERE batch_name = '2026届高三1月期末')
  AND sd.question_no LIKE '15%'
ORDER BY sd.question_no
""")
print('15_ question_nos:', [r[0] for r in cur.fetchall()])
conn.close()
```

Expected: 包含 `15`、`15_1`、`15_2`。

- [ ] **Step 5: Commit（如执行过程中有调整）**

```bash
git commit -m "fix(scripts): real-run adjustments for 1月期末 question rewrite"
```

---

## Task 7: 全量测试

**Files:**
- Run: `uv run pytest`

- [ ] **Step 1: 跑全部测试**

```bash
uv run pytest tests/agent/education/test_question_parser.py tests/agent/education/test_paper_parser.py -v
```

Expected: 全部 PASS。

- [ ] **Step 2: 跑 lint**

```bash
uv run ruff check .
```

Expected: 无新增错误。

- [ ] **Step 3: 最终 commit**

```bash
git commit -m "test(edu): add tests for question_parser and paper_parser"
```

---

## Self-Review Checklist

### Spec coverage

| Spec 要求 | 对应任务 |
|---|---|
| 大题保留、子题独立成行 | Task 2 `question_parser.py` |
| question_no 直接用原列名（不转下划线） | Task 2 |
| content = 完整题干 / 完整题干 + 小问 | Task 3 `paper_parser.py` |
| 从 PDF 提取题干 | Task 3 |
| `tb_score_detail` 按子题拆分 | Task 4 Step 7 |
| 旧题目/旧明细删除重建 | Task 4 Step 8 |
| `--paper-dir` 参数 | Task 4 Step 4 |
| 后续批次复用 | Task 4 整体设计 |
| 测试覆盖 | Task 2 / Task 3 / Task 7 |

### Placeholder scan

- 无 TBD/TODO
- 无 "Add appropriate error handling" 等模糊描述
- 每个步骤含具体代码或命令

### Type consistency

- `question_no` 始终为 `str`
- `main_no` 在 `question_parser` 中为 `str | None`
- `sub_no` 在 `paper_parser` 中为 `str | None`
- `col_idx` 在题目定义中为单个 `int`

### 遗漏点

- 需确认 `tb_exam` 上 `exam_batch_id` 与 `tb_exam_batch` 关联方式（已在 spec 确认存在）
- 需验证 9 科 PDF 版式差异（语文/英语/政治文本密度大，需实际跑）

---

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-08-19-edu-exam-question-rewrite-plan.md`.

Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
