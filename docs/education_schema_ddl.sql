-- 教育学情外部数据源 DDL 参考（在外部 PostgreSQL 执行，非本仓库 Alembic）
-- 所有新列可空，可重复执行。

-- 考试批次（一场考试）下挂多科试卷 tb_exam
CREATE TABLE IF NOT EXISTS tb_exam_batch (
    id         BIGSERIAL PRIMARY KEY,
    batch_name VARCHAR(255) NOT NULL,
    exam_time  TIMESTAMP
);

COMMENT ON TABLE tb_exam_batch IS
    '考试批次表；用户口中的「考试/这场考试」对应 batch_name，如 2026届高三11月期中';
COMMENT ON COLUMN tb_exam_batch.id IS '自增主键';
COMMENT ON COLUMN tb_exam_batch.batch_name IS '考试名称，如 2026届高三11月期中';
COMMENT ON COLUMN tb_exam_batch.exam_time IS '考试时间；上场/环比按本列从早到晚取上一场，禁止按 id';

ALTER TABLE tb_exam_batch
    ADD COLUMN IF NOT EXISTS exam_time TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_exam_batch_exam_time ON tb_exam_batch (exam_time);
CREATE UNIQUE INDEX IF NOT EXISTS uk_exam_batch_batch_name ON tb_exam_batch (batch_name);

ALTER TABLE tb_exam
    ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT;

COMMENT ON TABLE tb_exam IS '试卷基础信息表；一场批次下多科试卷，exam_score 为该科卷面满分';
COMMENT ON COLUMN tb_exam.exam_batch_id IS '考试批次ID，关联 tb_exam_batch.id';

CREATE INDEX IF NOT EXISTS idx_exam_batch_id ON tb_exam (exam_batch_id);

ALTER TABLE tb_school
    ADD COLUMN IF NOT EXISTS district VARCHAR(64);

COMMENT ON COLUMN tb_school.district IS '区县维度，用于区域聚合';

ALTER TABLE tb_exam_question
    ADD COLUMN IF NOT EXISTS question_type VARCHAR(32);

ALTER TABLE tb_exam_question
    ADD COLUMN IF NOT EXISTS difficulty DECIMAL(4, 2);

COMMENT ON COLUMN tb_exam_question.question_type IS '题型：选择/填空/解答等';
COMMENT ON COLUMN tb_exam_question.difficulty IS '预设难度，可选；无则运行时由得分率推算';

ALTER TABLE tb_knowledge
    ADD COLUMN IF NOT EXISTS ability_level VARCHAR(32);

COMMENT ON COLUMN tb_knowledge.ability_level IS '能力层级：basic/applied/advanced';

-- 题目 ↔ 知识点多对多（诊断 SQL 只读本表；上线前须先回填再发版）
CREATE TABLE IF NOT EXISTS tb_exam_question_knowledge (
    question_id  BIGINT NOT NULL,
    knowledge_id BIGINT NOT NULL,
    weight       NUMERIC(8, 4) NOT NULL DEFAULT 1,
    PRIMARY KEY (question_id, knowledge_id),
    CONSTRAINT chk_eqk_weight_positive CHECK (weight > 0)
);

COMMENT ON TABLE tb_exam_question_knowledge IS '题目-知识点多对多；weight>0，题内按 SUM(weight) 归一化后拆分得分';
COMMENT ON COLUMN tb_exam_question_knowledge.weight IS '关联权重，题内归一化：w_norm = weight / SUM(weight)';

CREATE INDEX IF NOT EXISTS idx_eqk_knowledge ON tb_exam_question_knowledge (knowledge_id);

-- 从旧 eq.knowledge_id 回填（可重复执行；发版前必须执行）
INSERT INTO tb_exam_question_knowledge (question_id, knowledge_id, weight)
SELECT id, knowledge_id, 1
FROM tb_exam_question
WHERE knowledge_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- 预测线达线预计算指标（问数用长表；区县/全市须 SUM 后重算率，禁止 AVG(reach_rate)）
CREATE TABLE IF NOT EXISTS tb_score_indicator (
    id              BIGSERIAL PRIMARY KEY,
    exam_name       VARCHAR(128) NOT NULL,
    exam_batch_id   BIGINT,
    track           VARCHAR(32)  NOT NULL DEFAULT '',
    district        VARCHAR(64)  NOT NULL DEFAULT '',
    school_id       VARCHAR(128) NOT NULL DEFAULT '',
    school_name     VARCHAR(128) NOT NULL DEFAULT '',
    line_code       VARCHAR(32)  NOT NULL DEFAULT '',
    line_name       VARCHAR(64)  NOT NULL,
    threshold       NUMERIC(8, 2),
    candidates      INTEGER NOT NULL DEFAULT 0,
    reached_count   INTEGER NOT NULL DEFAULT 0,
    reach_rate      NUMERIC(8, 4),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_score_indicator UNIQUE (exam_name, track, district, school_id, line_name)
);

