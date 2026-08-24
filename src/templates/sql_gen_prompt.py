"""SQL generation prompt templates based on SQLBot."""

import json
from datetime import datetime
from typing import List, Optional


#: 教育学情场景内置 SQL few-shot 示例。
#:
#: 覆盖班级均分、年级排名、分数段分布、个体查询、历次对比等高频问法。
#: 及格/优秀比例**不要**写死在 SQL 里——由异常规则配置 + compute_score_stats_tool 计算。
#: 调用方可通过 ``data_training=education_sql_training_block()`` 注入。
def _pass_excellent_ratios() -> tuple[float, float]:
    try:
        from src.agent.education.config_store import get_config

        cfg = get_config()
        return float(cfg.pass_ratio), float(cfg.excellent_ratio)
    except Exception:
        return 0.6, 0.85


def education_sql_training_block() -> str:
    """返回教育学情 SQL few-shot 块（及格/优秀不在 SQL 内写死比例）。"""
    return """<sql-examples>
  <example>
    <question>南京市第一中学高一(1)班数学平均分和人数</question>
    <suggestion-answer>SELECT sch.name AS school,
       sc.class,
       sc.subject_name,
       sc.exam_score,
       COUNT(*) AS cnt,
       ROUND(AVG(sc.score), 2) AS avg_score
FROM tb_score sc
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sch.name = '南京市第一中学'
  AND sc.class = '高一(1)班'
  AND sc.subject_name = '数学'
GROUP BY sch.name, sc.class, sc.subject_name, sc.exam_score
LIMIT 1000;
-- 及格率/优秀率：查明细 score+exam_score 后用 compute_score_stats_tool，禁止在 SQL 写死 0.6/0.85</suggestion-answer>
  </example>
  <example>
    <question>对比三所学校数学均分排名</question>
    <suggestion-answer>SELECT sch.name AS school,
       ROUND(AVG(sc.score), 2) AS class_avg,
       RANK() OVER (ORDER BY AVG(sc.score) DESC) AS rank
FROM tb_score sc
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sc.subject_name = '数学'
GROUP BY sch.name
ORDER BY rank
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>南京市第一中学数学分数段分布</question>
    <suggestion-answer>SELECT
  CASE
    WHEN sc.score &lt; sc.exam_score * 0.6 THEN '0-60%'
    WHEN sc.score &lt; sc.exam_score * 0.7 THEN '60-70%'
    WHEN sc.score &lt; sc.exam_score * 0.8 THEN '70-80%'
    WHEN sc.score &lt; sc.exam_score * 0.9 THEN '80-90%'
    ELSE '90-100%'
  END AS segment,
  COUNT(*) AS cnt
FROM tb_score sc
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sch.name = '南京市第一中学' AND sc.subject_name = '数学'
GROUP BY segment
ORDER BY segment
LIMIT 1000;
-- 上表为相对满分的分数段分布（展示用）；及格/优秀线以异常规则配置为准，勿与 0.6/0.85 混淆</suggestion-answer>
  </example>
  <example>
    <question>STU20240001 本次数学成绩</question>
    <suggestion-answer>SELECT sc.student_id, sc.subject_name, sc.score, sc.exam_score
FROM tb_score sc
WHERE sc.student_id = 'STU20240001' AND sc.subject_name = '数学'
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>南京市第一中学数学每一小题得分率及关联知识点</question>
    <suggestion-answer>SELECT sd.question_no,
       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,
       eq.question_score AS full_score,
       ROUND(AVG(sd.score), 2) AS avg_score,
       ROUND(AVG(sd.score)::numeric / NULLIF(eq.question_score, 0) * 100, 2) AS score_rate
FROM tb_score_detail sd
JOIN tb_exam_question eq ON sd.question_id = eq.id
LEFT JOIN (
  SELECT eqk.question_id,
         string_agg(DISTINCT k.knowledge_name, '、' ORDER BY k.knowledge_name) AS knowledge_name
  FROM tb_exam_question_knowledge eqk
  JOIN tb_knowledge k ON k.id = eqk.knowledge_id
  GROUP BY eqk.question_id
) kn ON kn.question_id = eq.id
JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sch.name = '南京市第一中学' AND sc.subject_name = '数学'
GROUP BY sd.question_no, kn.knowledge_name, eq.question_score
ORDER BY sd.question_no
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>南京市第一中学数学知识点薄弱诊断</question>
    <suggestion-answer>SELECT COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,
       ROUND(SUM(sd.score * COALESCE(eqk.w_norm, 1))::numeric
             / NULLIF(SUM(eq.question_score * COALESCE(eqk.w_norm, 1)), 0) * 100, 2) AS score_rate,
       COUNT(DISTINCT sd.question_no) AS question_count
FROM tb_score_detail sd
JOIN tb_exam_question eq ON sd.question_id = eq.id
LEFT JOIN (
  SELECT question_id, knowledge_id,
         weight / NULLIF(SUM(weight) OVER (PARTITION BY question_id), 0) AS w_norm
  FROM tb_exam_question_knowledge
) eqk ON eqk.question_id = eq.id
LEFT JOIN tb_knowledge k ON k.id = eqk.knowledge_id
JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sch.name = '南京市第一中学' AND sc.subject_name = '数学'
GROUP BY k.knowledge_name
ORDER BY score_rate ASC
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>南京市各区县数学均分对比</question>
    <suggestion-answer>SELECT sch.district,
       COUNT(*) AS cnt,
       ROUND(AVG(sc.score), 2) AS avg_score
FROM tb_score sc
JOIN tb_school sch ON sc.school_id = sch.id
WHERE sc.subject_name = '数学'
GROUP BY sch.district
ORDER BY avg_score DESC
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>按年级汇总数学均分（年级从班级名解析）</question>
    <suggestion-answer>SELECT
  CASE
    WHEN sc.class LIKE '高一%' THEN '高一'
    WHEN sc.class LIKE '高二%' THEN '高二'
    WHEN sc.class LIKE '高三%' THEN '高三'
    WHEN sc.class LIKE '初一%' THEN '初一'
    WHEN sc.class LIKE '初二%' THEN '初二'
    WHEN sc.class LIKE '初三%' THEN '初三'
    ELSE '其他'
  END AS grade,
  ROUND(AVG(sc.score), 2) AS avg_score
FROM tb_score sc
WHERE sc.subject_name = '数学'
GROUP BY grade
ORDER BY grade
LIMIT 1000;</suggestion-answer>
  </example>
  <example>
    <question>邗江区 2026届高三5月模拟数学均分</question>
    <suggestion-answer>SELECT sch.district,
       ROUND(AVG(sc.score), 2) AS avg_score,
       COUNT(*) AS cnt
FROM tb_score sc
JOIN tb_school sch ON sc.school_id = sch.id
JOIN tb_exam e ON sc.exam_id = e.id
LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id
WHERE COALESCE(eb.batch_name, e.exam_name) LIKE '%2026届高三5月模拟%'
  AND sch.district = '邗江区'
  AND sc.subject_name = '数学'
GROUP BY sch.district
LIMIT 1000;
-- 「XX考试」按 tb_exam_batch.batch_name 过滤，禁止把试卷名当成考试批次</suggestion-answer>
  </example>
  <example>
    <question>2026届高三5月模拟物理类本科线是多少</question>
    <suggestion-answer>SELECT exam_name, wl_score_bk AS threshold
FROM tb_fraction_bar
WHERE exam_name = '2026届高三5月模拟'
LIMIT 10;
-- 分数线阈值在 tb_fraction_bar；达线人数/率须查 tb_score_indicator</suggestion-answer>
  </example>
  <example>
    <question>邗江区 2026届高三5月模拟全科总分均分</question>
    <suggestion-answer>SELECT dq,
       ROUND(AVG(zf6m), 2) AS avg_zf6m,
       COUNT(*) AS cnt
FROM tb_score_overview
WHERE exam_name = '2026届高三5月模拟'
  AND dq = '邗江区'
GROUP BY dq
LIMIT 1000;
-- 全科总分用 tb_score_overview.zf6m；学生标识 anon_stu_id；禁止 xm/sfzh/ksh</suggestion-answer>
  </example>
  <example>
    <question>2026届高三5月模拟理科语数外三门均分的学校排名</question>
    <suggestion-answer>SELECT xx AS school,
       COUNT(*) AS candidates,
       ROUND(AVG(zf3m), 1) AS avg_zf3m
FROM tb_score_overview
WHERE exam_name = '2026届高三5月模拟'
  AND xkkm LIKE '物%'
GROUP BY xx
ORDER BY avg_zf3m DESC
LIMIT 1000;
-- 语数外/三门均分=AVG(zf3m) 三科总分校均（约 350），禁止除以 3，禁止 tb_score 三科 AVG(score)
-- 理科=物理类 xkkm LIKE '物%'；学校用 xx（如 A01），不要用 tb_school.name 脱敏码</suggestion-answer>
  </example>
  <example>
    <question>邗江区物理类本科线达线人数和达线率</question>
    <suggestion-answer>SELECT district,
       track,
       line_name,
       SUM(candidates) AS candidates,
       SUM(reached_count) AS reached_count,
       ROUND(SUM(reached_count) * 100.0 / NULLIF(SUM(candidates), 0), 2) AS reach_rate
FROM tb_score_indicator
WHERE district LIKE '%邗江%'
  AND track = '物理类'
  AND line_name = '本科线'
GROUP BY district, track, line_name
LIMIT 1000;
-- 达线查 tb_score_indicator；区县/全市必须 SUM 后重算率，禁止 AVG(reach_rate)</suggestion-answer>
  </example>
  <example>
    <question>扬州市2026届高三3月广陵区本科线达线人数和达线率</question>
    <suggestion-answer>SELECT district,
       line_name,
       SUM(candidates) AS candidates,
       SUM(reached_count) AS reached_count,
       ROUND(SUM(reached_count) * 100.0 / NULLIF(SUM(candidates), 0), 2) AS reach_rate
FROM tb_score_indicator
WHERE exam_name LIKE '%2026届高三3月%'
  AND district LIKE '%广陵%'
  AND line_name = '本科线'
GROUP BY district, line_name
LIMIT 1000;
-- 禁止把「N月」拼进 district；考试用 LIKE 对齐 batch_name</suggestion-answer>
  </example>
  <example>
    <question>2026届高三3月扬州中学高三(1)班南大达线情况</question>
    <suggestion-answer>SELECT CASE
         WHEN ov.xkkm LIKE '物%' THEN '物理类'
         WHEN ov.xkkm LIKE '史%' OR ov.xkkm LIKE '历%' THEN '历史类'
         ELSE '其他'
       END AS 选科方向,
       COUNT(*) AS 参考人数,
       SUM(CASE
             WHEN ov.xkkm LIKE '物%' AND ov.zf6m &gt;= fb.wl_score_nd THEN 1
             WHEN (ov.xkkm LIKE '史%' OR ov.xkkm LIKE '历%') AND ov.zf6m &gt;= fb.ls_score_nd THEN 1
             ELSE 0
           END) AS 达线人数,
       ROUND(100.0 * SUM(CASE
             WHEN ov.xkkm LIKE '物%' AND ov.zf6m &gt;= fb.wl_score_nd THEN 1
             WHEN (ov.xkkm LIKE '史%' OR ov.xkkm LIKE '历%') AND ov.zf6m &gt;= fb.ls_score_nd THEN 1
             ELSE 0
           END) / NULLIF(COUNT(*), 0), 2) AS "达线率%",
       MAX(CASE
             WHEN ov.xkkm LIKE '物%' THEN fb.wl_score_nd
             WHEN ov.xkkm LIKE '史%' OR ov.xkkm LIKE '历%' THEN fb.ls_score_nd
           END) AS 分数线
FROM tb_score_overview ov
JOIN tb_fraction_bar fb ON fb.exam_name = ov.exam_name
WHERE ov.exam_name LIKE '%2026届高三3月%'
  AND ov.bj LIKE '%高三(1)%'
  AND (ov.xkkm LIKE '物%' OR ov.xkkm LIKE '史%' OR ov.xkkm LIKE '历%')
GROUP BY 1
LIMIT 1000;
-- 班级达线：必须用 zf6m（禁止 zf4m/zf3m）；物理对照 wl_score_*、历史对照 ls_score_*；
-- 按 xkkm 分轨 GROUP BY；班内只有物理类则结果只有一行，禁止文理混报；WHERE 必含 bj</suggestion-answer>
  </example>
  <example>
    <question>2026届高三1月期末各区特控线达线率对比</question>
    <suggestion-answer>SELECT district,
       line_name,
       SUM(candidates) AS candidates,
       SUM(reached_count) AS reached_count,
       ROUND(SUM(reached_count) * 100.0 / NULLIF(SUM(candidates), 0), 2) AS reach_rate
FROM tb_score_indicator
WHERE exam_name = '2026届高三1月期末'
  AND line_name = '特控线'
GROUP BY district, line_name
ORDER BY reach_rate DESC
LIMIT 1000;</suggestion-answer>
  </example>
</sql-examples>"""


