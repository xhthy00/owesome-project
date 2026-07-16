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