COMMENT ON TABLE tb_score_indicator IS
    '预测线达线预计算：一行=一场考试×选科×学校×线种。达线率=reached_count/candidates×100。区县/全市聚合须 SUM(reached_count)/SUM(candidates) 重算率，禁止 AVG(reach_rate)。无学生/班级粒度。';
COMMENT ON COLUMN tb_score_indicator.exam_name IS '考试名称，与 tb_exam_batch.batch_name / tb_fraction_bar.exam_name 一致';
COMMENT ON COLUMN tb_score_indicator.exam_batch_id IS '考试批次ID，关联 tb_exam_batch.id，与 exam_name 对应';
COMMENT ON COLUMN tb_score_indicator.track IS '选科方向：物理类 / 历史类';
COMMENT ON COLUMN tb_score_indicator.district IS '区县，来自 tb_score_overview.dq';
COMMENT ON COLUMN tb_score_indicator.school_id IS '脱敏校码，与 overview.xx、权限 school_id 对齐';
COMMENT ON COLUMN tb_score_indicator.school_name IS '学校展示名，当前与 school_id 同值';
COMMENT ON COLUMN tb_score_indicator.line_code IS '线种代码：tz特控/bk本科/ty体育/ms美术/yy音乐/211/985/qb清北/nd南大';
COMMENT ON COLUMN tb_score_indicator.line_name IS '线种名称：特控线/本科线/…';
COMMENT ON COLUMN tb_score_indicator.threshold IS '该选科该线预测分数线';
COMMENT ON COLUMN tb_score_indicator.candidates IS '该校该选科参考人数';
COMMENT ON COLUMN tb_score_indicator.reached_count IS '达线人数（总分≥threshold）';
COMMENT ON COLUMN tb_score_indicator.reach_rate IS '学校粒度达线率 0-100；区县/全市禁止对本列求平均';
COMMENT ON COLUMN tb_score_indicator.updated_at IS '最近一次按分数线重算时间';

ALTER TABLE tb_score_indicator
    ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_score_indicator_exam ON tb_score_indicator (exam_name);
CREATE INDEX IF NOT EXISTS idx_score_indicator_batch ON tb_score_indicator (exam_batch_id);
CREATE INDEX IF NOT EXISTS idx_score_indicator_district ON tb_score_indicator (district);
CREATE INDEX IF NOT EXISTS idx_score_indicator_school ON tb_score_indicator (school_id);

-- 预测分数线宽表（一行一场考试批次；物理美术列名为历史拼写 wl_socre_ms）
CREATE TABLE IF NOT EXISTS tb_fraction_bar (
    id            SERIAL PRIMARY KEY,
    exam_batch_id BIGINT,
    exam_name     VARCHAR(128) NOT NULL,
    wl_score_bk   INTEGER,
    wl_score_tz   INTEGER,
    ls_score_bk   INTEGER,
    ls_score_tz   INTEGER,
    wl_score_ty   INTEGER,
    wl_socre_ms   INTEGER,
    wl_score_yy   INTEGER,
    ls_score_ty   INTEGER,
    ls_score_ms   INTEGER,
    ls_score_yy   INTEGER,
    wl_score_211  INTEGER,
    wl_score_qb   INTEGER,
    wl_score_nd   INTEGER,
    wl_score_985  INTEGER,
    ls_score_211  INTEGER,
    ls_score_qb   INTEGER,
    ls_score_nd   INTEGER,
    ls_score_985  INTEGER
);

ALTER TABLE tb_fraction_bar
    ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT;

COMMENT ON TABLE tb_fraction_bar IS
    '预测分数线宽表：一行一场考试批次。wl_score_* 物理类、ls_score_* 历史类。只提供阈值，达线人数/率查 tb_score_indicator';
COMMENT ON COLUMN tb_fraction_bar.exam_batch_id IS '考试批次ID，关联 tb_exam_batch.id';
COMMENT ON COLUMN tb_fraction_bar.exam_name IS '考试批次名称，与 tb_exam_batch.batch_name 一致';
COMMENT ON COLUMN tb_fraction_bar.wl_score_bk IS '物理方向本科线';
COMMENT ON COLUMN tb_fraction_bar.wl_score_tz IS '物理方向特控线';
COMMENT ON COLUMN tb_fraction_bar.ls_score_bk IS '历史方向本科线';
COMMENT ON COLUMN tb_fraction_bar.ls_score_tz IS '历史方向特控线';
COMMENT ON COLUMN tb_fraction_bar.wl_socre_ms IS '物理方向美术类（列名历史拼写 socre）';

CREATE INDEX IF NOT EXISTS idx_fraction_bar_exam ON tb_fraction_bar (exam_name);
CREATE INDEX IF NOT EXISTS idx_fraction_bar_batch ON tb_fraction_bar (exam_batch_id);

-- 学生考试总览宽表已存在于业务库；补批次关联与匿名学号（可重复执行）
ALTER TABLE tb_score_overview
    ADD COLUMN IF NOT EXISTS exam_batch_id BIGINT;