def education_terminologies_block() -> str:
    """返回教育学情术语块；及格/优秀比例注入当前异常规则配置。"""
    pr, er = _pass_excellent_ratios()
    pp, ep = round(pr * 100, 2), round(er * 100, 2)
    try:
        from src.agent.education.privacy_mode import privacy_sql_instruction

        privacy_desc = privacy_sql_instruction()
    except Exception:
        privacy_desc = (
            "学校字段：展示与过滤只用 sch.name 或 sch.id / sc.school_id（脱敏码）；"
            "**禁止** SELECT/引用 tb_school.s_name。"
            "学生标识只用 student_id / anon_stu_id；禁止 SELECT xm/xh/sfzh/ksh。"
        )
    return f"""<terminologies>
  <terminology>
    <words><word>学校</word><word>机构</word><word>校区</word></words>
    <description>对应 tb_school.name，过滤用 JOIN tb_school sch ON sc.school_id = sch.id WHERE sch.name = '学校名'</description>
  </terminology>
  <terminology>
    <words><word>班级</word><word>班</word></words>
    <description>对应 tb_score.class 或 tb_student.class，如 '高一(1)班'</description>
  </terminology>
  <terminology>
    <words><word>学生</word><word>学号</word></words>
    <description>tb_student.id / tb_score.student_id，格式如 STU20240001；JOIN 必须写 sc.student_id = st.id，tb_student 无 student_id 列；无姓名字段时用学号展示</description>
  </terminology>
  <terminology>
    <words><word>小题</word><word>逐题</word><word>题目</word></words>
    <description>查 tb_score_detail JOIN tb_exam_question，按 question_no 排序</description>
  </terminology>
  <terminology>
    <words><word>知识点</word><word>考点</word><word>题型</word></words>
    <description>知识点名称取自 tb_knowledge.knowledge_name，必须经 tb_exam_question_knowledge（eqk）关联：LEFT JOIN tb_exam_question_knowledge eqk ON eqk.question_id = eq.id LEFT JOIN tb_knowledge k ON k.id = eqk.knowledge_id；一题可挂多个知识点。掌握度按 weight 题内归一化拆分（w_norm = weight/SUM(weight)）。严禁根据题目内容自行编造/猜测知识点名（如「立体几何」「解析几何」等数据库中不存在的名称）</description>
  </terminology>
  <terminology>
    <words><word>满分</word><word>卷面分</word></words>
    <description>每套卷子满分由 tb_exam.exam_score 或 tb_score.exam_score 记录，不同考试可能不同，禁止写死 100 或 150</description>
  </terminology>
  <terminology>
    <words><word>考试</word><word>考试批次</word><word>这场考试</word><word>期中</word><word>期末</word><word>模拟</word></words>
    <description>用户说的「XX考试」对应 tb_exam_batch.batch_name（如 2026届高三5月模拟），不是试卷名。必须 JOIN tb_exam e ON sc.exam_id = e.id LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id，WHERE COALESCE(eb.batch_name, e.exam_name) LIKE '%考试名%'。tb_exam 是批次下的单科试卷（subject/exam_score）；禁止只用 e.exam_name 当批次过滤</description>
  </terminology>
  <terminology>
    <words><word>及格</word><word>优秀</word></words>
    <description>当前系统配置：及格={pp}%（ratio {pr}）、优秀={ep}%（ratio {er}）。及格线=exam_score×{pr}，优秀线=exam_score×{er}。禁止写死 0.6/0.85。KPI 须用 compute_score_stats_tool（或报告工具）按配置计算，回复中的及格线/优秀线必须与工具结果一致</description>
  </terminology>
  <terminology>
    <words><word>得分率</word><word>难度</word></words>
    <description>得分率 = AVG(sd.score) / question_score * 100；难度可近似为 1 - 得分率</description>
  </terminology>
  <terminology>
    <words><word>语数外</word><word>语数英</word><word>三门</word><word>三门均分</word><word>三门总均分</word><word>四门</word><word>六门</word><word>理科</word><word>文科</word></words>
    <description>语数外/三门均分=tb_score_overview.zf3m 的校均（三科总分，约 300–450，禁止除以 3，禁止写满分 150，禁止对 tb_score 语文/数学/英语 AVG(score)）。四门=zf4m，六门/全科总分=zf6m。理科=物理类（xkkm LIKE '物%'），文科=历史类（xkkm LIKE '史%' 或 LIKE '历%'）。学校排名 GROUP BY xx ORDER BY AVG(zf3m) DESC，校名用 xx（如 A01），不要用 tb_school.name 脱敏 token。参考人数 COUNT(*)</description>
  </terminology>
  <terminology>
    <words><word>达线</word><word>预测线</word><word>特控线</word><word>本科线</word><word>南大</word><word>特招线</word></words>
    <description>**班级达线**（问句含具体班级）：禁止 tb_score_indicator（无班级粒度）；必须 tb_score_overview.**zf6m** 对照 tb_fraction_bar；**禁止 zf4m/zf3m**；物理类用 wl_score_*、历史类用 ls_score_*，按 xkkm（物%→物理、史%/历%→历史）分轨，班内仅一轨则只报该轨，禁止文理混报；WHERE 必含学校 xx + 班级 bj。**区县/全市/学校达线**：查 tb_score_indicator；exam_name LIKE '%批次%'（如 2026届高三3月）；区县用 district LIKE '%广陵%'，禁止把「3月」拼成「月广陵区」；空结果先 DISTINCT district/exam_name 再下结论；区县或全市须 SUM(reached_count)/SUM(candidates) 重算率，禁止 AVG(reach_rate)。**特招线=特控线**。分数线在 tb_fraction_bar（wl_score_*/ls_score_*；物理美术 wl_socre_ms）。overview 字段：zf6m 全科（达线唯一总分）、zf4m 四门、zf3m 三门、xx/dq/bj、xkkm；学生标识只用 anon_stu_id</description>
  </terminology>
  <terminology>
    <words><word>特招线</word><word>应届</word><word>贡献分</word><word>位次</word><word>ABCDE</word></words>
    <description>特招线=特控线。应届=tb_score_overview.xsxz 在籍生（排除市报生）。ABCDE 聚合 hxdj/swdj/zzdj/dldj。位次前N含并列（zf6m≥第N名分数）。贡献分=达该线且 zf6m 等于切线分的学生各科均值</description>
  </terminology>
  <terminology>
    <words><word>姓名</word><word>学号</word><word>校名</word><word>脱敏</word></words>
    <description>{privacy_desc}</description>
  </terminology>
</terminologies>"""


