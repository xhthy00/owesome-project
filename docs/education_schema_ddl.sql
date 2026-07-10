-- 教育学情外部数据源 DDL 参考（在外部 PostgreSQL 执行，非本仓库 Alembic）
-- 所有新列可空，可重复执行。

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