ALTER TABLE tb_score_overview
    ADD COLUMN IF NOT EXISTS anon_stu_id VARCHAR(80);

COMMENT ON TABLE tb_score_overview IS
    '学生考试总览宽表：一行=一学生一场批次。xx 学校、dq 区县、bj 班级、zf6m 全科总分。学生标识用 anon_stu_id；禁止使用 xm/sfzh/ksh';
COMMENT ON COLUMN tb_score_overview.exam_name IS '考试批次名称，与 tb_exam_batch.batch_name 一致';
COMMENT ON COLUMN tb_score_overview.exam_batch_id IS '考试批次ID，关联 tb_exam_batch.id';
COMMENT ON COLUMN tb_score_overview.anon_stu_id IS '学号匿名编码，问数学生标识只用本列';
COMMENT ON COLUMN tb_score_overview.xx IS '学校码（如 A01），学校排名用本列；与权限 school_id 对齐';
COMMENT ON COLUMN tb_score_overview.dq IS '区县';
COMMENT ON COLUMN tb_score_overview.bj IS '班级';
COMMENT ON COLUMN tb_score_overview.zf6m IS '全科总分（达线判定用）';
COMMENT ON COLUMN tb_score_overview.zf4m IS '语数英+首选四门总分';
COMMENT ON COLUMN tb_score_overview.zf3m IS '语数英三门总分；三门均分=AVG(zf3m)，禁止除以 3';
COMMENT ON COLUMN tb_score_overview.xkkm IS '选考科目组合，如 物化生；理科/物理类 LIKE ''物%''';

CREATE INDEX IF NOT EXISTS idx_score_overview_exam ON tb_score_overview (exam_name);
CREATE INDEX IF NOT EXISTS idx_score_overview_batch ON tb_score_overview (exam_batch_id);
CREATE INDEX IF NOT EXISTS idx_score_overview_school ON tb_score_overview (xx);

-- 业务库 tb_score_overview 已有科目/转换/等级/应届列，禁止再 ADD COLUMN。
-- 已有：yw/ywzw/sx/sxkg/yy/yyzw/wl/hx/sw/zz/ls/dl、hxzh/hxdj、swzh/swdj、
--       zzzh/zzdj、dlzh/dldj、xsxz、xxlb。局端分析直接 SELECT 这些列。
COMMENT ON COLUMN tb_score_overview.yw IS '语文原始分';
COMMENT ON COLUMN tb_score_overview.ywzw IS '语文作文分';
COMMENT ON COLUMN tb_score_overview.sx IS '数学原始分';
COMMENT ON COLUMN tb_score_overview.sxkg IS '数学客观分';
COMMENT ON COLUMN tb_score_overview.yy IS '英语原始分';
COMMENT ON COLUMN tb_score_overview.yyzw IS '英语作文分';
COMMENT ON COLUMN tb_score_overview.hxzh IS '化学转换分（等级赋分）';
COMMENT ON COLUMN tb_score_overview.hxdj IS '化学等级 A-E';
COMMENT ON COLUMN tb_score_overview.xsxz IS '学生性质：在籍生=应届口径；市报生排除';
COMMENT ON COLUMN tb_score_overview.xxlb IS '学校分层：引领校/支撑校/发展校/其他校';

-- 再选科目分档（00 表）；有 *dj 列时聚合直接用等级，本表供解释与缺列重算
CREATE TABLE IF NOT EXISTS tb_assign_band (
    id            BIGSERIAL PRIMARY KEY,
    exam_name     VARCHAR(128) NOT NULL,
    exam_batch_id BIGINT,
    subject       VARCHAR(16) NOT NULL,
    grade         VARCHAR(8) NOT NULL,
    raw_hi        NUMERIC(8, 2),
    raw_lo        NUMERIC(8, 2),
    conv_hi       NUMERIC(8, 2),
    conv_lo       NUMERIC(8, 2),
    CONSTRAINT uq_assign_band UNIQUE (exam_name, subject, grade)
);

COMMENT ON TABLE tb_assign_band IS
    '再选科目 ABCDE 分档：一场批次×科目×等级。raw_* 原始分区间，conv_* 转换分区间';

-- 尖子班名单（88/89 表）；不靠班级号启发式
CREATE TABLE IF NOT EXISTS tb_elite_class (
    id            BIGSERIAL PRIMARY KEY,
    exam_name     VARCHAR(128) NOT NULL,
    exam_batch_id BIGINT,
    school_id     VARCHAR(128) NOT NULL,
    class_name    VARCHAR(64) NOT NULL,
    track         VARCHAR(32) NOT NULL DEFAULT '',
    CONSTRAINT uq_elite_class UNIQUE (exam_name, school_id, class_name, track)
);

COMMENT ON TABLE tb_elite_class IS
    '尖子班配置：exam_name×school_id(xx)×class_name(bj)×track(物理类/历史类)';