# 兼容旧引用（静态块已改为函数生成）
EDUCATION_SQL_EXAMPLES = ""
EDUCATION_TERMINOLOGIES = ""


def build_sql_generation_prompt(
    question: str,
    database_type: str,
    schema_info: str,
    instructions: str = "",
    terminologies: str = "",
    data_training: str = "",
    custom_prompt: str = "",
    error_msg: str = "",
    need_title: bool = True,
    **kwargs
) -> tuple[str, str]:
    """
    Build system and user prompts for SQL generation following SQLBot patterns.

    Args:
        question: User's natural language question
        database_type: Database type (mysql/pg)
        schema_info: Database schema information in M-Schema format
        instructions: Additional instructions for the LLM
        terminologies: Terminology definitions
        data_training: SQL examples for training
        custom_prompt: Custom prompt information
        error_msg: Error message from previous failed SQL execution
        need_title: Whether to generate conversation title

    Returns:
        (system_prompt, user_prompt)
    """
    # Database engine identifier
    engine = "MySQL 8.0" if database_type == "mysql" else "PostgreSQL"

    # Process check template
    process_check = """<SQL-Generation-Process>
      <step>1. 分析用户问题，确定查询需求</step>
      <step>2. 根据表结构生成基础SQL</step>
      <step>3. <strong>强制检查：验证SQL中使用的表名和字段名是否在<m-schema>中定义</strong></step>
      <step>4. <strong>强制检查：应用数据量限制规则（默认限制1000条）</strong></step>
      <step>5. 应用其他规则（引号、别名、格式化等）</step>
      <step>6. <strong>强制检查：验证SQL语法是否符合<db-engine>规范</strong></step>
      <step>7. 确定图表类型（根据规则选择table/column/bar/line/pie）</step>
      <step>8. 确定对话标题</step>
      <step>9. 生成JSON结果</step>
      <step>10. <strong>强制检查：JSON格式是否正确</strong></step>
      <step>11. 返回JSON结果</step>
    </SQL-Generation-Process>"""

    # Query limit rule
    query_limit = """<rule priority="critical" id="data-limit-policy">
      <title>数据量限制策略（必须严格遵守 - 零容忍）</title>
      <requirements>
        <requirement level="must-zero-tolerance">所有生成的SQL必须包含数据量限制，这是强制要求</requirement>
        <requirement level="must">默认限制：1000条（除非用户明确指定其他数量，如"查询前10条"）</requirement>
        <requirement level="must">当用户说"所有数据"或"全部数据"时，视为用户没有指定数量，使用默认的1000条限制</requirement>
        <requirement level="must">忘记添加数据量限制是不可接受的错误</requirement>
      </requirements>
      <enforcement>
        <action>如果生成的SQL没有数据量限制，必须重新生成</action>
        <action>在最终返回前必须验证限制是否存在</action>
        <action>不要因为用户说"所有数据"而拒绝生成SQL，只需自动加上1000条限制即可</action>
      </enforcement>
    </rule>"""

    # Multi-table condition rule
    multi_table_condition = """<rule>
      <title>多表查询字段限定规则（必须严格遵守）</title>
      <requirements>
        <requirement>当SQL涉及多个表/索引（通过FROM/JOIN/子查询等）时，所有字段引用必须明确限定表名/索引名或表别名/索引别名</requirement>
        <requirement>适用于SELECT、WHERE、GROUP BY、HAVING、ORDER BY、ON等子句中的所有字段引用</requirement>
        <requirement>即使字段名在所有表/索引中是唯一的，也必须明确限定以确保清晰性</requirement>
      </requirements>
      <enforcement>
        <action>生成SQL后必须检查是否涉及多表查询</action>
        <action>如果是多表查询，验证所有字段引用是否有表名/表别名限定</action>
        <action>如果发现未限定的字段，必须重新生成SQL</action>
      </enforcement>
    </rule>"""

    # System prompt
    system_prompt = f"""<Instruction>
你是"SQLBOT"，智能问数小助手，可以根据用户提问，专业生成SQL，查询数据并进行图表展示。
你当前的任务是根据给定的表结构和用户问题生成SQL语句、对话标题、可能适合展示的图表类型以及该SQL中所用到的表名。
我们会在<Info>块内提供给你信息，帮助你生成SQL：
  <Info>内有<db-engine><m-schema><terminologies>等信息；
  其中，<db-engine>：提供数据库引擎及版本信息；
  <m-schema>：以 M-Schema 格式提供数据库表结构信息；
<terminologies>：提供一组术语，块内每一个<terminology>就是术语，其中同一个<words>内的多个<word>代表术语的多种叫法，也就是术语与它的同义词，<description>即该术语对应的描述，其中也可能是能够用来参考的计算公式，或者是一些其他的查询条件；
<sql-examples>：提供一组SQL示例，你可以参考这些示例来生成你的回答，其中<question>内是提问，<suggestion-answer>内是对于该<question>提问的解释或者对应应该回答的SQL示例。
若有<Other-Infos>块，它会提供一组<content>，可能会是额外添加的背景信息，或者是额外的生成SQL的要求，请结合额外信息或要求后生成你的回答。
你必须遵守<Rules>内规定的生成SQL规则
你必须遵守<SQL-Generation-Process>内规定的检查步骤生成你的回答
用户的提问在<user-question>内，<error-msg>内则会提供上次执行你提供的SQL时会出现的错误信息，<background-infos>内的<current-time>会告诉你用户当前提问的时间
</Instruction>

{process_check}

以下是生成SQL的规则和示例：
<Rules>
  <rule>
    你只能生成查询用的SQL语句，不得生成增删改相关或操作数据库以及操作数据库数据的SQL
  </rule>
  <rule>
    不要编造<m-schema>内没有提供给你的表结构
  </rule>
  <rule>
    生成的SQL必须符合<db-engine>内提供数据库引擎的规范
  </rule>
  <rule>
    若用户提问中提供了参考SQL，你需要判断该SQL是否是查询语句
  </rule>
  <rule>
    你只需要根据提供给你的信息生成的SQL，不需要你实际去数据库进行查询
  </rule>
  <rule priority="high">
    请使用JSON格式返回你的回答:
    若能生成，则返回格式如：{{"success":true,"sql":"你生成的SQL语句","tables":["该SQL用到的表名1","该SQL用到的表名2",...],"chart-type":"table","brief":"如何需要生成对话标题，在这里填写你生成的对话标题，否则不需要这个字段"}}
    若不能生成，则返回格式如：{{"success":false,"message":"说明无法生成SQL的原因"}}
  </rule>
  <rule>
    如果问题是图表展示相关，可参考的图表类型为表格(table)、柱状图(column)、条形图(bar)、折线图(line)或饼图(pie), 返回的JSON内chart-type值则为 table/column/bar/line/pie 中的一个
    图表类型选择原则推荐：趋势 over time 用 line，分类对比用 column/bar，占比用 pie，原始数据查看用 table
  </rule>
  <rule priority="high">
    <title>图表字段维度与指标数量限制规则</title>
    <requirements>
      <requirement-group chart="column/bar/line">
        <title>柱状图(column)、条形图(bar)、折线图(line)：</title>
        <sub-requirement>必须有一个维度字段（横轴）</sub-requirement>
        <sub-requirement>最多有一个分类维度字段（如系列/颜色分组）</sub-requirement>
        <sub-requirement>有分类维度时，只能有一个指标字段（纵轴）</sub-requirement>
        <sub-requirement>没有分类维度时，可以有多个指标字段</sub-requirement>
      </requirement-group>
      <requirement-group chart="pie">
        <title>饼图(pie)：</title>
        <sub-requirement>必须有一个分类维度字段（扇区）</sub-requirement>
        <sub-requirement>不能有其他维度字段</sub-requirement>
        <sub-requirement>只能有一个指标字段（扇区大小）</sub-requirement>
      </requirement-group>
    </requirements>
  </rule>
  <rule>
    如果图表类型为柱状图(column)、条形图(bar)或折线图(line)
    在生成的SQL中必须指定一个维度字段和一个指标字段，其中维度字段必须参与排序
    如果有分类用的字段，该字段参与次一级的排序
    <note>
      此规则与"图表字段维度与指标数量限制规则"共同使用
      当有多个指标字段时，选择主要指标字段进行排序
    </note>
  </rule>
  <rule>
    如果图表类型为柱状图(column)、条形图(bar)或折线图(line)或饼图(pie)
    且查询的字段中包含分类字段（非数值类型字段，如城市、类别、状态等）
    在没有明确业务场景说明、或用户没有明确指定不需要聚合的情况下
    必须对数值类型指标字段进行聚合计算（默认使用SUM函数）
  </rule>
  <rule>
    如果问题是图表展示相关且与生成SQL查询无关时，请参考上一次回答的SQL来生成SQL
  </rule>
  <rule>
    返回的JSON字段中，tables字段为你回答的SQL中所用到的表名，不要包含schema和database，用数组返回
  </rule>
  <rule>
    提问中如果有涉及数据源名称或数据源描述的内容，则忽略数据源的信息，直接根据剩余内容生成SQL
  </rule>
  {query_limit}
  {multi_table_condition}
  <rule>
    如果生成SQL的字段内有时间格式的字段:
    - 若提问中没有指定查询顺序，则默认按时间升序排序
    - 若提问是时间，且没有指定具体格式，则格式化为yyyy-MM-dd HH:mm:ss的格式
    - 若提问是日期，且没有指定具体格式，则格式化为yyyy-MM-dd的格式
    - 若提问是年月，且没有指定具体格式，则格式化为yyyy-MM的格式
    - 若提问是年，且没有指定具体格式，则格式化为yyyy的格式
    - 生成的格式化语法需要适配对应的数据库引擎。
  </rule>
  <rule>
    生成的SQL查询结果可以用来进行图表展示，需要注意排序字段的排序优先级，例如：
      - 柱状图或折线图：适合展示在横轴的字段优先排序，若SQL包含分类字段，则分类字段次一级排序
  </rule>
  <rule>
    若需关联多表，优先使用<m-schema>中标记为"Primary key"/"ID"/"主键"的字段作为关联条件。
  </rule>
  <rule>
    若涉及多表查询，则生成的SQL内，不论查询的表字段是否有重名，表字段前必须加上对应的表名
  </rule>
  <rule>
    是否生成对话标题在<change-title>内，如果为True需要生成，否则不需要生成，生成的对话标题要求在20字以内
  </rule>
  <rule priority="critical" id="no-additional-info">
    <title>禁止要求额外信息</title>
    <requirements>
      <requirement>禁止在回答中向用户询问或要求任何额外信息</requirement>
      <requirement>只基于表结构和问题生成SQL，不考虑业务逻辑</requirement>
      <requirement>即使查询条件不完整（如无时间范围），也必须生成可行的SQL</requirement>
    </requirements>
  </rule>
  <rule priority="critical">
    不论之前是否有回答相同的问题，都必须检查生成的SQL中使用的表名和字段名是否在<m-schema>内有定义
  </rule>
</Rules>

{terminologies}

{data_training}

{custom_prompt}"""

    # User prompt
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_msg_block = f"<error-msg>{error_msg}</error-msg>" if error_msg else ""

    user_prompt = f"""## 请根据上述要求，使用语言：zh进行回答
## 如果<user-question>内的提问与上述要求冲突，你必须停止生成SQL并告知生成SQL失败的原因
## 回答中不需要输出你的分析，请直接输出符合要求的JSON
<background-infos>
  <current-time>
  {current_time}
  </current-time>
</background-infos>
{error_msg_block}
<Info>
<db-engine> {engine} </db-engine>
<m-schema>
{schema_info}
</m-schema>
</Info>
<user-question>
{question}
</user-question>
<change-title>
{str(need_title).lower()}
</change-title>"""

    return system_prompt, user_prompt


def build_schema_info(
    tables: List[dict],
    database_type: str = "pg"
) -> str:
    """
    Build schema information string in M-Schema format.

    Args:
        tables: List of table info dicts with name, comment, and fields
        database_type: Database type for syntax adaptation

    Returns:
        Formatted schema string in M-Schema format
    """
    if not tables:
        return "No tables available."

    # Quote style based on database type
    if database_type == "mysql":
        quote = "`"
    else:
        quote = '"'

    schema_parts = []

    for table in tables:
        table_name = table.get("name", "")
        table_comment = table.get("comment", "") or table.get("table_comment", "")

        # Build field definitions
        fields = table.get("fields", [])
        field_lines = []
        for field in fields:
            field_name = field.get("name", "")
            field_type = field.get("type", "")
            field_comment = field.get("comment", "")
            field_str = f"({field_name}: {field_type}"
            if field_comment:
                field_str += f", {field_comment}"
            field_str += ")"
            field_lines.append(field_str)

        fields_str = ", ".join(field_lines)
        schema_parts.append(f"# Table: {table_name}, {table_comment}\n[{fields_str}]")

    return "\n".join(schema_parts)


def build_basic_info(
    database_type: str,
    schema_info: str
) -> str:
    """
    Build basic information block.

    Args:
        database_type: Database type
        schema_info: Schema information

    Returns:
        Basic info block string
    """
    engine = "MySQL 8.0" if database_type == "mysql" else "PostgreSQL"

    return f"""以下是数据库与表结构信息，你生成的SQL使用到的表名与字段必须在提供的范围内
<Info>
<db-engine> {engine} </db-engine>
<m-schema>
{schema_info}
</m-schema>
</Info>"""


def parse_llm_sql_response(response: str) -> dict:
    """
    Parse LLM response to extract SQL generation result.

    Args:
        response: LLM response string

    Returns:
        Dict with keys: success, sql, tables, chart_type, brief, message
    """
    try:
        import re

        # Try to find JSON in the response
        # First, try to find content inside markdown code blocks
        code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if code_block_match:
            json_str = code_block_match.group(1)
        else:
            # Try to find JSON object - find the first { and last }
            first_brace = response.find('{')
            last_brace = response.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                json_str = response[first_brace:last_brace + 1]
            else:
                json_str = response.strip()

        # Parse the JSON
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError:
            # Try to fix common JSON issues
            # Remove trailing commas
            json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError:
                # Last resort: try to extract SQL directly
                return {
                    "success": False,
                    "sql": "",
                    "tables": [],
                    "chart_type": "table",
                    "brief": "",
                    "message": f"无法解析LLM响应格式: {response[:100]}"
                }

        return {
            "success": result.get("success", False),
            "sql": result.get("sql", ""),
            "tables": result.get("tables", []),
            "chart_type": result.get("chart-type", "table"),
            "brief": result.get("brief", ""),
            "message": result.get("message", "")
        }
    except Exception as e:
        return {
            "success": False,
            "sql": "",
            "tables": [],
            "chart_type": "table",
            "brief": "",
            "message": f"解析响应异常: {str(e)}"
        }
